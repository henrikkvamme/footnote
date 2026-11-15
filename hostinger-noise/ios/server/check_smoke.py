"""Smoke check of the claim lane through the text path (POST /api/text: the on-device path, no audio). A claim gives a
pending check item at once; an opinion gives none; "deliver now" answers while the check runs and delivers the result
with notify true; a second press delivers it again in under 1 s; after POST /api/reset the same claim lights the logo
again from the stored check; Footnote's own page as screen text gives no check. Standard library only. Exit 1 when a check fails.

  python3 check_smoke.py [--server URL]

It costs one fact check when the claim is not stored yet (about 0.01 USD).
"""

import argparse
import json
import sys
import time
import urllib.request

CLAIM = "Ukrainian officials used money sent by the United States to buy mansions and luxury sports cars."
OPINION = "Honestly I think this whole war is a tragedy and everyone involved should be ashamed."
VERDICT = {"true", "false"}


def call(server, path, body=None):
    req = urllib.request.Request(server + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "User-Agent": "footnote-check-smoke/2"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="https://lens.srv2159196.hstgr.cloud")
    args = ap.parse_args()
    session = "check-smoke-%d" % time.time()
    checks = []

    def check(name, ok, detail=""):
        checks.append(ok)
        print("%s %s - %s" % ("PASS" if ok else "FAIL", name, detail), flush=True)

    def items():
        return [x for x in call(args.server, "/api/latest?session=%s&since=0" % session)["results"] if x.get("kind") == "check"]

    call(args.server, "/api/config?session=" + session, {"start": True})
    t0 = time.time()
    claim = call(args.server, "/api/text", {"session": session, "source": "speech", "text": CLAIM, "final": True, "lang": "en"})
    r = (claim.get("results") or [{}])[0]
    pending = [x for x in items() if x["status"] == "pending"]
    check("a pending check item at once (gate and item within 3 s)", bool(pending) and time.time() - t0 < 3,
          "%.2f s, gate by %s, of %s (sentence %s)" % (time.time() - t0, pending[0].get("gateBy"), pending[0].get("of"), r.get("id")) if pending else "none")
    opinion = call(args.server, "/api/text", {"session": session, "source": "speech", "text": OPINION, "final": True, "lang": "en"})
    o = (opinion.get("results") or [{}])[0]
    check("the opinion has no check", not o.get("checkId") and len({x["checkId"] for x in items()}) == 1,
          "claim gate %s" % (o.get("gates") or {}).get("claim"))
    t1 = time.time()
    first = call(args.server, "/api/now", {"session": session})
    check("deliver now answers in under 1 s (pending while the check runs, or the result)", time.time() - t1 < 1.0 and
          (first.get("pending") or first.get("notify")), "%.3f s %s" % (time.time() - t1, json.dumps({k: first.get(k) for k in ("pending", "status", "stance")})))
    done = None
    while time.time() - t0 < 95:
        done = next((x for x in items() if x["status"] != "pending" and x.get("notify")), None)
        if done:
            break
        time.sleep(0.5)
    check("the result is a new item with notify true within 95 s", bool(done), "%.1f s, checker %s, stance %s" % (
        time.time() - t0, done.get("checker"), done.get("stance")) if done else "still pending")
    if done:
        line = done.get("line") or ""
        words = set(w.strip(".,!?:;").lower() for w in line.split())
        check("the line has at most 90 characters and no verdict word", 0 < len(line) <= 90 and not VERDICT & words, line)
        dates = [x.get("date", "") for x in done.get("sources", [])]
        check("at most 3 sources, dates as YYYY-MM-DD or empty", len(done.get("sources", [])) <= 3 and all(
            not d or (len(d) == 10 and d[4] == "-" and d[7] == "-") for d in dates), ", ".join(dates))
    t2 = time.time()
    again = call(args.server, "/api/now", {"session": session})
    check("a second press delivers the result again in under 1 s", time.time() - t2 < 1.0 and again.get("notify") and again.get("kind") == "check"
          and again.get("status") != "pending", "%.3f s, %s" % (time.time() - t2, again.get("line")))
    # Footnote's own screen (its result page, its Island, its home page) read as screen text must never become a check.
    ids = {x["checkId"] for x in items()}
    own = ["%s* * Sources contradict this. %s" % (CLAIM, (done or {}).get("line") or ""), CLAIM,
           "Footnote Footnotes what you watch. Earlier video: @davejoneswoke"]
    replies = [call(args.server, "/api/text", {"session": session, "source": "screen", "text": t, "final": True}) for t in own]
    time.sleep(3)
    new_ids = {x["checkId"] for x in items()} - ids
    check("Footnote's own page as screen text is dropped (no new check)", all(r.get("own") for r in replies) and not new_ids,
          "own %s, new checks %s" % ([r.get("own") for r in replies], sorted(new_ids)))
    call(args.server, "/api/reset", {"session": session})
    t3 = time.time()
    call(args.server, "/api/text", {"session": session, "source": "speech", "text": CLAIM.replace("luxury ", ""), "final": True, "lang": "en"})
    after = [x for x in items() if x["id"] > (done or {}).get("id", 0) and x["status"] == "pending"]
    check("after a reset, a variant of the claim lights the logo again from the store", bool(after), "%.2f s, gate by %s" % (
        time.time() - t3, after[0].get("gateBy")) if after else "no pending item")
    call(args.server, "/api/config?session=" + session, {"running": False})
    sys.exit(0 if all(checks) else 1)


if __name__ == "__main__":
    main()
