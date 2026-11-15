#!/usr/bin/env bash
# demo.sh <TikTok URL | video file> [max seconds]
#
# Shows Footnote end to end with no iPhone: boots the iPhone 17 Pro simulator, starts the app in follow mode on a
# fresh replay session, plays the same video muted in Safari on the simulator, feeds the server with replay.py in
# real time (the broadcast extension's part), records the simulator screen, and writes an mp4 with the video's
# real sound and the label "simulation: real video, real server, simulated phone".
#
#   DEMO_OUT=dir         where the mp4 goes (default ~/.cache/footnote-bench/demos)
#   DEMO_SKIP_BUILD=1    use the last simulator build
#   DEMO_DEVICE="name"   simulator name (default "iPhone 17 Pro")
#   AGENT_DEVICE=path    drive the simulator with this agent-device command (bound to the device) instead of simctl
set -euo pipefail

SRC=${1:?usage: demo.sh <TikTok URL or video file> [max seconds]}
MAX=${2:-}
HERE=$(cd "$(dirname "$0")" && pwd)
IOS=$(dirname "$HERE")
CACHE="$HOME/.cache/footnote-bench"
OUT=${DEMO_OUT:-$CACHE/demos}
DEVICE_NAME=${DEMO_DEVICE:-iPhone 17 Pro}
SESSION="replay-demo-$(date -u +%Y%m%d-%H%M%S)"
WORK="$CACHE/demo-work/$SESSION"
BUILD="$CACHE/build"
mkdir -p "$OUT" "$WORK"

log() { printf '[demo %s] %s\n' "$(date +%H:%M:%S)" "$*"; }

# UI actions go through agent-device when it is given, else through simctl.
launch_app() {
  if [[ -n "${AGENT_DEVICE:-}" ]]; then
    "$AGENT_DEVICE" open no.henrikkvamme.lens "$@" --launch-args -lensSession --launch-args "$SESSION" --launch-args -lensFollow --launch-args YES >/dev/null
  else
    xcrun simctl launch "$UDID" no.henrikkvamme.lens -lensSession "$SESSION" -lensFollow YES >/dev/null
  fi
}
open_url() {
  if [[ -n "${AGENT_DEVICE:-}" ]]; then "$AGENT_DEVICE" open "$1" >/dev/null; else xcrun simctl openurl "$UDID" "$1"; fi
}
record_start() {
  if [[ -n "${AGENT_DEVICE:-}" ]]; then
    "$AGENT_DEVICE" record start --scope device "$1" >"$WORK/record.log" 2>&1
  else
    xcrun simctl io "$UDID" recordVideo --codec h264 --force "$1" 2>"$WORK/record.log" &
    REC_PID=$!
  fi
}
record_stop() {
  if [[ -n "${AGENT_DEVICE:-}" ]]; then
    "$AGENT_DEVICE" record stop >>"$WORK/record.log" 2>&1 || true
  elif [[ -n "$REC_PID" ]]; then
    kill -INT "$REC_PID" 2>/dev/null; wait "$REC_PID" 2>/dev/null || true
  fi
  REC_PID=""
  RECORDING=0
}

# 1. The media (downloaded once into the bench cache).
MEDIA=$(uv run --quiet "$HERE/replay.py" --print-media "$SRC")
VIDEO=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])[0]["video"])' "$MEDIA")
VID=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])[0]["id"])' "$MEDIA")
log "video $VID: $VIDEO"

# 2. The app for the simulator.
if [[ -z "${DEMO_SKIP_BUILD:-}" || ! -d "$BUILD/Build/Products/Debug-iphonesimulator/Lens.app" ]]; then
  log "building the app"
  xcodebuild -project "$IOS/Lens.xcodeproj" -scheme Lens -destination "platform=iOS Simulator,name=$DEVICE_NAME" \
    -derivedDataPath "$BUILD" -quiet build
fi
APP="$BUILD/Build/Products/Debug-iphonesimulator/Lens.app"

# 3. The simulator.
UDID=$(xcrun simctl list devices available -j | python3 -c '
import json, sys
name = sys.argv[1]
devs = [d for r in json.load(sys.stdin)["devices"].values() for d in r if d["name"] == name]
devs.sort(key=lambda d: d["state"] != "Booted")
print(devs[0]["udid"] if devs else "")' "$DEVICE_NAME")
[[ -n "$UDID" ]] || { echo "no simulator named $DEVICE_NAME" >&2; exit 2; }
xcrun simctl bootstatus "$UDID" -b >/dev/null
[[ -n "${AGENT_DEVICE:-}" ]] || open -g -a Simulator --args -CurrentDeviceUDID "$UDID" || true
xcrun simctl status_bar "$UDID" override --time 9:41 --batteryState charged --batteryLevel 100 --wifiBars 3 --cellularBars 4 || true
xcrun simctl install "$UDID" "$APP"
xcrun simctl terminate "$UDID" no.henrikkvamme.lens 2>/dev/null || true
xcrun simctl terminate "$UDID" com.apple.mobilesafari 2>/dev/null || true
# The first run and the Live Activity prompt must not cover the demo: the app skips its first run in follow mode.

# 4. A small web server for the page that plays the video (Safari needs HTTP range requests for video).
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
cat > "$WORK/index.html" <<'HTML'
<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<style>html,body{margin:0;height:100%;background:#000;overflow:hidden}video{position:fixed;inset:0;width:100%;height:100%;object-fit:contain}</style>
<video id="v" src="/video.mp4" autoplay muted playsinline preload="auto"></video>
<script>
const v = document.getElementById("v");
v.addEventListener("playing", () => fetch("/playing"), { once: true });
v.play().catch(() => {});
</script>
HTML
cat > "$WORK/serve.py" <<'PY'
import http.server, os, sys
root, video, flag = sys.argv[2], sys.argv[3], sys.argv[4]
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith("/playing"):
            open(flag, "w").write("1"); self.send_response(204); self.end_headers(); return
        path = video if self.path.startswith("/video.mp4") else os.path.join(root, "index.html")
        data_len = os.path.getsize(path)
        ctype = "video/mp4" if path == video else "text/html"
        start, end = 0, data_len - 1
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            start = int(a) if a else 0
            end = min(int(b), data_len - 1) if b else data_len - 1
            self.send_response(206); self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, data_len))
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype); self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1)); self.end_headers()
        with open(path, "rb") as f:
            f.seek(start); self.wfile.write(f.read(end - start + 1))
http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PY
python3 -I "$WORK/serve.py" "$PORT" "$WORK" "$VIDEO" "$WORK/playing" &
SERVER_PID=$!
REC_PID=""
RECORDING=0
cleanup() {
  [[ "$RECORDING" == 1 ]] && record_stop
  kill "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT

# 5. Record, start the app on the replay session, then play the video in Safari.
RAW="$WORK/screen.mp4"
record_start "$RAW"
RECORDING=1
REC_START=$(python3 -c 'import time; print(time.time())')
sleep 1
log "app follows session $SESSION"
launch_app --relaunch
sleep 4
open_url "http://127.0.0.1:$PORT/"
for _ in $(seq 1 100); do [[ -f "$WORK/playing" ]] && break; sleep 0.1; done
[[ -f "$WORK/playing" ]] || log "Safari did not report playback; the replay starts anyway"

# 6. The replay, in real time (the broadcast extension's part).
REPLAY_START=$(python3 -c 'import time; print(time.time())')
set +e
uv run --quiet "$HERE/replay.py" "$SRC" --session "$SESSION" --out "$WORK/run" --tail 12 ${MAX:+--max-seconds "$MAX"} | tee "$WORK/replay.log"
REPLAY_EXIT=${PIPESTATUS[0]}
set -e

# 7. Back to the app: the page with the notes.
launch_app || true
sleep 8
record_stop

# 8. The final cut: the screen recording, the video's real sound at the time the replay played it, and the label.
OFFSET=$(python3 -c "print(max(0.0, $REPLAY_START - $REC_START))")
FINAL="$OUT/footnote-demo-$VID.mp4"
FONT=/System/Library/Fonts/HelveticaNeue.ttc
ffmpeg -v error -y -i "$RAW" -itsoffset "$OFFSET" -i "$VIDEO" \
  -filter_complex "[0:v]scale=-2:1920,drawbox=x=0:y=ih-110:w=iw:h=110:color=black@0.6:t=fill,drawtext=fontfile=$FONT:text='simulation\: real video, real server, simulated phone':fontcolor=white:fontsize=34:x=(w-text_w)/2:y=h-72[v]" \
  -map "[v]" -map "1:a?" -c:v libx264 -preset veryfast -crf 22 -pix_fmt yuv420p -c:a aac -b:a 128k -shortest "$FINAL" 2>"$WORK/ffmpeg.log" \
  || ffmpeg -v error -y -i "$RAW" -vf "scale=-2:1920" -c:v libx264 -preset veryfast -crf 22 -pix_fmt yuv420p "$FINAL"
log "replay exit $REPLAY_EXIT, run output $WORK/run"
log "demo video: $FINAL"
exit "$REPLAY_EXIT"
