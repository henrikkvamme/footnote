#!/usr/bin/env python3
"""Silent set and latency budget for Footnote, in one harness.

For each video of set.jsonl: replay.py plays the video, then a filler video (so a held result can be delivered on
"the next video"), against a LOCAL server. Then the harness reads /debug.json of that session and reports:
- alerts (check items with notify true): must be 0 for the silent kinds, 1 for a positive;
- the claims that passed the Jev gate, and why each was not alerted;
- the seconds per stage, from the timestamps the server and replay.py keep.

  python3 run.py [--server http://127.0.0.1:8791] [--raw DIR] [--only NAME ...] [--seconds 60] [--report-only]

The server must be a local copy (never the live one). Standard library only; replay.py runs through uv.
Output: DIR/<session>/ (replay output and debug.json of the session), DIR/report.json, and a table on stdout.
"""

import argparse
import datetime
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPLAY = os.path.join(HERE, "..", "..", "tools", "replay.py")
LIVE = "lens.srv2159196.hstgr.cloud"
# An 18 s cooking clip with no checkable claim: the "next video" after each case.
FILLER = "https://www.tiktok.com/@gordonramsayofficial/video/7702914429884566806"
STAGES = [
    ("spoken_to_transcript", "claim spoken (end of its audio chunk) to transcript"),
    ("transcript_to_gate", "transcript to gate passed (sentence buffer and Jev)"),
    ("gate_to_check", "gate passed to check done (footnote-agent)"),
    ("check_to_next_video", "check done to next video on screen (the person watches; not server time)"),
    ("next_video_to_candidate", "next video on screen to first screen read with the new account (Jev frame gate and a language model read)"),
    ("candidate_to_confirmed", "first read to second read that confirms the new video"),
    ("confirmed_to_delivered", "new video confirmed to delivered (speaker question and the 1 s deliver loop)"),
    ("next_video_to_delivered", "next video on screen to delivered (the delivery delay)"),
    ("spoken_to_delivered", "claim spoken to delivered"),
]


def get(url, timeout=20):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "footnote-eval-silent/1"}), timeout=timeout) as r:
        return json.loads(r.read())


def epoch_of(hms, near):
    """A "HH:MM:SSZ" server time as epoch seconds, on the UTC day of epoch `near`."""
    day = datetime.datetime.fromtimestamp(near, datetime.timezone.utc).date()
    t = datetime.datetime.strptime(hms, "%H:%M:%SZ").time()
    e = datetime.datetime.combine(day, t, datetime.timezone.utc).timestamp()
    return e - 86400 if e - near > 43200 else e + 86400 if near - e > 43200 else e


def pct(values, p):
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 2)


def play(case, args, session, out):
    cmd = ["uv", "run", "--quiet", REPLAY, case["url"], FILLER, "--server", args.server, "--session", session,
           "--max-seconds", str(case.get("seconds", args.seconds)), "--tail", str(args.tail), "--out", out]
    with open(os.path.join(out, "replay.log"), "w") as log:
        code = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=600).returncode
    d = get(args.server + "/debug.json")
    mine = {k: [x for x in v if isinstance(x, dict) and x.get("session") == session] for k, v in d.items()
            if isinstance(v, list)}
    mine["detect_cost"] = d.get("detect_cost")
    mine["replayExit"] = code
    with open(os.path.join(out, "debug.json"), "w") as f:
        json.dump(mine, f)
    return mine


def timeline(out):
    rows = []
    try:
        with open(os.path.join(out, "timeline.jsonl")) as f:
            rows = [json.loads(line) for line in f if line.strip()]
    except OSError:
        pass
    return rows


def analyse(case, session, out):
    """The verdict and the stage times of one run, from its saved debug.json and replay timeline."""
    with open(os.path.join(out, "debug.json")) as f:
        d = json.load(f)
    tl = timeline(out)
    chunk_end = {e["seq"]: e["wall"] for e in tl if e["event"] == "chunk_made"}
    segments = [e for e in tl if e["event"] == "segment"]
    next_video_wall = segments[1]["wall"] if len(segments) > 1 else None
    results = {r["id"]: r for r in d["results"] if r.get("kind") != "check"}
    chunks = {c["name"]: c for c in d["chunks"]}
    items = sorted((r for r in d["results"] if r.get("kind") == "check"), key=lambda r: r["id"])
    reads = sorted(d["screen_reads"], key=lambda s: (s["t"], s["seq"]))
    by_check = {}
    for it in items:
        by_check.setdefault(it["checkId"], []).append(it)
    logged = {}  # "check c2 by agent: found differ: ..." for a held check that has no final item yet
    for e in d.get("logs", []):
        w = e.get("line", "").split()
        if len(w) >= 6 and w[0] == "check" and w[2] == "by" and w[4] == "found":
            logged[w[1]] = {"status": "found", "stance": w[5].rstrip(":"), "checker": w[3].rstrip(":")}
    claims = []
    used = set()
    for cid, its in by_check.items():
        first, last = its[0], its[-1]
        src = results.get(first["of"]) or {}
        if first["of"] in used:
            # A second check on the same result: a caption or on-screen text claim, linked to the newest sentence. Its
            # sentence times are not its own.
            src = {"source": "screen", "text": first.get("claimText")}
        used.add(first["of"])
        chunk = chunks.get(src.get("chunk")) or {}
        gate_ts = first["ts"]
        spoken = chunk_end.get(src.get("seq")) if src.get("source") == "audio" else None
        transcript = src["received"] + (chunk.get("transcribeS") or src.get("latency") or 0) if src.get("received") else None
        checker_ms = last.get("checkerMs") or last.get("ms")
        check_done = gate_ts + checker_ms / 1000 if checker_ms else None
        notified = next((x for x in its if x.get("notify")), None)
        delivered = notified["ts"] if notified else None
        claim_video = first.get("videoId")
        cand = conf = None
        if next_video_wall:
            later = [s for s in reads if s.get("parsed", {}).get("handle") and epoch_of(s["t"], gate_ts) >= next_video_wall]
            c1 = next((s for s in later if s.get("candidate") or s.get("newVideo")), None)
            c2 = next((s for s in later if s.get("newVideo") and (s.get("videoId") or 0) > (claim_video or 0)), None)
            cand = epoch_of(c1["t"], gate_ts) if c1 else None
            conf = epoch_of(c2["t"], gate_ts) if c2 else None
        if last.get("notify"):
            why = "alerted (%s)" % (last.get("deliveredBecause") or "forced")
        elif last.get("notNotified"):
            why = last["notNotified"]
        elif last.get("duplicateOf"):
            why = "duplicate of %s" % last["duplicateOf"]
        elif last["status"] == "pending" and cid in logged:
            last = last | logged[cid]
            why = "%s, held: not delivered within the run" % last["stance"] if last["stance"] == "differ" else "stance %s" % last["stance"]
        elif last["status"] == "pending":
            why = "still pending at the end of the run"
        elif last.get("stance") == "differ":
            why = "differ, held: not delivered within the run"
        else:
            why = "stance %s (only differ alerts)" % last.get("stance")

        def gap(a, b):
            return round(b - a, 2) if a is not None and b is not None and b >= a - 1 else None

        claims.append({
            "check": cid, "claim": last.get("claim"), "sentence": (src.get("text") or first.get("claimText") or "")[:200],
            "source": src.get("source"), "claimP": first.get("claimP"), "gates": src.get("gates"), "gateBy": first.get("gateBy"),
            "status": last["status"], "stance": last.get("stance"), "checker": last.get("checker"), "assertP": last.get("assertP"),
            "sites": [s.get("site") for s in last.get("sources") or []], "line": last.get("line"), "alert": bool(notified), "why": why,
            "videoId": claim_video,
            "stages": {
                "spoken_to_transcript": gap(spoken, transcript),
                "transcript_to_gate": gap(transcript, gate_ts),
                "gate_to_check": round(checker_ms / 1000, 2) if checker_ms else None,
                "check_to_next_video": gap(check_done, next_video_wall) if notified else None,
                "next_video_to_candidate": gap(next_video_wall, cand) if notified else None,
                "candidate_to_confirmed": gap(cand, conf) if notified else None,
                "confirmed_to_delivered": gap(conf, delivered) if notified else None,
                "next_video_to_delivered": gap(next_video_wall, delivered) if notified else None,
                "spoken_to_delivered": gap(spoken, delivered) if notified else None,
            }})
    # Only the case video counts: a claim in the filler video is reported but is not the case's alert.
    alerts = sum(1 for c in claims if c["alert"] and c["videoId"] == 1)
    ok = alerts == 0 if case["expect"] == "silent" else alerts >= 1
    reads_after = [s for s in reads if next_video_wall and epoch_of(s["t"], time.time()) >= next_video_wall]
    return {"name": case["name"], "url": case["url"], "kind": case["kind"], "expect": case["expect"], "why": case["why"],
            "session": session, "alerts": alerts, "ok": ok, "claims": claims, "replayExit": d.get("replayExit"),
            "transcripts": sum(1 for r in results.values() if r.get("speech")),
            "screenReads": len(reads), "readLatency": [s["latency"] for s in reads], "readsAfterSwitch": len(reads_after)}


def report(rows):
    print("\n%-30s %-10s %-6s %6s %s" % ("video", "kind", "expect", "alerts", "verdict"))
    for r in rows:
        print("%-30s %-10s %-6s %6d %s" % (r["name"], r["kind"], r["expect"], r["alerts"], "ok" if r["ok"] else "FAIL"))
        for c in r["claims"]:
            print("    %s v%s p=%s %s | %s | %s" % (c["check"], c["videoId"], c["claimP"], c["stance"], c["why"], (c["claim"] or "")[:70]))
    silent = [r for r in rows if r["expect"] == "silent"]
    pos = [r for r in rows if r["expect"] == "alert"]
    print("\nfalse alerts: %d of %d silent videos; recall: %d of %d positives" % (
        sum(1 for r in silent if not r["ok"]), len(silent), sum(1 for r in pos if r["ok"]), len(pos)))
    table = {}
    print("\n%-26s %4s %7s %7s %7s" % ("stage (s)", "n", "p50", "p95", "max"))
    for key, _ in STAGES:
        vals = [c["stages"][key] for r in rows for c in r["claims"] if c["stages"][key] is not None]
        table[key] = {"n": len(vals), "p50": pct(vals, 50), "p95": pct(vals, 95), "max": max(vals) if vals else None}
        print("%-26s %4d %7s %7s %7s" % (key, len(vals), table[key]["p50"], table[key]["p95"], table[key]["max"]))
    server = [k for k in ("spoken_to_transcript", "transcript_to_gate", "gate_to_check", "next_video_to_candidate",
                          "candidate_to_confirmed", "confirmed_to_delivered") if table[k]["p50"] is not None]
    slowest = max(server, key=lambda k: table[k]["p50"]) if server else None
    print("slowest server stage by p50: %s" % slowest)
    reads = [x for r in rows for x in r["readLatency"]]
    if reads:
        print("language model screen reads: n=%d p50=%.1f s p95=%.1f s" % (len(reads), statistics.median(reads), pct(reads, 95)))
    return table, slowest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8791")
    ap.add_argument("--raw", default=os.path.join(os.environ.get("TMPDIR", "/tmp"), "footnote-eval-silent"))
    ap.add_argument("--set", default=os.path.join(HERE, "set.jsonl"))
    ap.add_argument("--only", action="append")
    ap.add_argument("--seconds", type=float, default=60, help="seconds of each case video (the filler plays 18 s)")
    ap.add_argument("--tail", type=float, default=20)
    ap.add_argument("--tag", default=time.strftime("%H%M%S"), help="part of each session name")
    ap.add_argument("--report-only", action="store_true", help="analyse the runs already in --raw")
    args = ap.parse_args()
    if LIVE in args.server:
        sys.exit("refusing the live server: run a local copy (see README.md)")
    cases = [json.loads(line) for line in open(args.set) if line.strip()]
    if args.only:
        cases = [c for c in cases if c["name"] in args.only]
    os.makedirs(args.raw, exist_ok=True)
    rows = []
    for case in cases:
        if args.report_only:
            found = sorted(x for x in os.listdir(args.raw) if x.endswith(case["name"]) and os.path.isfile(os.path.join(args.raw, x, "debug.json")))
            if not found:
                continue
            session = found[-1]
        else:
            session = "replay-eval-%s-%s" % (args.tag, case["name"])
            out = os.path.join(args.raw, session)
            os.makedirs(out, exist_ok=True)
            t0 = time.time()
            play(case, args, session, out)
            print("played %s in %.0f s" % (case["name"], time.time() - t0), flush=True)
        rows.append(analyse(case, session, os.path.join(args.raw, session)))
    table, slowest = report(rows)
    with open(os.path.join(args.raw, "report.json"), "w") as f:
        json.dump({"rows": rows, "stages": table, "slowest": slowest}, f, indent=1)
    print("report: %s" % os.path.join(args.raw, "report.json"))


if __name__ == "__main__":
    main()
