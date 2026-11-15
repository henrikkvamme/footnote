#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=1.26", "pillow>=10.3"]
# ///
"""Builds the video boundary set: phone screen frames (1 per second) and labelled pairs.

The frames are drawn the way tools/replay.py draws them (its TikTokScreen, settings, chat and bank screens, 540 px wide,
grey, JPEG quality 50). The videos come from replay.py's cache (~/.cache/footnote-bench/media/<id>/); a missing video is
downloaded with yt-dlp by replay.py's `resolve`. Frames go to ~/.cache/footnote-bench/boundary/frames/ (not in git).
The pairs go to pairs.jsonl next to this file (small, in git). Seeded: the same input gives the same set.

  uv run build.py            # rebuild frames and pairs.jsonl
"""

from __future__ import annotations

import importlib.util
import io
import json
import random
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

HERE = Path(__file__).resolve().parent
IOS = HERE.parents[1]
spec = importlib.util.spec_from_file_location("replay", IOS / "tools" / "replay.py")
replay = importlib.util.module_from_spec(spec)
sys.modules["replay"] = replay
spec.loader.exec_module(replay)

OUT = Path.home() / ".cache" / "footnote-bench" / "boundary"
FRAMES = OUT / "frames"
W = 540
H = round(replay.SCREEN_H * W / replay.SCREEN_W)

# The 4 test videos, then videos of other kinds. Pairs of the same account test "same person, another video".
VIDEOS = {
    "7482516372354862367": "test",  # davejoneswoke
    "7386937105588456746": "test",  # dougwilliams165
    "7300621185769868576": "test",  # badas429
    "7387362738974035233": "test",  # leadstories (fact check, text overlay)
    "7677800231316098334": "talking head, account ethanjournal1 (1 of 2)",
    "7708055116145757471": "talking head, account ethanjournal1 (2 of 2)",
    "7664871828820692246": "fast cuts, account warfootage25 (1 of 2)",
    "7658573432547822870": "fast cuts, account warfootage25 (2 of 2)",
    "7697351130438847774": "talking head with text, account vauxvids (1 of 2)",
    "7699954166298397983": "talking head with text, account vauxvids (2 of 2)",
    "7653147555932441888": "fast-cut edit",
    "7312861943881010465": "news report with captions",
}
SAME_ACCOUNT = [("7677800231316098334", "7708055116145757471"), ("7664871828820692246", "7658573432547822870"),
                ("7697351130438847774", "7699954166298397983")]
MAX_SECONDS = 40
URL = "https://www.tiktok.com/@x/video/%s"


def to_jpeg(img: Image.Image) -> bytes:
    small = img.resize((W, H), Image.LANCZOS) if img.width != W else img
    return replay.jpeg(small)


def video_frames(src) -> list[np.ndarray]:
    """1 frame per second, grey, at the phone width (as replay.FrameReader scales them)."""
    w = replay.SCREEN_W
    h = max(2, int(round(src.height * w / src.width / 2)) * 2)
    cmd = ["ffmpeg", "-v", "error", "-i", str(src.video), "-t", str(MAX_SECONDS), "-vf", "fps=1,scale=%d:%d" % (w, h),
           "-pix_fmt", "gray", "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    n = len(raw) // (w * h)
    return [np.frombuffer(raw[i * w * h:(i + 1) * w * h], dtype=np.uint8).reshape(h, w) for i in range(n)]


def save(name: str, data: bytes) -> str:
    (FRAMES / name).write_bytes(data)
    return name


def main():
    rng = random.Random(7)
    FRAMES.mkdir(parents=True, exist_ok=True)
    screens, raw, meta = {}, {}, {}
    for vid, note in VIDEOS.items():
        folder = replay.CACHE / "media" / vid
        src = replay.resolve(str(folder) if (folder / "video.mp4").exists() else URL % vid)
        frames = video_frames(src)
        tk = replay.TikTokScreen(src)
        screens[vid] = [tk.compose(f, "9:41") for f in frames]
        raw[vid] = frames
        meta[vid] = {"handle": src.handle, "note": note, "seconds": len(frames), "screen": tk}
        for i, img in enumerate(screens[vid]):
            save("%s_%02d.jpg" % (vid, i), to_jpeg(img))
        print(vid, src.handle, len(frames), "frames", file=sys.stderr)

    def f(vid, i):
        return "%s_%02d.jpg" % (vid, i)

    pairs = []

    def add(kind, ref, now, same, **more):
        pairs.append({"id": "%s-%03d" % (kind, len([p for p in pairs if p["kind"] == kind])), "kind": kind, "ref": ref, "now": now,
                      "same": same} | more)

    vids = list(VIDEOS)
    # same_far: the reference is an early frame (the language model read it), the frame now is 3 to 30 s later.
    for k in range(40):
        vid = vids[k % len(vids)]
        n = meta[vid]["seconds"]
        r = rng.randrange(0, max(1, n // 3))
        add("same_far", f(vid, r), f(vid, min(n - 1, r + rng.randint(3, 30))), True)
    # same_cut: a hard cut inside one video (a large change of the bare video picture between two seconds).
    cuts = []
    for vid in vids:
        sigs = [replay.signature(x) for x in raw[vid]]
        for i in range(1, len(sigs)):
            d = float(np.abs(sigs[i] - sigs[i - 1]).mean())
            if d > 28:
                cuts.append((d, vid, i))
    cuts.sort(reverse=True)
    per = {}
    for d, vid, i in cuts:
        if per.get(vid, 0) >= 4 or len([p for p in pairs if p["kind"] == "same_cut"]) >= 32:
            continue
        per[vid] = per.get(vid, 0) + 1
        add("same_cut", f(vid, i - 1), f(vid, i), True, cut=round(d, 1))
    # dark and blurred frames of the same video: a dark scene under the sharp app interface, and a blurred whole screen
    # (an app switch or a fade).
    for k in range(10):
        vid = vids[k % len(vids)]
        n = meta[vid]["seconds"]
        i = rng.randrange(n // 2, n)
        dark = meta[vid]["screen"].compose((raw[vid][i] * 0.15).astype(np.uint8), "9:41")
        add("dark_same", f(vid, 1), save("%s_%02d_dark.jpg" % (vid, i), to_jpeg(dark)), True)
        blur = screens[vid][i].filter(ImageFilter.GaussianBlur(14))
        add("blur_same", f(vid, 1), save("%s_%02d_blur.jpg" % (vid, i), to_jpeg(blur)), True)
    # diff_other: another account's video.
    for k in range(30):
        a, b = rng.sample(vids, 2)
        if any({a, b} == set(p) for p in SAME_ACCOUNT):
            b = vids[(vids.index(b) + 1) % len(vids)]
            if b == a:
                b = vids[(vids.index(b) + 1) % len(vids)]
        add("diff_other", f(a, rng.randrange(0, 8)), f(b, rng.randrange(0, meta[b]["seconds"])), False)
    # diff_same_account: the same person (same account name), another video.
    for k in range(30):
        a, b = SAME_ACCOUNT[k % 3] if k % 2 == 0 else SAME_ACCOUNT[k % 3][::-1]
        add("diff_same_account", f(a, rng.randrange(0, 8)), f(b, rng.randrange(0, meta[b]["seconds"])), False)
    # transition: a scroll frame that shows the end of the reference video and the top of the next one. Its truth is
    # "different" (the person scrolls away); at a 35 % shift the old caption is still on screen, at 75 % it is gone.
    for k in range(16):
        a, b = rng.sample(vids, 2)
        i = rng.randrange(5, meta[a]["seconds"])
        shift = int(replay.SCREEN_H * (0.35 if k % 2 == 0 else 0.75))
        img = Image.new("L", (replay.SCREEN_W, replay.SCREEN_H), 0)
        img.paste(screens[a][i].crop((0, shift, replay.SCREEN_W, replay.SCREEN_H)), (0, 0))
        img.paste(screens[b][0].crop((0, 0, replay.SCREEN_W, shift)), (0, replay.SCREEN_H - shift))
        add("transition", f(a, 1), save("tr_%s_%s_%d.jpg" % (a, b, shift), to_jpeg(img)), False, shift=round(shift / replay.SCREEN_H, 2))
    # dark_diff: a dark scene of another video.
    for k in range(6):
        a, b = rng.sample(vids, 2)
        i = rng.randrange(0, meta[b]["seconds"])
        dark = meta[b]["screen"].compose((raw[b][i] * 0.15).astype(np.uint8), "9:41")
        add("dark_diff", f(a, 1), save("%s_%02d_dark.jpg" % (b, i), to_jpeg(dark)), False)
    # nonvideo: another app in front (the server must not call it the same video, and media must be false).
    others = {"settings": replay.settings_screen("9:41"), "chat": replay.chat_screen("9:41"), "bank": replay.bank_screen("9:41")}
    for name, img in others.items():
        save("app_%s.jpg" % name, to_jpeg(img))
    for k in range(12):
        name = list(others)[k % 3]
        vid = vids[k % len(vids)]
        add("nonvideo", f(vid, 2), "app_%s.jpg" % name, False, app=name, private=True)
    (HERE / "pairs.jsonl").write_text("".join(json.dumps(p) + "\n" for p in pairs))
    kinds = {}
    for p in pairs:
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
    print(len(pairs), "pairs", kinds, "same:", sum(p["same"] for p in pairs), file=sys.stderr)


if __name__ == "__main__":
    main()
