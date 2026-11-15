"""Gate eval for Footnote: precision and recall of the Jev contract on set.jsonl.

The contract (server.py maybe_check, then the speaker question at delivery):
    public >= PUBLIC_MIN and claim >= CLAIM_P and quote < CLAIM_QUOTE_MAX   (one Jev call, detect.detect_one)
    and assert >= ASSERT_MIN                                                (one Jev call, server.ASSERT_Q)

The script imports the gate code from server/detect.py and reads ASSERT_Q and the thresholds from server/server.py
(with ast, so the server does not start). Raw Jev scores are cached in cache.jsonl, keyed by the exact request, so
thresholds tune offline with no new calls. A new question wording is a new key and needs new calls.

    python3 run.py                         # score the set with the server thresholds (calls Jev for items not cached)
    python3 run.py --offline               # cache only; an item with no cached score fails the run
    python3 run.py --tune                  # also grid search the thresholds on dev, report dev and test
    python3 run.py --spec alt.json --assert-q alt_assert.json   # a question wording variant (new calls)
    python3 run.py --public 0.5 --claim 0.7 --quote 0.9 --assert-min 0.5   # other thresholds

Jev goes through OpenRouter (backend jevor, typesafe/jev-1.14). The key comes from detect.openrouter_key()
(~/.config/footnote/openrouter.env); it is never printed. --budget stops new calls when the cached cost passes it.
"""

import argparse
import ast
import hashlib
import itertools
import json
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.normpath(os.path.join(HERE, "..", "..", "server"))
CACHE = os.path.join(HERE, "cache.jsonl")


def server_constants(names):
    """Literal top-level assignments of server.py (thresholds and ASSERT_Q), read without importing the server."""
    tree = ast.parse(open(os.path.join(SERVER, "server.py"), encoding="utf-8").read())
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in names:
                v = node.value
                # CLAIM_P = float(os.environ.get("LENS_CLAIM_P", "0.7")): take the default
                if isinstance(v, ast.Call) and getattr(v.func, "id", "") == "float":
                    v = v.args[0].args[1]
                out[name] = ast.literal_eval(v)
    return {k: float(v) if isinstance(v, str) else v for k, v in out.items()}


def load_detect(spec):
    """Imports server/detect.py set up as one backend, no fallback, no hedge: one Jev call per item, measured alone."""
    os.environ["FOOTNOTE_TECHNIQUES"] = os.path.abspath(spec)
    os.environ["FOOTNOTE_BACKEND"] = "jevor"
    os.environ["FOOTNOTE_FALLBACK"] = ""
    os.environ["FOOTNOTE_HEDGE_SECONDS"] = "0"
    os.environ["FOOTNOTE_TECHNIQUES_ON"] = "0"
    sys.path.insert(0, SERVER)
    import detect  # noqa: E402
    return detect


def h(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Cache:
    def __init__(self, path):
        self.path, self.rows, self.lock = path, {}, threading.Lock()
        if os.path.exists(path):
            for line in open(path, encoding="utf-8"):
                if line.strip():
                    r = json.loads(line)
                    self.rows[r["key"]] = r

    def get(self, key):
        return self.rows.get(key)

    def put(self, row):
        with self.lock:
            self.rows[row["key"]] = row
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def spent(self):
        return sum(r.get("cost_usd") or 0 for r in self.rows.values())


def assert_lines(row):
    """The state of the speaker question, in the shape of server.after_context: the claim, then what the video says
    (here: the earlier sentences and the sentence, as no account or caption exists in the set)."""
    said = " ".join(x for x in (row.get("context"), row["text"]) if x)
    return ["The claim: " + row["text"], "What the video says: " + said]


def gate_item(row):
    return {"id": row["id"], "text": row["text"], "source": "speech", "before": row.get("context") or ""}


def score_row(detect, cache, row, assert_q, budget, new_calls):
    """Raw scores of one row: gates (public, claim, quote, war) and assert. Calls Jev for what is not cached."""
    item = gate_item(row)
    out = {}
    gq = detect.asked()
    gkey = h(["gate", detect.JEVOR_MODEL, gq, detect.context_lines(item)])
    akey = h(["assert", detect.JEVOR_MODEL, assert_q, assert_lines(row)])
    for kind, key in (("gate", gkey), ("assert", akey)):
        hit = cache.get(key)
        if hit and not hit.get("error"):
            out[kind] = hit
            continue
        if new_calls is None:
            raise SystemExit("no cached %s score for %s (run without --offline)" % (kind, row["id"]))
        if cache.spent() >= budget:
            raise SystemExit("budget reached: %.4f USD cached cost >= %.4f" % (cache.spent(), budget))
        t0 = time.time()
        if kind == "gate":
            r = detect.detect_one(item, backend="jevor")
            rec = {"key": key, "kind": kind, "id": row["id"], "scores": r.get("gates") or {}, "cost_usd": r.get("cost_usd"),
                   "latency_ms": r.get("latency_ms"), "error": r.get("error")}
        else:
            data, err = detect._post(detect.DECISION_URL, {"model": detect.JEVOR_MODEL, "state": assert_lines(row),
                                                           "questions": {"q": assert_q}}, detect.openrouter_key(), 8)
            a = ((data or {}).get("answers") or {}).get("q") or {}
            p = a.get("noul") if isinstance(a.get("noul"), (int, float)) else None
            rec = {"key": key, "kind": kind, "id": row["id"], "scores": {"assert": p}, "latency_ms": round((time.time() - t0) * 1000),
                   "cost_usd": ((data or {}).get("usage") or {}).get("cost"), "error": err or (None if p is not None else "no answer")}
        new_calls.append(rec)
        cache.put(rec)
        out[kind] = rec
    s = dict(out["gate"]["scores"])
    s["assert"] = out["assert"]["scores"]["assert"]
    return s


def predict(s, t, use_assert=True):
    ok = s.get("public", 0) >= t["public"] and s.get("claim", 0) >= t["claim"] and s.get("quote", 1) < t["quote"]
    return ok and (not use_assert or (s.get("assert") or 0) >= t["assert"])


def counts(rows, preds):
    c = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for r, p in zip(rows, preds):
        gold = r["label"] == "check"
        c[("t" if p == gold else "f") + ("p" if p else "n")] += 1
    return c


def prf(c):
    p = c["tp"] / (c["tp"] + c["fp"]) if c["tp"] + c["fp"] else None
    r = c["tp"] / (c["tp"] + c["fn"]) if c["tp"] + c["fn"] else None
    f = 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
    return p, r, f


def fmt(x):
    return "  -  " if x is None else "%.3f" % x


def report(title, rows, scores, t, use_assert=True, confusion=True):
    preds = [predict(s, t, use_assert) for s in scores]
    print("\n== %s  (public>=%.2f claim>=%.2f quote<%.2f%s)" % (title, t["public"], t["claim"], t["quote"],
                                                                  " assert>=%.2f" % t["assert"] if use_assert else ", no assert"))
    print("%-22s %4s %4s %4s %4s %4s  %6s %6s %6s" % ("category", "n", "tp", "fp", "fn", "tn", "prec", "recall", "f1"))
    for cat in sorted({r["category"] for r in rows}):
        idx = [i for i, r in enumerate(rows) if r["category"] == cat]
        c = counts([rows[i] for i in idx], [preds[i] for i in idx])
        p, rc, f = prf(c)
        print("%-22s %4d %4d %4d %4d %4d  %6s %6s %6s" % (cat, len(idx), c["tp"], c["fp"], c["fn"], c["tn"], fmt(p), fmt(rc), fmt(f)))
    c = counts(rows, preds)
    p, rc, f = prf(c)
    print("%-22s %4d %4d %4d %4d %4d  %6s %6s %6s" % ("TOTAL", len(rows), c["tp"], c["fp"], c["fn"], c["tn"], fmt(p), fmt(rc), fmt(f)))
    if confusion:
        wrong = [(r, s, pr) for r, s, pr in zip(rows, scores, preds) if pr != (r["label"] == "check")]
        if wrong:
            print("-- wrong items (FP = false alert, FN = missed claim)")
        for r, s, pr in sorted(wrong, key=lambda x: (not x[2], x[0]["category"])):
            print("%s %-26s pub %.2f claim %.2f quote %.2f assert %s | %s%s" % (
                "FP" if pr else "FN", r["id"], s.get("public", 0), s.get("claim", 0), s.get("quote", 0),
                fmt(s.get("assert")), ("[ctx: %s] " % r["context"]) if r.get("context") else "", r["text"][:110]))
    return {"precision": p, "recall": rc, "f1": f, **c}


def tune(rows, scores, base, min_precision):
    """Grid search on the given rows (dev only). Objective: the highest precision first, then recall; among configs
    with precision >= min_precision, the highest recall, ties broken by staying close to the server thresholds."""
    grid = {"public": [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9], "claim": [0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95],
            "quote": [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.01], "assert": [0.0, 0.3, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95]}
    best = None
    for vals in itertools.product(*grid.values()):
        t = dict(zip(grid, vals))
        c = counts(rows, [predict(s, t) for s in scores])
        p, r, _ = prf(c)
        p = p or 0.0
        dist = sum(abs(t[k] - base[k]) for k in t)
        key = (p >= min_precision, r if p >= min_precision else p, p, -dist)
        if best is None or key > best[0]:
            best = (key, t, p, r)
    return best[1], best[2], best[3]


def main():
    k = server_constants({"CLAIM_P", "CLAIM_QUOTE_MAX", "PUBLIC_MIN", "ASSERT_MIN", "ASSERT_Q"})
    ap = argparse.ArgumentParser(description="Footnote gate eval")
    ap.add_argument("--set", default=os.path.join(HERE, "set.jsonl"))
    ap.add_argument("--spec", default=os.path.join(SERVER, "techniques.json"), help="techniques.json with the gate questions")
    ap.add_argument("--assert-q", help="JSON file with the speaker question (default: ASSERT_Q of server.py)")
    ap.add_argument("--public", type=float, default=k["PUBLIC_MIN"])
    ap.add_argument("--claim", type=float, default=k["CLAIM_P"])
    ap.add_argument("--quote", type=float, default=k["CLAIM_QUOTE_MAX"])
    ap.add_argument("--assert-min", type=float, default=k["ASSERT_MIN"])
    ap.add_argument("--split", choices=["dev", "test", "all"], default="all")
    ap.add_argument("--offline", action="store_true", help="use cached scores only")
    ap.add_argument("--tune", action="store_true", help="grid search thresholds on dev, then report dev and test")
    ap.add_argument("--min-precision", type=float, default=0.95, help="--tune: the precision floor before recall counts")
    ap.add_argument("--budget", type=float, default=0.15, help="USD: no new call once the cached cost reaches this")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--no-confusion", action="store_true")
    ap.add_argument("--json", help="write the summary as JSON here")
    args = ap.parse_args()

    detect = load_detect(args.spec)
    assert_q = json.load(open(args.assert_q)) if args.assert_q else k["ASSERT_Q"]
    if not args.offline and not detect.openrouter_key():
        raise SystemExit("no OpenRouter key (OPENROUTER_API_KEY or ~/.config/footnote/openrouter.env)")
    rows = [json.loads(l) for l in open(args.set, encoding="utf-8") if l.strip()]
    if args.split != "all":
        rows = [r for r in rows if r["split"] == args.split]
    cache = Cache(CACHE)
    new = None if args.offline else []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        scores = list(pool.map(lambda r: score_row(detect, cache, r, assert_q, args.budget, new), rows))

    t = {"public": args.public, "claim": args.claim, "quote": args.quote, "assert": args.assert_min}
    print("set %s: %d rows; spec %s (%s); assert question %s" % (os.path.basename(args.set), len(rows), os.path.basename(args.spec),
                                                                   detect.SPEC["version"], "file " + args.assert_q if args.assert_q else "server.py ASSERT_Q"))
    summary = {"thresholds": t}
    for split in ("dev", "test"):
        idx = [i for i, r in enumerate(rows) if r["split"] == split]
        if idx:
            sr, ss = [rows[i] for i in idx], [scores[i] for i in idx]
            summary[split + "_gate_only"] = report("%s, gate only" % split, sr, ss, t, False, confusion=False)
            summary[split] = report("%s, gate + speaker question" % split, sr, ss, t, True, confusion=not args.no_confusion)
    if args.tune:
        dev = [i for i, r in enumerate(rows) if r["split"] == "dev"]
        tt, p, r = tune([rows[i] for i in dev], [scores[i] for i in dev], t, args.min_precision)
        print("\n## tuned on dev (precision floor %.2f): %s -> dev precision %s recall %s" % (args.min_precision, tt, fmt(p), fmt(r)))
        summary["tuned"] = {"thresholds": tt}
        for split in ("dev", "test"):
            idx = [i for i, rr in enumerate(rows) if rr["split"] == split]
            summary["tuned"][split] = report("%s, TUNED" % split, [rows[i] for i in idx], [scores[i] for i in idx], tt, True,
                                             confusion=not args.no_confusion)

    lat = sorted(r["latency_ms"] for r in cache.rows.values() if r.get("latency_ms") and not r.get("error"))
    used = [r for r in cache.rows.values() if r["id"] in {x["id"] for x in rows}]
    print("\ncalls this run: %d new (%.5f USD), %d from cache" % (len(new or []), sum(r.get("cost_usd") or 0 for r in new or []),
                                                                   2 * len(rows) - len(new or [])))
    print("cache: %d calls, %.5f USD in total; cost per call %.7f USD" % (len(cache.rows), cache.spent(),
                                                                         cache.spent() / max(1, len(cache.rows))))
    if lat:
        print("latency of all cached calls: p50 %d ms, p95 %d ms (n=%d)" % (statistics.median(lat), lat[min(len(lat) - 1, int(0.95 * len(lat)))], len(lat)))
    summary["cost"] = {"new_calls": len(new or []), "cache_calls": len(cache.rows), "cache_usd": round(cache.spent(), 6),
                       "used_rows": len(used)}
    if args.json:
        json.dump(summary, open(args.json, "w"), indent=1)


if __name__ == "__main__":
    main()
