#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=1.26", "pillow>=10.3"]
# ///
"""Eval of the video boundary decision: "is the screen now the same video as the reference frame?".

It asks Jev through the server's own code: detect.ask_frame with server.SAME_Q (what server.same_video sends), and
server.tiktok_gate for the media and private questions. Raw scores are cached on disk
(~/.cache/footnote-bench/boundary/cache.jsonl), so a second run costs nothing. Build the set first with build.py.

  uv run run.py                         # the current server question at 540 px (the baseline)
  uv run run.py --variants all          # every question and image size below
  uv run run.py --gate                  # also the media and private questions on video and non-video frames
  uv run run.py --budget 0.15           # stop before the summed call cost passes this (USD)

Output: a table per variant and per kind, and results.json next to this file.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import io
import json
import statistics
import sys
import threading
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "server"))
import detect  # noqa: E402
import server  # noqa: E402  (no side effects on import: it serves only in main)

FRAMES = Path.home() / ".cache" / "footnote-bench" / "boundary" / "frames"
CACHE = FRAMES.parent / "cache.jsonl"

# Candidate questions. "server" is the live one, read from server.py, never copied.
QUESTIONS = {
    "server": server.SAME_Q,
    "caption": ("The state has two phone screens: a reference, then the screen now. Is the account name and the caption text "
                "(the white text at the bottom left, above the tab bar) the same in both screens?",
                "Both screens show the same account name and the same caption words. Ignore the video picture, the text burned into "
                "the video, the counts and the time: they change within one video.",
                "The account name or the caption words differ, or the second screen shows no caption: another app, a menu, a "
                "chat, settings, or two videos in the middle of a scroll."),
    "short": ("Same video in both screens (same account name and same caption)?",
              "Same account name and same caption text. The video picture may differ.",
              "Another account or caption, another app, or no video."),
}
SIZES = {"540": 540, "360": 360, "270": 270}


def resize(jpeg: bytes, width: int) -> bytes:
    if width >= 540:
        return jpeg
    img = Image.open(io.BytesIO(jpeg))
    img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=50)
    return buf.getvalue()


# MARK: Local signal: caption.py (the code that proposal.patch puts in server.py)

sys.path.insert(0, str(HERE))
from caption import caption_overlap  # noqa: E402


def caption_iou(ref: bytes, now: bytes) -> float:
    return caption_overlap(now, ref)


# MARK: Cached Jev calls


class Cache:
    def __init__(self, path: Path):
        self.path, self.lock, self.rows = path, threading.Lock(), {}
        if path.exists():
            for line in path.read_text().splitlines():
                r = json.loads(line)
                self.rows[r["key"]] = r

    def get(self, key):
        return self.rows.get(key)

    def put(self, row):
        with self.lock:
            self.rows[row["key"]] = row
            with self.path.open("a") as f:
                f.write(json.dumps(row) + "\n")


class Budget:
    def __init__(self, limit):
        self.limit, self.spent, self.lock = limit, 0.0, threading.Lock()

    def take(self, usd):
        with self.lock:
            self.spent += usd or 0.0

    def over(self):
        return self.spent >= self.limit


def key_of(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else json.dumps(p).encode())
        h.update(b"\0")
    return h.hexdigest()[:32]


def ask_same(cache, budget, q, ref, now):
    key = key_of("same", detect.FRAME_MODEL, list(q), ref, now)
    row = cache.get(key)
    if row and row.get("err") is None:
        return row
    if budget.over():
        return {"key": key, "p": None, "ms": 0, "usd": 0, "err": "budget"}
    p, ms, usd, err = detect.ask_frame(now, *q, timeout=5, ref=ref)
    budget.take(usd)
    row = {"key": key, "p": p, "ms": ms, "usd": usd, "err": err}
    cache.put(row)
    return row


def ask_gate(cache, budget, jpeg):
    key = key_of("gate", detect.FRAME_MODEL, list(server.MEDIA_Q), server.PRIVATE_FRAME_Q, jpeg)
    row = cache.get(key)
    if row and row.get("err") is None:
        return row
    if budget.over():
        return {"key": key, "p": None, "err": "budget"}
    p, ms, usd, err, priv = server.tiktok_gate(jpeg)
    budget.take(usd)
    row = {"key": key, "p": p, "private": priv, "ms": ms, "usd": usd, "err": err}
    cache.put(row)
    return row


# MARK: Metrics


def pct(values, q):
    v = sorted(values)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))] if v else None


def metrics(rows, t):
    """rows: dicts with same (truth) and p (None on an error: the server then reads the frame, so it counts as 'different')."""
    pred = [(r["p"] is not None and r["p"] >= t) for r in rows]
    same = [r["same"] for r in rows]
    n_diff = sum(not s for s in same) or 1
    n_same = sum(same) or 1
    fs = sum(p and not s for p, s in zip(pred, same)) / n_diff
    fd = sum((not p) and s for p, s in zip(pred, same)) / n_same
    acc = sum(p == s for p, s in zip(pred, same)) / max(1, len(rows))
    return {"acc": round(acc, 3), "false_same": round(fs, 3), "false_diff": round(fd, 3)}


def best_threshold(rows):
    """The threshold with the lowest false_same + false_diff (a missed video and an early alert weigh the same)."""
    best = None
    for t in [x / 100 for x in range(5, 96)]:
        m = metrics(rows, t)
        score = m["false_same"] + m["false_diff"]
        if best is None or score < best[0] - 1e-9:
            best = (score, t, m)
    return best[1], best[2]


def per_kind(rows, t):
    kinds = {}
    for r in rows:
        kinds.setdefault(r["kind"], []).append(r)
    out = {}
    for k, rs in kinds.items():
        pred_ok = sum(((r["p"] is not None and r["p"] >= t) == r["same"]) for r in rs)
        out[k] = {"n": len(rs), "truth": "same" if rs[0]["same"] else "different", "acc": round(pred_ok / len(rs), 3)}
    return out


def summary(name, rows, t_fixed=0.5):
    ms = [r["ms"] for r in rows if r.get("ms")]
    usd = [r["usd"] for r in rows if r.get("usd")]
    t, mb = best_threshold(rows)
    return {"variant": name, "n": len(rows), "errors": sum(r["p"] is None for r in rows), "at_0.5": metrics(rows, t_fixed),
            "best_t": t, "at_best": mb, "kinds_0.5": per_kind(rows, t_fixed), "kinds_best": per_kind(rows, t),
            "ms_p50": pct(ms, 0.5), "ms_p95": pct(ms, 0.95), "usd_per_call": round(statistics.mean(usd), 7) if usd else None,
            "usd_total": round(sum(usd), 5)}


def print_summary(s):
    print("\n== %s  n=%d errors=%d  p50 %s ms  p95 %s ms  %s USD/call" % (s["variant"], s["n"], s["errors"], s["ms_p50"], s["ms_p95"], s["usd_per_call"]))
    print("  at 0.5:  acc %(acc).3f  false same %(false_same).3f  false different %(false_diff).3f" % s["at_0.5"])
    print("  best t=%.2f: acc %.3f  false same %.3f  false different %.3f" % (s["best_t"], s["at_best"]["acc"], s["at_best"]["false_same"], s["at_best"]["false_diff"]))
    for k, v in s["kinds_0.5"].items():
        print("    %-18s n=%-3d truth %-9s acc@0.5 %.2f  acc@best %.2f" % (k, v["n"], v["truth"], v["acc"], s["kinds_best"][k]["acc"]))


LOCAL_SAME = 0.85  # caption overlap at or above: the same video, no model call (the highest "different" pair is 0.71)


def proposal(pairs, ious, base):
    """Before: Jev on every frame at 0.5. After: the local caption check first (no call when it says same), then Jev
    at each threshold. Latency is the same-call time (the media call runs in parallel and takes about as long)."""
    def rows_for(local, t):
        rows = []
        for p, iou, b in zip(pairs, ious, base):
            if local and iou >= LOCAL_SAME:
                rows.append(p | {"p": 1.0, "ms": 0, "usd": 0})
            else:
                rows.append(p | {"p": None if b["p"] is None else (1.0 if b["p"] >= t else 0.0), "ms": b["ms"], "usd": b["usd"] or 0})
        return rows
    res = {}
    print("\n== proposal: before (Jev at 0.5) and after (local caption check, then Jev at t)")
    for name, local, t in (("before", False, 0.5), ("after t=0.5", True, 0.5), ("after t=0.3", True, 0.3), ("after t=0.15", True, 0.15)):
        rows = rows_for(local, t)
        m = metrics(rows, 0.5)
        calls = sum(r["ms"] > 0 for r in rows)
        ms = [r["ms"] for r in rows]
        kinds = per_kind(rows, 0.5)
        tr = [r for r in rows if r["kind"] == "transition"]
        for shift in (0.35, 0.75):
            rs = [r for r in tr if r.get("shift") == shift]
            kinds["transition_%d" % round(shift * 100)] = {"n": len(rs), "truth": "different", "acc": round(sum((r["p"] or 0) < 0.5 for r in rs) / max(1, len(rs)), 3)}
        res[name] = m | {"jev_calls": calls, "ms_p50": pct(ms, 0.5), "ms_p95": pct(ms, 0.95), "usd": round(sum(r["usd"] for r in rows), 5), "kinds": kinds}
        print("  %-12s acc %.3f  false same %.3f  false different %.3f  same calls %d/%d  p50 %s ms  p95 %s ms  %.5f USD" % (
            name, m["acc"], m["false_same"], m["false_diff"], calls, len(rows), pct(ms, 0.5), pct(ms, 0.95), res[name]["usd"]))
        print("      " + "  ".join("%s %.2f" % (k, v["acc"]) for k, v in kinds.items()))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="server@540", help="comma list of question@size, or 'all'")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--budget", type=float, default=0.15)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    pairs = [json.loads(x) for x in (HERE / "pairs.jsonl").read_text().splitlines()]
    img = {}
    for p in pairs:
        for f in (p["ref"], p["now"]):
            img.setdefault(f, (FRAMES / f).read_bytes())
    variants = [q + "@" + s for q in QUESTIONS for s in SIZES] if a.variants == "all" else a.variants.split(",")
    cache, budget = Cache(CACHE), Budget(a.budget)
    out = {"variants": [], "local": None, "gate": None}
    pool = concurrent.futures.ThreadPoolExecutor(a.workers)
    scored = {}
    for v in variants:
        qn, sz = v.split("@")
        q, w = QUESTIONS[qn], SIZES[sz]
        jobs = [pool.submit(ask_same, cache, budget, q, resize(img[p["ref"]], w), resize(img[p["now"]], w)) for p in pairs]
        rows = [p | {"p": j.result()["p"], "ms": j.result()["ms"], "usd": j.result()["usd"], "err": j.result()["err"]} for p, j in zip(pairs, jobs)]
        scored[v] = rows
        s = summary(v, rows)
        s["bytes_now_mean"] = round(statistics.mean(len(resize(img[p["now"]], w)) for p in pairs))
        out["variants"].append(s)
        print_summary(s)
        if budget.over():
            print("budget reached: %.4f USD" % budget.spent)
            break
    # The local signal, alone and as a first step before Jev (the first variant).
    ious = [caption_iou(img[p["ref"]], img[p["now"]]) for p in pairs]
    base = scored[variants[0]]
    t_jev = 0.5
    local = {"rules": []}
    for hi in (0.70, 0.80, 0.85, 0.90, 0.95):
        for lo in (None, 0.10, 0.20, 0.30):
            rows, skipped = [], 0
            for p, iou, b in zip(pairs, ious, base):
                if iou >= hi:
                    rows.append(p | {"p": 1.0, "ms": 0})
                    skipped += 1
                elif lo is not None and iou <= lo:
                    rows.append(p | {"p": 0.0, "ms": 0})
                    skipped += 1
                else:
                    rows.append(p | {"p": b["p"], "ms": b["ms"]})
            m = metrics(rows, t_jev)
            ms = [r["ms"] for r in rows]
            local["rules"].append({"hi": hi, "lo": lo, "skipped": skipped, **m, "ms_p50": pct(ms, 0.5), "ms_p95": pct(ms, 0.95),
                                   "kinds": per_kind(rows, t_jev)})
    local["iou_by_kind"] = {}
    for p, iou in zip(pairs, ious):
        local["iou_by_kind"].setdefault(p["kind"], []).append(round(iou, 3))
    out["local"] = local
    print("\n== local caption signal before Jev (%s, Jev threshold 0.5)" % variants[0])
    for k, v in local["iou_by_kind"].items():
        print("    %-18s iou min %.2f  median %.2f  max %.2f" % (k, min(v), statistics.median(v), max(v)))
    for r in local["rules"]:
        print("  same if iou>=%.2f, different if iou<=%s: skips %3d calls  acc %.3f  false same %.3f  false different %.3f  p50 %s p95 %s" % (
            r["hi"], r["lo"], r["skipped"], r["acc"], r["false_same"], r["false_diff"], r["ms_p50"], r["ms_p95"]))
    # Worst failures of the first variant.
    worst = sorted(base, key=lambda r: -abs((r["p"] if r["p"] is not None else 0) - (1.0 if r["same"] else 0.0)))[:8]
    out["worst"] = [{"id": r["id"], "kind": r["kind"], "same": r["same"], "p": r["p"], "ref": r["ref"], "now": r["now"]} for r in worst]
    print("\n== worst (%s)" % variants[0])
    for r in out["worst"]:
        print("   ", r)
    if a.gate:
        frames = sorted({p["now"] for p in pairs if p["kind"] in ("nonvideo", "transition", "dark_diff", "blur_same")} |
                        {p["now"] for p in pairs if p["kind"] == "same_far"}.intersection([p["now"] for p in pairs][:10]))
        jobs = {f: pool.submit(ask_gate, cache, budget, img[f]) for f in frames}
        g = []
        for f, j in jobs.items():
            r = j.result()
            kind = "app" if f.startswith("app_") else ("transition" if f.startswith("tr_") else ("blur" if "_blur" in f else ("dark" if "_dark" in f else "video")))
            g.append({"frame": f, "kind": kind, "media": r.get("p"), "private": r.get("private"), "ms": r.get("ms"), "err": r.get("err")})
        out["gate"] = g
        print("\n== media and private questions (server.tiktok_gate)")
        for kind in ("app", "video", "dark", "blur", "transition"):
            rs = [x for x in g if x["kind"] == kind and x["media"] is not None]
            if rs:
                print("    %-10s n=%-3d media>=0.5 %d  private>=0.5 %d  media mean %.2f  private mean %.2f" % (
                    kind, len(rs), sum(x["media"] >= 0.5 for x in rs), sum((x["private"] or 0) >= 0.5 for x in rs),
                    statistics.mean(x["media"] for x in rs), statistics.mean((x["private"] or 0) for x in rs)))
        for x in g:
            if x["kind"] == "app":
                print("     ", x)
    out["proposal"] = proposal(pairs, ious, base)
    pool.shutdown()
    out["spent_this_run_usd"] = round(budget.spent, 5)
    print("\nspent this run (new calls): %.5f USD" % budget.spent)
    (HERE / "results.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
