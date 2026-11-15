# Footnote without a phone

Two tools test the whole loop (broadcast, server, app) with real videos (TikTok, YouTube, Instagram), news articles and no iPhone.

- `replay.py` plays the broadcast extension's part: it sends the same audio chunks, screen frames, heartbeats and polls as `Broadcast/SampleHandler.swift`, from a video file or a TikTok URL.
- `demo.sh` shows the result in the iOS simulator: the app follows the replay session, the Island updates, and the screen is recorded to an mp4.

Both need `uv`, `ffmpeg` and `yt-dlp`. Media goes to `~/.cache/footnote-bench/media/<id>/` (`video.mp4`, `info.json`), the same cache as `hostinger-noise/bench`. Nothing goes into git.

## What is real and what is simulated

- Real: the video pixels, the audio, the server, the router, the detector, the app.
- Simulated: the app around the video. The server's screen read expects an app UI (handle, caption, counts), so `replay.py` draws the screen at 1206 x 2622 from the video's real metadata. TikTok gets a TikTok-like For You screen: status bar, tabs, right rail with the real counts, the handle and caption, and the tab bar. YouTube, Instagram and other sites get a neutral player with no logos: a short-video layout for a vertical video, a watch page for a wide one. `article:PATH` draws a news article in a browser, one screen each 7 s, with no sound. `--raw-frames` sends the bare video frame instead, for a comparison.
- Simulated: private screens. `--private S` shows a drawn chat and a drawn banking app for S seconds each, while speech plays (the next part of the first video). The server must pause and keep nothing: the checks want zero transcripts and zero notes from them.
- Simulated: the phone. The demo video says so in its label: "simulation: real video, real server, simulated phone".

## Commands

```sh
cd hostinger-noise/ios/tools
uv run replay.py --smoke                                   # 22 s of a known video, 8 s of a chat and 8 s of a bank app, checks; exit 1 when broken
uv run replay.py https://www.tiktok.com/@user/video/123    # one video in real time
uv run replay.py URL1 URL2 URL3 --visit-other 5            # a feed: videos one after another, 5 s in another app between them
uv run replay.py --feed list.txt --speed 4 --max-seconds 30  # a bulk run (delays are then not honest)
uv run replay.py https://www.youtube.com/shorts/ID article:~/.cache/footnote-bench/other_apps/web/web-stopfake-zelensky-trips
uv run replay.py VIDEO --text-path --private 8              # the on-device text path: the reference transcript goes to POST /api/text
./demo.sh https://www.tiktok.com/@user/video/123 40        # the simulator demo, first 40 s of the video
```

The CLI flags and the output files (`timeline.jsonl`, `notes.jsonl`, `summary.json`) are documented at the top of `replay.py`. Keep them stable: the bench calls them.

The app follows a session with the launch arguments `-lensSession NAME -lensFollow YES`: it skips the first run and starts listening without the broadcast sheet. Debug has a Session field for a lasting change.

## Where the replay differs from the real extension

- Screen: the app chrome is drawn, not captured. There is no comment sheet, no swipe between videos (a feed cuts), no ads and no LIVE.
- Frames: the video is decoded at 4 frames per second, so a frame can be up to 0.25 s old. The phone gets the screen at 50 or more frames per second. The grey image comes from ffmpeg's luma, not from the Y plane of ReplayKit's buffer. Scaling is Pillow Lanczos, not vImage. The JPEG is Pillow at quality 50, not ImageIO at 0.5, so the sizes differ a little.
- Frame processing runs in line, so no frame is skipped because the previous one is still being encoded.
- Audio: ffmpeg decodes the file to 44.1 kHz stereo float and the replay averages the channels. The phone gets TikTok's output in whatever format ReplayKit gives (often 44.1 or 48 kHz Int16 big-endian). The resampler, the quiet-point cut, the silence threshold and mu-law are the same code, ported.
- Network: Python's urllib with a new HTTP/1.1 connection per request, from the Mac's network. The extension uses URLSession with one HTTP/2 connection per host over the phone's Wi-Fi or mobile network.
- No Live Activity from the extension (update path A). In `demo.sh` the app in the simulator updates the Island (path B). APNs push does not reach a simulator.
- The text path (`--text-path`) does not run a recognizer: it sends the bench's reference transcript (`units/<id>.json`, or `other_apps/units/`), a partial text in the middle of each sentence and the final text `--text-lag` seconds (default 0.8) after the sentence ends. The real lag of the phone's recognizer is not measured yet.
- Only the JPEG screen path. The on-device OCR paths (Vision) are not ported: the replay sends JPEGs even when the server config asks for OCR.
- Heartbeat memory fields are 0, and `"replay": true` is added.
- No Darwin notifications: the app learns about the broadcast from the heartbeat only.
- Another app is a drawn iOS Settings screen with silence. On a phone, the app switcher animates and TikTok's sound stops.
- Start and stop: the replay posts `/api/config` with `start` and, at the end, `running: false`, which the app does on a phone. `--no-start` leaves the start to the app.
