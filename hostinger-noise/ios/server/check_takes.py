"""Takes check of the demo flow on the live server: play the claim video, then another video, press "deliver now".
Each take starts a run (as the phone does at each broadcast) and must give a pending check item soon after the claim, a press
answer in under 1 s that is the main claim (notify true, stance "differ", at least 2 sources, `of` set), and no notify of
the other video's checks. Takes run in one session (with POST /api/reset between them, as
the app's Debug button does) and in new sessions. Standard library; it runs ../tools/replay.py (uv) for the media.

  python3 check_takes.py [--same 5] [--new 3] [--video URL --claim-end S] [--other URL] [--server URL]

Exit 1 when a take fails. It prints one line per take and a JSON summary.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPLAY = os.path.join(HERE, "..", "tools", "replay.py")
MAIN = "https://www.tiktok.com/@davejoneswoke/video/7482516372354862367"  # "The mansion is worth $20 million." ends at 10.9 s
CONTROL = "https://www.tiktok.com/@leadstories/video/7387362738974035233"  # debunks a Bugatti claim: must stay silent


def call(server, path, body=None):
    req = urllib.request.Request(server + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "User-Agent": "footnote-check-takes/1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def take(args, session, start, n):
    """One take. Returns a dict with pass/fail and the times."""
    since = max([r["id"] for r in call(args.server, "/api/latest?session=%s&since=0" % session)["results"]] or [0])
    seen, stop = {}, threading.Event()

    def poll():
        while not stop.is_set():
            try:
                for r in call(args.server, "/api/latest?session=%s&since=%d" % (session, since))["results"]:
                    seen.setdefault(r["id"], (time.time(), r))
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.25)
    threading.Thread(target=poll, daemon=True).start()
    out = os.path.join(os.path.expanduser("~/.cache/footnote-bench/runs"), "%s-take%d" % (session, n))
    cmd = ["uv", "run", REPLAY, args.video, args.other, "--session", session, "--server", args.server, "--out", out,
           "--max-seconds", str(args.max_seconds), "--tail", "8"]  # each take starts a run, as the phone does
    proc = subprocess.run(cmd, capture_output=True, text=True)
    t_press = time.time()
    press = call(args.server, "/api/now", {"session": session})
    press_s = time.time() - t_press
    time.sleep(1.5)
    stop.set()
    # The wall time when the first video started, from the replay timeline.
    v0 = None
    try:
        for line in open(os.path.join(out, "timeline.jsonl")):
            e = json.loads(line)
            if e.get("video") == 0 and "tv" in e and "wall" in e:
                v0 = e["wall"] - e["tv"]
                break
    except OSError:
        pass
    checks = sorted(((t, r) for t, r in seen.values() if r.get("kind") == "check"), key=lambda x: x[1]["id"])
    pending = [(t, r) for t, r in checks if r["status"] == "pending"]
    delivered = [(t, r) for t, r in checks if r.get("notify") and r["status"] != "pending"]
    claim_end = (v0 + args.claim_end) if v0 else None
    res = {
        "take": n, "session": session, "replayExit": proc.returncode,
        "pendingAfterClaimS": round(pending[0][0] - claim_end, 2) if pending and claim_end else None,
        "pendingGateBy": pending[0][1].get("gateBy") if pending else None,
        "deliveredAfterClaimS": round(delivered[0][0] - claim_end, 2) if delivered and claim_end else None,
        "deliveredBecause": delivered[0][1].get("deliveredBecause") if delivered else None,
        "checker": delivered[0][1].get("checker") if delivered else None,
        "pressS": round(press_s, 3), "pressKind": press.get("kind"), "pressStance": press.get("stance"),
        "pressNotify": press.get("notify"), "pressSources": len(press.get("sources") or []), "pressLine": press.get("line"),
        "pressPending": press.get("pending"), "checkItems": len(checks),
    }
    words = [w.lower() for w in args.claim_words.split(",") if w]
    said = " ".join(str(press.get(k) or "") for k in ("claim", "claimText")).lower()
    res["pressIsMainClaim"] = any(w in said for w in words)
    res["pressOf"] = press.get("of")
    # The other video (the control debunks a claim) must stay silent: no item with notify true that a press did not force.
    res["otherNotified"] = [r["checkId"] for _, r in delivered if not r.get("forced") and not any(w in (r.get("claim", "") + r.get("claimText", "")).lower() for w in words)]
    res["autoNotified"] = len([1 for _, r in delivered if not r.get("forced")])
    res["pass"] = bool(proc.returncode == 0 and pending and res["pendingAfterClaimS"] is not None and res["pendingAfterClaimS"] <= args.pending_max
                       and press.get("kind") == "check" and press.get("notify") and press.get("stance") == "differ" and res["pressIsMainClaim"]
                       and press.get("of") is not None and not res["otherNotified"] and len(press.get("sources") or []) >= 2 and press_s < 1.0)
    if proc.returncode not in (0, 1):
        res["replayError"] = (proc.stderr or proc.stdout)[-300:]
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="https://lens.srv2159196.hstgr.cloud")
    ap.add_argument("--video", default=MAIN)
    ap.add_argument("--claim-end", type=float, default=10.9, help="seconds into the video where the claim sentence ends")
    ap.add_argument("--other", default=CONTROL)
    ap.add_argument("--claim-words", default="mansion,house,ponte vedra", help="words of the main claim; the press must return it")
    ap.add_argument("--max-seconds", type=float, default=20)
    ap.add_argument("--pending-max", type=float, default=3.0)
    ap.add_argument("--same", type=int, default=5)
    ap.add_argument("--new", type=int, default=3)
    args = ap.parse_args()
    results = []
    session = "replay-takes-%d" % time.time()
    for i in range(args.same):
        if i:
            call(args.server, "/api/reset", {"session": session})
        results.append(take(args, session, start=(i == 0), n=len(results) + 1))
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    for _ in range(args.new):
        results.append(take(args, "replay-takes-%d" % time.time(), start=True, n=len(results) + 1))
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    passed = sum(r["pass"] for r in results)
    print(json.dumps({"passed": passed, "of": len(results)}))
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
