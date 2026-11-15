#!/usr/bin/env python3
"""End to end test of Footnote in the iOS simulator against the hosted server, with real TikTok videos.

Each case plays a real TikTok video in the simulator's browser (TikTok web), then a second video. replay.py sends the
simulator's real screen (1 frame per second) and the video's sound to the hosted server, as the broadcast extension does
on a phone. The Footnote app runs in the simulator with its Live Activity. The server pushes the Dynamic Island state
through APNs. The test reads the server (/debug.json) and the app's own heartbeat, so a PASS means the app showed it.

  python3 e2e_sim.py --device "<agent-device command>" --udid <simulator UDID> [--case NAME ...] [--out DIR]

Before the run: the Footnote app runs in the simulator with `-lensSession sim-e2e -lensFollow YES`.
The fast set takes about 1 minute per case. `--full` plays each first video to its end. Standard library only.
Exit 1 when a check fails.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.request

MAIN = "https://www.tiktok.com/@davejoneswoke/video/7482516372354862367"
SECOND = "https://www.tiktok.com/@dougwilliams165/video/7386937105588456746"
THIRD = "https://www.tiktok.com/@badas429/video/7300621185769868576"
CONTROL = "https://www.tiktok.com/@leadstories/video/7387362738974035233"
# name, first video, seconds of it in the fast set, what must happen
CASES = [
    ("main", MAIN, 22, "notify"),  # the claim is in the first 11 s: result when the check is done, then its link opens the claim
    ("press", MAIN, 30, "press"),  # "Footnote this" during the claim's video: the result at once
    ("third", THIRD, 40, "notify"),
    ("control", CONTROL, 45, "silent"),  # a debunk: nothing may reach the Island
]
APP = "no.henrikkvamme.lens"
HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", required=True, help="the agent-device command bound to the simulator")
    ap.add_argument("--udid", required=True)
    ap.add_argument("--server", default="https://lens.srv2159196.hstgr.cloud")
    ap.add_argument("--session", default="sim-e2e")
    ap.add_argument("--case", action="append", choices=[c[0] for c in CASES])
    ap.add_argument("--full", action="store_true", help="play each first video to its end")
    ap.add_argument("--out", default="/tmp/footnote-e2e")
    args = ap.parse_args()
    device = shlex.split(args.device)
    failed = []
    base = [0, 0]  # the newest result id before the case (older items are not of this case), and the newest id seen

    def ad(*a, timeout=40):
        try:
            return subprocess.run(device + list(a), capture_output=True, text=True, timeout=timeout).stdout
        except subprocess.TimeoutExpired:
            return ""

    def call(path, body=None):
        req = urllib.request.Request(args.server + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", "User-Agent": "footnote-e2e-sim/1"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    def look():
        """The server's and the app's view of the session now."""
        d = call("/debug.json")
        beats = [h for h in d["heartbeats"] if h.get("session") == args.session and h.get("source") == "app"]
        app = max(beats, key=lambda h: h.get("ts", 0), default={})
        items = [r for r in d["results"] if r.get("session") == args.session and r.get("kind") == "check" and r["id"] > base[0]]
        base[1] = max([base[1]] + [r["id"] for r in d["results"]])
        push = d["push"]["sessions"].get(args.session, {})
        return {"island": (d.get("island", {}).get(args.session) or {}).get("mode"), "push": push.get("lastMode"), "pushOk": push.get("ok"),
                "appMode": app.get("activityMode"), "owner": app.get("islandOwner"), "noteId": app.get("activityNoteId"),
                "phase": app.get("scenePhase"), "appAge": time.time() - app.get("ts", 0), "items": items}

    def open_video(url):
        ad("open", url)
        time.sleep(4)
        for line in ad("snapshot", "-i").splitlines():  # TikTok web's "open the app" sheet
            if '"Not now"' in line and "@e" in line:
                ad("press", "@e" + line.split("@e", 1)[1].split()[0].strip('",]'))
                break

    def run(name, first, seconds, expect):
        out = os.path.join(args.out, name)
        os.makedirs(out, exist_ok=True)
        log = []

        def check(what, ok, detail=""):
            print("%s [%s] %s - %s" % ("PASS" if ok else "FAIL", name, what, detail), flush=True)
            if not ok:
                failed.append("%s: %s" % (name, what))

        call("/api/reset", {"session": args.session})
        look()
        base[0] = base[1]
        call("/api/config", {"session": args.session, "running": True})
        ad("record", "start", "--scope", "device", os.path.join(out, "run.mp4"))
        open_video(first)
        cmd = ["uv", "run", "--quiet", os.path.join(HERE, "replay.py"), first, SECOND, "--sim", args.udid, "--session", args.session,
               "--server", args.server, "--no-start", "--tail", "14", "--out", os.path.join(out, "replay")]
        if not args.full:
            cmd += ["--max-seconds", str(seconds)]
        replay = subprocess.Popen(cmd, stdout=open(os.path.join(out, "replay.log"), "w"), stderr=subprocess.STDOUT)
        t0 = time.time()
        switched = pressed = tapped = None
        seen = {"checking": None, "appChecking": None, "checked": None, "appChecked": None, "early": False, "owner": set(), "note": None}
        while replay.poll() is None:
            time.sleep(1.5)
            try:
                s = look()
            except Exception as e:  # noqa: BLE001
                log.append("look failed: %r" % (e,))
                continue
            t = round(time.time() - t0, 1)
            log.append("%5.1f island=%s push=%s app=%s owner=%s note=%s phase=%s" % (t, s["island"], s["push"], s["appMode"], s["owner"], s["noteId"], s["phase"]))
            if s["owner"]:
                seen["owner"].add(s["owner"])
            if s["island"] == "checking" and seen["checking"] is None:
                seen["checking"] = t
            if s["appMode"] == "checking" and seen["appChecking"] is None:
                seen["appChecking"] = t
            notified = [r for r in s["items"] if r.get("notify") and r.get("status") != "pending"]
            if notified and seen["checked"] is None:
                seen["checked"], seen["item"] = t, notified[-1]
                seen["early"] = switched is None and pressed is None
            if s["appMode"] == "checked" and seen["appChecked"] is None:
                seen["appChecked"], seen["note"] = t, s["noteId"]
            if expect == "press" and pressed is None and seen["appChecking"] is not None and t < seconds - 8:
                ad("open", APP, "footnote://now")  # what the Action button's intent does
                pressed = t
            if switched is None and not args.full and t >= seconds:
                if pressed is not None:
                    ad("open", first)  # the press opened Footnote: back to the browser
                open_video(SECOND)
                switched = round(time.time() - t0, 1)
            if expect == "notify" and seen["appChecked"] is not None and tapped is None:
                ad("screenshot", os.path.join(out, "island.png"))
                # The Island is system UI and the device tool cannot tap it: open the link that the Island's result carries.
                ad("open", APP, "footnote://check/%s" % s["noteId"])
                tapped = t
                time.sleep(3)
                page = ad("snapshot", "-i")
                open(os.path.join(out, "page.txt"), "w").write(page)
                ad("screenshot", os.path.join(out, "page.png"))
                item = seen.get("item") or {}
                sites = [x.get("site", "") for x in item.get("sources", [])]
                seen["page"] = any(x and x.split(".")[0].lower() in page.lower() for x in sites) or "contradict" in page.lower()
        ad("record", "stop")
        open(os.path.join(out, "states.log"), "w").write("\n".join(log) + "\n")
        final = look()
        item = seen.get("item") or {}
        check("the server pushed the Island states (APNs ok)", final["pushOk"] is True, "last push: %s" % final["push"])
        if expect == "silent":
            # The video after it asserts a false claim, and its result may show: only the first video must stay silent.
            late = switched is not None and (seen["appChecked"] is None or seen["appChecked"] >= switched)
            check("no result reached the Island during the video", not seen["early"] and (seen["appChecked"] is None or late),
                  "first result at %s s (app %s s), next video at %s s" % (seen["checked"], seen["appChecked"], switched))
            why = sorted({r.get("notNotified") or r.get("stance") or r.get("status") for r in final["items"]} - {None})
            print("     [%s] checks of the video: %s" % (name, why))
            return
        check("the asterisk lit on the server", seen["checking"] is not None, "%s s after the start" % seen["checking"])
        check("the app showed the lit asterisk", seen["appChecking"] is not None, "%s s, owner %s" % (seen["appChecking"], sorted(seen["owner"])))
        check("a contradicted result with 2 source domains", item.get("stance") == "differ" and len({x.get("site") for x in item.get("sources", [])}) >= 2,
              "%s, %s, by %s, %s" % (item.get("stance"), [x.get("site") for x in item.get("sources", [])], item.get("checker"), (item.get("claim") or "")[:60]))
        check("the app showed the result in the Island", seen["appChecked"] is not None, "%s s, note %s" % (seen["appChecked"], seen["note"]))
        if expect == "notify":
            # A result shows when its check is done; it does not wait for the next video.
            check("the result came when the check was done", seen["checked"] is not None,
                  "result at %s s, next video at %s s (%s)" % (seen["checked"], switched, item.get("deliveredBecause")))
            check("the link of the Island result (footnote://check/<id>) opens the claim with its sources", bool(seen.get("page")), "see %s" % os.path.join(out, "page.png"))
        else:
            check("the press delivered the result during the claim's video", pressed is not None and seen["checked"] is not None
                  and seen["checked"] < (switched or 1e9), "press at %s s, result at %s s, forced %s" % (pressed, seen["checked"], item.get("forced")))

    for name, first, seconds, expect in CASES:
        if not args.case or name in args.case:
            run(name, first, seconds, expect)
    print("%d failed%s" % (len(failed), ": " + "; ".join(failed) if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
