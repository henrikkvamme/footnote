"""Warms the check store for the demo claims after a deploy, so the phone never waits for the checker: it sends each
claim's sentences through the text path in a throwaway session, presses "deliver now" until the result is there, and
resets the session. A claim that is stored already answers at once and costs nothing. Standard library only.

  python3 warm_cache.py [--server URL]

Exit 1 when a claim gives no finished check within 90 s.
"""

import argparse
import json
import sys
import time
import urllib.request

DEMO = [  # @davejoneswoke 7482516372354862367, the main demo video
    ["This is the house of Ukrainian President Zelenskyy.", "The mansion is worth $20 million.", "Located in Ponte Vedra Beach, Florida."],
    # @dougwilliams165 7386937105588456746, the second demo video (Cartier)
    ["Ukrainian first lady Olena Zelenska spent $1.1 million on Cartier jewelry during a visit to New York."],
]


def call(server, path, body=None):
    req = urllib.request.Request(server + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "User-Agent": "footnote-warm/1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="https://lens.srv2159196.hstgr.cloud")
    args = ap.parse_args()
    ok = True
    for sentences in DEMO:
        session = "warm-%d" % time.time()
        call(args.server, "/api/config?session=" + session, {"start": True})
        t0 = time.time()
        for s in sentences:
            call(args.server, "/api/text", {"session": session, "source": "speech", "text": s, "final": True, "lang": "en"})
        out = {}
        while time.time() - t0 < 90:
            out = call(args.server, "/api/now", {"session": session})
            if out.get("kind") == "check" and out.get("status") != "pending":
                break
            time.sleep(2)
        done = out.get("kind") == "check" and out.get("status") != "pending"
        ok = ok and done
        print(json.dumps({"claim": out.get("claim"), "stance": out.get("stance"), "checker": out.get("checker"), "sources": len(out.get("sources") or []),
                          "line": out.get("line"), "s": round(time.time() - t0, 1)} if done else {"claim": sentences[0], "error": out}, ensure_ascii=False))
        call(args.server, "/api/reset", {"session": session})
        call(args.server, "/api/config?session=" + session, {"running": False})
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
