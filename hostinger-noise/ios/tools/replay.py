#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=1.26", "pillow>=10.3"]
# ///
"""Device replay: plays the broadcast extension's part from a real video (TikTok, YouTube, Instagram) or a news
article, with no iPhone.

It sends what `Broadcast/SampleHandler.swift` sends: 16 kHz mono mu-law audio chunks cut at a quiet point,
grey 540 px JPEG screen frames (at most `fps` per second, unchanged screens skipped), heartbeats each 2 s and
a poll of /api/latest each second. The server cannot tell it from a phone, except that the session name
starts with "replay-" and the heartbeat says "replay": true.

The video pixels and the audio are real. The app around the video (handle, caption, counts, tab bar) is a
simulation drawn from the video's real metadata, because the server's screen read expects an app UI. TikTok gets
its For You screen; YouTube, Instagram and other sites get a neutral player (no logos) with the app's name.

CLI (stable: hostinger-noise/bench calls it)
  uv run replay.py SOURCE [SOURCE ...]     play one video, or several one after another (a feed)
      SOURCE: a TikTok, YouTube or Instagram URL (downloaded with yt-dlp into ~/.cache/footnote-bench/media/<id>/),
      a local video file, a directory with video.mp4 and info.json (the bench cache layout), or
      article:PATH, a news article shown page by page with no sound (PATH: a .txt file, or a directory with
      gloss.json from the bench's other_apps/web cache)
  --feed FILE         read more SOURCEs from FILE, one per line (# starts a comment)
  --visit-other S     between two videos, show another app (iOS Settings) for S seconds: exercises the pause guard
  --session NAME      server session (default replay-<UTC timestamp>); "lens-demo" (the phone) is refused
  --speed N           play N times faster than real time (default 1: real time, so the delays are honest)
  --max-seconds S     play only the first S seconds of each video
  --raw-frames        send the bare video frame (scaled to 540 px wide) instead of the simulated phone screen
  --sim UDID          send the real screen of a booted iOS simulator as each frame (open the video there in a browser);
                      the audio still comes from the video file
  --chunk-seconds S   chunk length (default 4, like the extension; the server config can change it, as on the phone)
  --fps F             screen frames per second (default 1; the server config can change it)
  --server URL        default https://lens.srv2159196.hstgr.cloud
  --out DIR           default ~/.cache/footnote-bench/runs/<session>/
  --keep-running      do not stop the run at the end (no "finished" heartbeat, no config running=false)
  --no-start          do not start a new run on the server (the app in the simulator already did)
  --smoke             a short check of the loop: 22 s of a known video, then a chat and a banking app (8 s each, with
                      speech) that must pause the session with nothing kept; exit 1 when the loop is broken
  --print-media       resolve the SOURCEs (download when needed), print their paths and metadata as JSON, exit
  --visit-after S     show another app (iOS Settings) for S seconds after the last video
  --save-frames       also write each sent frame to --out, to look at the simulated screen
  --private S         after the last video, show a drawn chat for S seconds and then a drawn banking app for S seconds,
                      with speech playing (the next part of the first video). The server must pause: the checks want
                      zero transcripts and zero notes from these screens (--smoke uses 8)
  --app NAME          the app of every video source (tiktok, youtube, instagram, web), when the metadata does not say
  --text-path         the on-device text path: instead of audio chunks, send POST /api/text with the reference
                      transcript (~/.cache/footnote-bench/units/<id>.json), a partial text in the middle of each
                      sentence and the final text --text-lag seconds after its end, like the phone's recognizer
  --text-lag S        seconds from the end of a sentence to its final on-device text (default 0.8)
  Exit codes: 0 all checks passed, 1 a check failed (the message says which), 2 bad input or media.

Output in --out
  timeline.jsonl  one event per line: {"event", "wall" (epoch s), "t" (content s since the start), "video" (index),
                  "tv" (s in that video), ...}. Events: start, chunk_made, chunk_skipped_silent, chunk_sent,
                  chunk_failed, frame_taken, frame_same, frame_sent, frame_failed, screen_read, transcript,
                  note, state, segment, stop, error.
  notes.jsonl     one row per note (a result with a technique) or unsure result:
                  video_id, url, sentence, source ("speech" or "screen"), technique, label, probs, confidence,
                  unsure, t_video_start, t_video_end, t_note_wall, delay_s, t_from, result_id, seq, text, backend,
                  detect_ms. Times in the video are seconds. t_note_wall is epoch seconds when the poll saw the
                  note. delay_s = t_note_wall minus the wall time when the video reached t_video_end.
                  t_from "units": t_video_start/end come from the bench's sentence units
                  (~/.cache/footnote-bench/units/<id>.json); "chunk": they are the audio chunk's span, so the real
                  end of the sentence can be earlier and delay_s is then a lower bound. With --speed N the
                  delays are wall seconds of a compressed playback: use real time for delays.
                  app: the app of the video ("tiktok", "youtube", "instagram", "web"); t_from "text": the times come
                  from the reference transcript of the text path.
  summary.json    counts (chunks, frames, transcripts, screen reads, notes, errors, retries), delay median and
                  p95, router credits for the session, the detector backends seen, the checks and their result.
"""

from __future__ import annotations

import argparse
import difflib
import glob
import io
import json
import math
import os
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SERVER = "https://lens.srv2159196.hstgr.cloud"
CACHE = Path.home() / ".cache" / "footnote-bench"
# The yt-dlp in nixpkgs (2026.06) fails on single TikTok videos; the bench pins 2026.10.26 with curl-cffi too.
YTDLP = shlex.split(os.environ.get("YTDLP", "uvx -q --with curl-cffi yt-dlp@2026.10.26"))
SMOKE_URL = "https://www.tiktok.com/@sonyaturova120/video/7704085270265744672"
SCREEN_W, SCREEN_H = 1206, 2622  # iPhone 17 Pro screen in pixels, what ReplayKit gives
TAB_BAR_H = 249
RATE = 16_000
IN_RATE = 44_100  # ReplayKit app audio arrives at 44.1 or 48 kHz
BUFFER = 1024  # samples per ReplayKit audio buffer
SILENCE_RMS = 0.0003
DECODE_FPS = 4  # frames decoded per second of video; the sampler picks the newest one
ARTICLE_PAGE_SECONDS = 7  # a reader scrolls one screen of a news article each 7 s


# MARK: Small helpers


def now() -> float:
    return time.time()


def iso(t: float) -> str:
    return time.strftime("%H:%M:%S", time.gmtime(t)) + ".%03dZ" % int((t % 1) * 1000)


def http(method: str, url: str, body: bytes | None = None, ctype: str | None = None, timeout: float = 8) -> tuple[int, bytes, str]:
    """One request on a new connection. Returns (status, body, error)."""
    req = urllib.request.Request(url, data=body, method=method, headers={"User-Agent": "footnote-replay/1"} | ({"Content-Type": ctype} if ctype else {}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), ""
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b"", "HTTP %d" % e.code
    except Exception as e:  # noqa: BLE001
        return 0, b"", repr(e)


def post_json(server: str, path: str, data: dict, timeout: float = 15) -> tuple[int, dict]:
    code, body, _ = http("POST", server + path, json.dumps(data).encode(), "application/json", timeout)
    try:
        return code, json.loads(body or b"{}")
    except json.JSONDecodeError:
        return code, {}


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 2)


def norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", str(text or "").lower()).split())


def sentence_with(text: str, mark: str | None) -> str:
    """The sentence of `text` that holds `mark`, or the whole text."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", text or "") if p.strip()]
    if mark:
        for p in parts:
            if mark.lower() in p.lower():
                return p
    return (text or "").strip()


# MARK: Media


@dataclass
class Source:
    ref: str
    video: Path
    info: dict
    id: str
    url: str
    duration: float
    width: int
    height: int
    units: list = field(default_factory=list)
    kind: str = "video"  # "video" or "article"
    pages: list = field(default_factory=list)  # an article's pages, each a list of paragraphs
    app_override: str | None = None

    @property
    def app(self) -> str:
        """The app the server should name: tiktok, youtube, instagram or web."""
        if self.kind == "article":
            return "web"
        if self.app_override:
            return self.app_override
        key = str(self.info.get("extractor_key") or self.info.get("extractor") or "").lower()
        for name in ("tiktok", "youtube", "instagram"):
            if name in key or name in self.url:
                return name
        return "tiktok"  # the bench's local files are TikTok videos

    @property
    def handle(self) -> str:
        # TikTok's For You page shows the display name (the phone read "Arjay McCandless", not a @name).
        return str(self.info.get("channel") or self.info.get("creator") or self.info.get("uploader") or self.video.parent.name)

    @property
    def username(self) -> str:
        return str(self.info.get("uploader") or self.handle)


def probe(path: Path) -> tuple[float, int, int]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    s = d["streams"][0]
    return float(d["format"]["duration"]), int(s["width"]), int(s["height"])


def media_id(url: str) -> str:
    """The id of a TikTok, YouTube or Instagram URL, the folder name in the media cache."""
    for pattern in (r"/video/(\d+)", r"[?&]v=([\w-]{11})", r"/shorts/([\w-]{11})", r"youtu\.be/([\w-]{11})", r"/(?:reel|reels|p)/([\w-]+)"):
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return re.sub(r"\W+", "_", url)[-40:]


def download(url: str) -> Path:
    vid = media_id(url)
    folder = CACHE / "media" / vid
    if (folder / "video.mp4").exists() and (folder / "info.json").exists():
        return folder
    folder.mkdir(parents=True, exist_ok=True)
    print("downloading %s with yt-dlp" % url, file=sys.stderr, flush=True)
    r = subprocess.run([*YTDLP, "--quiet", "--no-warnings", "--no-playlist", "-f", "b[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b[height<=1080]/b", "--merge-output-format", "mp4", "--write-info-json",
                        "-o", str(folder / "video.%(ext)s"), url], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("yt-dlp failed for %s: %s" % (url, r.stderr.strip()[-400:]))
    for f in glob.glob(str(folder / "*.info.json")):
        os.replace(f, folder / "info.json")
    if not (folder / "video.mp4").exists():
        found = [p for p in folder.glob("video.*") if p.suffix != ".json"]
        if not found:
            raise SystemExit("yt-dlp wrote no video for %s" % url)
        os.replace(found[0], folder / "video.mp4")
    return folder


def article(ref: str) -> Source:
    """article:PATH, a news article as a reader sees it in a browser: a .txt file (the first line is the headline,
    blank lines split paragraphs) or a bench web directory with gloss.json (one sentence per entry)."""
    path = Path(ref.split(":", 1)[1]).expanduser()
    if path.is_dir() and (path / "gloss.json").exists():
        sentences = [str(x.get("text") or x.get("text_en") or "").strip() for x in json.loads((path / "gloss.json").read_text()).get("sentences", [])]
        sentences = [x for x in sentences if x]
        headline, paragraphs = (sentences[0] if sentences else path.name), [" ".join(sentences[i:i + 2]) for i in range(1, len(sentences), 2)]
    elif path.is_file():
        blocks = [" ".join(b.split()) for b in re.split(r"\n\s*\n", path.read_text()) if b.strip()]
        headline, paragraphs = (blocks[0] if blocks else path.stem), blocks[1:]
    else:
        raise SystemExit("no article at %s (a .txt file or a directory with gloss.json)" % path)
    # The headline, a picture and 2 paragraphs fit on the first screen, 3 paragraphs on each next one; the reader
    # scrolls one screen each ARTICLE_PAGE_SECONDS.
    pages = [[headline] + paragraphs[:2]] + [paragraphs[i:i + 3] for i in range(2, len(paragraphs), 3)]
    name = path.name if path.is_dir() else path.stem
    return Source(ref, path, {"title": headline, "channel": "news article"}, "article-" + re.sub(r"\W+", "-", name)[:40], "", ARTICLE_PAGE_SECONDS * len(pages),
                  SCREEN_W, SCREEN_H, kind="article", pages=pages)


def resolve(ref: str) -> Source:
    if ref.startswith("article:"):
        return article(ref)
    if ref.startswith("http://") or ref.startswith("https://"):
        path = download(ref)
    else:
        path = Path(ref).expanduser()
        if not path.exists():
            raise SystemExit("no such file: %s" % ref)
    if path.is_dir():
        video, info_path = path / "video.mp4", path / "info.json"
    else:
        video = path
        info_path = next((p for p in (path.with_suffix(".info.json"), path.parent / "info.json") if p.exists()), path.parent / "info.json")
    info = json.loads(info_path.read_text()) if info_path.exists() else {}
    vid = str(info.get("id") or (video.parent.name if video.name == "video.mp4" else video.stem))
    url = str(info.get("webpage_url") or (ref if ref.startswith("http") else ""))
    duration, width, height = probe(video)
    units_path = next((p for p in (CACHE / "units" / ("%s.json" % vid), CACHE / "other_apps" / "units" / ("%s.json" % vid)) if p.exists()),
                      CACHE / "units" / ("%s.json" % vid))
    units = []
    if units_path.exists():
        try:
            units = [u for u in json.loads(units_path.read_text()).get("units", []) if u.get("source") == "speech"]
        except (json.JSONDecodeError, AttributeError):
            units = []
    return Source(ref, video, info, vid, url, duration, width, height, units)


def decode_audio(path: Path, seconds: float | None, start: float = 0) -> np.ndarray:
    """The app audio as ReplayKit would give it: 44.1 kHz float, two channels summed to mono like AudioDownmixer.mono."""
    cmd = ["ffmpeg", "-v", "error"] + (["-ss", "%.3f" % start] if start > 0 else []) + ["-i", str(path)] + (["-t", str(seconds)] if seconds else []) + ["-vn", "-ac", "2", "-ar", str(IN_RATE), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=False).stdout
    if not raw:
        return np.zeros(int((seconds or 1) * IN_RATE), dtype=np.float32)  # a video with no sound track
    st = np.frombuffer(raw, dtype=np.float32).reshape(-1, 2)
    return st.mean(axis=1).astype(np.float32)


class FrameReader:
    """Decodes a video at DECODE_FPS to grey frames scaled to the phone width, read in order."""

    def __init__(self, path: Path, width: int, height: int, seconds: float | None):
        self.w = SCREEN_W
        self.h = max(2, int(round(height * SCREEN_W / width / 2)) * 2)
        cmd = ["ffmpeg", "-v", "error", "-i", str(path)] + (["-t", str(seconds)] if seconds else []) + [
            "-vf", "fps=%d,scale=%d:%d" % (DECODE_FPS, self.w, self.h), "-pix_fmt", "gray", "-f", "rawvideo", "-"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.index = -1
        self.frame: np.ndarray | None = None

    def at(self, t: float) -> np.ndarray | None:
        want = int(t * DECODE_FPS)
        size = self.w * self.h
        while self.index < want:
            buf = self.proc.stdout.read(size)
            if len(buf) < size:
                break
            self.index += 1
            self.frame = np.frombuffer(buf, dtype=np.uint8).reshape(self.h, self.w)
        return self.frame

    def close(self):
        self.proc.kill()
        self.proc.wait()


# MARK: The simulated phone screen


def font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    index = {"regular": 0, "bold": 1, "medium": 10}[weight]
    for path in ("/System/Library/Fonts/HelveticaNeue.ttc",):
        if os.path.exists(path):
            return ImageFont.truetype(path, size, index=index)
    return ImageFont.load_default(size)


def count_text(n) -> str:
    """A count as TikTok shows it: 1025, 10.2K, 43.4M."""
    if n is None:
        return "0"
    n = int(n)
    if n < 10_000:
        return str(n)
    if n < 1_000_000:
        return ("%.1fK" % (n / 1000)).replace(".0K", "K")
    return ("%.1fM" % (n / 1_000_000)).replace(".0M", "M")


def strip_emoji(text: str) -> str:
    return "".join(c for c in text if ord(c) < 0x2600 or 0x0400 <= ord(c) <= 0x04FF)


def split_caption(info: dict) -> tuple[str, list[str]]:
    desc = str(info.get("description") or info.get("title") or "")
    tags = re.findall(r"#(\w+)", desc)
    text = re.sub(r"\s*#\w+", "", desc).strip()
    return strip_emoji(text), tags


def wrap(draw: ImageDraw.ImageDraw, text: str, f, width: int, lines: int) -> list[str]:
    out, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=f) <= width:
            cur = trial
            continue
        out.append(cur)
        cur = word
        if len(out) == lines:
            break
    if len(out) < lines and cur:
        out.append(cur)
    if len(out) == lines and (" ".join(out) != text.strip()):
        last = out[-1]
        while last and draw.textlength(last + "... more", font=f) > width:
            last = last[:-1]
        out[-1] = last.rstrip() + "... more"
    return out


class TikTokScreen:
    """Draws the TikTok For You screen around a video frame. All chrome is drawn once per video."""

    def __init__(self, src: Source):
        self.overlay = Image.new("RGBA", (SCREEN_W, SCREEN_H), (0, 0, 0, 0))
        d = ImageDraw.Draw(self.overlay)
        white, dim = (255, 255, 255, 255), (255, 255, 255, 170)
        shadow = (0, 0, 0, 110)

        def text(xy, s, f, fill=white, anchor="la"):
            d.text((xy[0] + 2, xy[1] + 3), s, font=f, fill=shadow, anchor=anchor)
            d.text(xy, s, font=f, fill=fill, anchor=anchor)

        # Top tabs.
        text((SCREEN_W / 2 - 40, 200), "Following", font(46, "medium"), dim, "rm")
        text((SCREEN_W / 2 + 40, 200), "For You", font(48, "bold"), white, "lm")
        fy = d.textlength("For You", font=font(48, "bold"))
        d.rounded_rectangle((SCREEN_W / 2 + 40 + fy / 2 - 34, 236, SCREEN_W / 2 + 40 + fy / 2 + 34, 243), 3, fill=white)
        text((60, 200), "LIVE", font(34, "bold"), white, "lm")
        d.ellipse((1100, 172, 1148, 220), outline=white, width=7)
        d.line((1140, 212, 1166, 238), fill=white, width=8)

        # Right rail.
        x = 1110
        d.ellipse((x - 64, 1280, x + 64, 1408), fill=(120, 120, 120, 255), outline=white, width=5)
        text((x, 1344), (src.handle[:1] or "?").upper(), font(64, "bold"), white, "mm")
        d.ellipse((x - 24, 1386, x + 24, 1434), fill=(254, 44, 85, 255))
        text((x, 1409), "+", font(44, "bold"), white, "mm")
        # Heart.
        hy = 1540
        d.ellipse((x - 46, hy - 40, x + 2, hy + 8), fill=white)
        d.ellipse((x - 2, hy - 40, x + 46, hy + 8), fill=white)
        d.polygon([(x - 44, hy - 6), (x + 44, hy - 6), (x, hy + 42)], fill=white)
        text((x, hy + 90), count_text(src.info.get("like_count")), font(34, "bold"), white, "mm")
        # Comment bubble.
        cy = 1750
        d.ellipse((x - 48, cy - 40, x + 48, cy + 36), fill=white)
        d.polygon([(x + 10, cy + 26), (x + 40, cy + 54), (x + 34, cy + 16)], fill=white)
        for dx in (-22, 0, 22):
            d.ellipse((x + dx - 6, cy - 6, x + dx + 6, cy + 6), fill=(40, 40, 40, 255))
        text((x, cy + 90), count_text(src.info.get("comment_count")), font(34, "bold"), white, "mm")
        # Bookmark.
        by = 1950
        d.polygon([(x - 34, by - 44), (x + 34, by - 44), (x + 34, by + 44), (x, by + 18), (x - 34, by + 44)], fill=white)
        text((x, by + 90), count_text(src.info.get("save_count") or src.info.get("favorite_count") or 0), font(34, "bold"), white, "mm")
        # Share arrow.
        sy = 2130
        d.polygon([(x + 46, sy - 6), (x + 2, sy - 46), (x + 2, sy - 24), (x - 44, sy - 10), (x - 44, sy + 40), (x + 2, sy + 14), (x + 2, sy + 34)], fill=white)
        text((x, sy + 88), count_text(src.info.get("repost_count") or src.info.get("share_count")), font(34, "bold"), white, "mm")
        # Sound disc.
        d.ellipse((x - 46, 2286, x + 46, 2378), fill=(30, 30, 30, 255), outline=(90, 90, 90, 255), width=12)

        # Handle, caption and sound at the bottom left.
        caption, tags = split_caption(src.info)
        f_cap = font(40)
        lines = wrap(d, (caption + " " + " ".join("#" + t for t in tags)).strip(), f_cap, 900, 2)
        y = 2290 - 56 * len(lines)
        text((36, y - 66), strip_emoji(src.handle), font(46, "bold"))
        for i, line in enumerate(lines):
            text((36, y + 56 * i), line, f_cap)
        music = strip_emoji(str(src.info.get("track") or "original sound")) + " - " + strip_emoji(str(src.info.get("artist") or src.handle))
        d.ellipse((36, 2318, 56, 2338), fill=white)
        d.line((54, 2328, 54, 2290), fill=white, width=5)
        d.line((54, 2290, 66, 2298), fill=white, width=5)
        text((80, 2310), music[:44], font(36))

        # Tab bar (opaque black).
        top = SCREEN_H - TAB_BAR_H
        d.rectangle((0, top, SCREEN_W, SCREEN_H), fill=(0, 0, 0, 255))
        labels = [("Home", 120, True), ("Friends", 360, False), ("", 603, False), ("Inbox", 846, False), ("Profile", 1086, False)]
        for name, cx, active in labels:
            col = white if active else (160, 160, 160, 255)
            if not name:
                d.rounded_rectangle((cx - 66, top + 30, cx - 2, top + 116), 22, fill=(32, 211, 234, 255))
                d.rounded_rectangle((cx + 2, top + 30, cx + 66, top + 116), 22, fill=(254, 44, 85, 255))
                d.rounded_rectangle((cx - 58, top + 30, cx + 58, top + 116), 22, fill=white)
                d.text((cx, top + 73), "+", font=font(64, "bold"), fill=(0, 0, 0, 255), anchor="mm")
                continue
            d.rounded_rectangle((cx - 30, top + 34, cx + 30, top + 88), 10, outline=col, width=6, fill=col if active else None)
            d.text((cx, top + 128), name, font=font(30, "medium"), fill=col, anchor="mm")
        d.rounded_rectangle((SCREEN_W / 2 - 200, SCREEN_H - 32, SCREEN_W / 2 + 200, SCREEN_H - 18), 7, fill=white)

        grey = self.overlay.convert("LA")
        self.ink, self.mask = grey.getchannel("L"), grey.getchannel("A")

    def compose(self, frame: np.ndarray | None, clock: str) -> Image.Image:
        screen = Image.new("L", (SCREEN_W, SCREEN_H), 0)
        if frame is not None:
            video = Image.fromarray(frame, "L")
            top = max(0, (SCREEN_H - TAB_BAR_H - video.height) // 2)
            screen.paste(video, (0, top))
        screen.paste(self.ink, (0, 0), self.mask)
        status_bar(screen, clock, dark=False)
        return screen


def status_bar(img: Image.Image, clock: str, dark: bool):
    d = ImageDraw.Draw(img)
    ink = 0 if dark else 255
    d.text((170, 66), clock, font=font(50, "bold"), fill=ink, anchor="mm")
    for i in range(4):
        d.rectangle((936 + i * 18, 82 - 12 * i - 12, 948 + i * 18, 84), fill=ink)
    d.rounded_rectangle((1040, 48, 1120, 86), 10, outline=ink, width=4)
    d.rectangle((1048, 56, 1100, 78), fill=ink)
    d.rectangle((1124, 60, 1130, 74), fill=ink)


def settings_screen(clock: str) -> Image.Image:
    """Another app in front: iOS Settings. The server must pause and discard this frame."""
    img = Image.new("L", (SCREEN_W, SCREEN_H), 242)
    d = ImageDraw.Draw(img)
    status_bar(img, clock, dark=True)
    d.text((48, 260), "Settings", font=font(104, "bold"), fill=0, anchor="ls")
    d.rounded_rectangle((48, 310, SCREEN_W - 48, 420), 30, fill=226)
    d.text((110, 365), "Search", font=font(50), fill=140, anchor="lm")
    rows = [["Airplane Mode", "Wi-Fi", "Bluetooth", "Mobile Service", "Personal Hotspot"],
            ["Notifications", "Sounds & Haptics", "Focus", "Screen Time"],
            ["General", "Control Centre", "Display & Brightness", "Wallpaper"]]
    y = 480
    for group in rows:
        d.rounded_rectangle((48, y, SCREEN_W - 48, y + 150 * len(group)), 36, fill=255)
        for i, name in enumerate(group):
            ry = y + 150 * i
            d.rounded_rectangle((84, ry + 36, 162, ry + 114), 18, fill=(60 + 37 * (i + len(name))) % 200)
            d.text((200, ry + 75), name, font=font(52), fill=0, anchor="lm")
            d.text((SCREEN_W - 96, ry + 75), ">", font=font(52), fill=180, anchor="rm")
            if i < len(group) - 1:
                d.line((200, ry + 149, SCREEN_W - 48, ry + 149), fill=225, width=2)
        y += 150 * len(group) + 70
    return img


APP_NAMES = {"youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok", "web": "Browser"}


class MediaScreen:
    """A neutral player around a video for apps other than TikTok: no logos, the app's name, the channel, the title
    and the counts. A vertical video gets a short-video layout (Shorts, Reels); a wide one a watch page."""

    def __init__(self, src: Source):
        self.src = src
        self.vertical = src.height > src.width
        app = APP_NAMES.get(src.app, src.app.title())
        caption, tags = split_caption(src.info)
        title = strip_emoji(str(src.info.get("title") or caption or ""))
        channel = strip_emoji(src.handle)
        if self.vertical:
            self.overlay = Image.new("RGBA", (SCREEN_W, SCREEN_H), (0, 0, 0, 0))
            d = ImageDraw.Draw(self.overlay)
            white, dim, shadow = (255, 255, 255, 255), (255, 255, 255, 170), (0, 0, 0, 110)

            def text(xy, t, f, fill=white, anchor="la"):
                d.text((xy[0] + 2, xy[1] + 3), t, font=f, fill=shadow, anchor=anchor)
                d.text(xy, t, font=f, fill=fill, anchor=anchor)
            text((60, 200), {"youtube": "Shorts", "instagram": "Reels"}.get(src.app, app), font(52, "bold"))
            text((SCREEN_W - 60, 200), app, font(36, "medium"), dim, "rm")
            x = 1110
            for i, (label, value) in enumerate([("Like", src.info.get("like_count")), ("Comments", src.info.get("comment_count")),
                                                ("Share", src.info.get("repost_count")), ("Remix", None)]):
                y = 1500 + 230 * i
                d.ellipse((x - 50, y - 50, x + 50, y + 50), fill=(255, 255, 255, 60), outline=white, width=5)
                text((x, y + 95), count_text(value) if value is not None else label, font(32, "bold"), white, "mm")
            lines = wrap(d, title, font(40), 860, 2)
            y = SCREEN_H - TAB_BAR_H - 70 - 56 * len(lines)
            d.ellipse((36, y - 120, 116, y - 40), fill=(120, 120, 120, 255))
            text((136, y - 80), "@" + channel.replace(" ", ""), font(44, "bold"), white, "lm")
            d.rounded_rectangle((136 + d.textlength("@" + channel.replace(" ", ""), font=font(44, "bold")) + 30, y - 110,
                                 136 + d.textlength("@" + channel.replace(" ", ""), font=font(44, "bold")) + 250, y - 50), 30, fill=white)
            d.text((136 + d.textlength("@" + channel.replace(" ", ""), font=font(44, "bold")) + 140, y - 80),
                   "Follow" if src.app == "instagram" else "Subscribe", font=font(32, "bold"), fill=(0, 0, 0, 255), anchor="mm")
            for i, line in enumerate(lines):
                text((36, y + 56 * i), line, font(40))
            top = SCREEN_H - TAB_BAR_H
            d.rectangle((0, top, SCREEN_W, SCREEN_H), fill=(0, 0, 0, 255))
            for i, name in enumerate(["Home", "Shorts" if src.app == "youtube" else "Search", "", "Subscriptions" if src.app == "youtube" else "Reels", "You"]):
                cx = 120 + 241 * i
                if name:
                    d.rounded_rectangle((cx - 30, top + 34, cx + 30, top + 88), 10, outline=(200, 200, 200, 255), width=6)
                    d.text((cx, top + 128), name, font=font(28, "medium"), fill=(200, 200, 200, 255), anchor="mm")
                else:
                    d.ellipse((cx - 50, top + 30, cx + 50, top + 130), outline=white, width=6)
                    d.text((cx, top + 80), "+", font=font(60, "bold"), fill=white, anchor="mm")
            grey = self.overlay.convert("LA")
            self.ink, self.mask = grey.getchannel("L"), grey.getchannel("A")
        else:
            # A watch page: the video at the top, the title, the channel and the comments below.
            page = Image.new("L", (SCREEN_W, SCREEN_H), 255)
            d = ImageDraw.Draw(page)
            y = 150 + round(src.height * SCREEN_W / src.width) + 40
            for i, line in enumerate(wrap(d, title, font(52, "bold"), SCREEN_W - 96, 3)):
                d.text((48, y + 66 * i), line, font=font(52, "bold"), fill=15)
            y += 66 * 3 + 20
            views = src.info.get("view_count")
            d.text((48, y), "%s views  %s  ...more" % (count_text(views), str(src.info.get("upload_date") or "")[:4]), font=font(36), fill=110)
            y += 90
            d.ellipse((48, y, 140, y + 92), fill=170)
            d.text((170, y + 46), channel, font=font(44, "bold"), fill=15, anchor="lm")
            d.text((170 + d.textlength(channel, font=font(44, "bold")) + 24, y + 46), count_text(src.info.get("channel_follower_count")), font=font(36), fill=110, anchor="lm")
            d.rounded_rectangle((SCREEN_W - 300, y + 6, SCREEN_W - 48, y + 86), 40, fill=15)
            d.text((SCREEN_W - 174, y + 46), "Subscribe", font=font(36, "bold"), fill=255, anchor="mm")
            y += 140
            for i, label in enumerate([count_text(src.info.get("like_count")), "Share", "Save", "Clip"]):
                x0 = 48 + 280 * i
                d.rounded_rectangle((x0, y, x0 + 250, y + 84), 42, fill=238)
                d.text((x0 + 125, y + 42), label, font=font(34, "medium"), fill=15, anchor="mm")
            y += 140
            d.rounded_rectangle((48, y, SCREEN_W - 48, y + 220), 30, fill=242)
            d.text((84, y + 50), "Comments  %s" % count_text(src.info.get("comment_count")), font=font(38, "bold"), fill=15, anchor="lm")
            d.text((84, y + 140), "Add a comment...", font=font(36), fill=130, anchor="lm")
            y += 280
            while y < SCREEN_H - 300:
                d.rounded_rectangle((48, y, 540, y + 280), 24, fill=225)
                d.rounded_rectangle((580, y + 10, SCREEN_W - 48, y + 60), 12, fill=232)
                d.rounded_rectangle((580, y + 90, SCREEN_W - 200, y + 130), 12, fill=238)
                y += 330
            status_bar(page, "00:00", dark=True)
            self.page = page

    def compose(self, frame: np.ndarray | None, clock: str) -> Image.Image:
        if self.vertical:
            screen = Image.new("L", (SCREEN_W, SCREEN_H), 0)
            if frame is not None:
                video = Image.fromarray(frame, "L")
                screen.paste(video, (0, max(0, (SCREEN_H - TAB_BAR_H - video.height) // 2)))
            screen.paste(self.ink, (0, 0), self.mask)
            status_bar(screen, clock, dark=False)
            return screen
        screen = self.page.copy()
        ImageDraw.Draw(screen).rectangle((0, 0, SCREEN_W, 120), fill=255)
        status_bar(screen, clock, dark=True)
        if frame is not None:
            screen.paste(Image.fromarray(frame, "L"), (0, 150))
        return screen


def article_screen(src: Source, page: int, clock: str) -> Image.Image:
    """A news article in a browser, one screen of it. No sound: the server reads the text from the screen."""
    img = Image.new("L", (SCREEN_W, SCREEN_H), 255)
    d = ImageDraw.Draw(img)
    status_bar(img, clock, dark=True)
    d.rounded_rectangle((48, SCREEN_H - 200, SCREEN_W - 48, SCREEN_H - 90), 40, fill=236)
    d.text((SCREEN_W / 2, SCREEN_H - 145), "news article", font=font(38), fill=60, anchor="mm")
    y = 180
    paragraphs = src.pages[min(page, len(src.pages) - 1)]
    for i, para in enumerate(paragraphs):
        headline = page == 0 and i == 0
        f = font(70, "bold") if headline else font(46)
        step = 86 if headline else 66
        for line in wrap(d, para, f, SCREEN_W - 120, 6 if headline else 9):
            if y > SCREEN_H - 300:
                break
            d.text((60, y), line, font=f, fill=10)
            y += step
        if headline:
            d.text((60, y + 20), "World  |  14 November 2026  |  4 min read", font=font(34), fill=120)
            y += 100
            d.rounded_rectangle((60, y, SCREEN_W - 60, y + 380), 20, fill=200)
            y += 420
        y += 34
    return img


def chat_screen(clock: str) -> Image.Image:
    """A private chat. The server must pause and keep nothing from it."""
    img = Image.new("L", (SCREEN_W, SCREEN_H), 255)
    d = ImageDraw.Draw(img)
    status_bar(img, clock, dark=True)
    d.rectangle((0, 120, SCREEN_W, 330), fill=246)
    d.text((60, 225), "<", font=font(70), fill=40, anchor="lm")
    d.ellipse((SCREEN_W / 2 - 60, 140, SCREEN_W / 2 + 60, 260), fill=190)
    d.text((SCREEN_W / 2, 200), "AK", font=font(48, "bold"), fill=255, anchor="mm")
    d.text((SCREEN_W / 2, 295), "Anna Korhonen", font=font(38, "medium"), fill=20, anchor="mm")
    msgs = [(False, "Are you home tonight?"), (True, "Yes, after 7. Door code is 4471"), (False, "Ok! Can you send me the rent money?"),
            (True, "Sent 640 EUR just now"), (False, "Thanks. Mum called, she is at the hospital again"), (True, "Oh no. I will call her"),
            (False, "Voice message 0:14")]
    y = 400
    for mine, t in msgs:
        lines = wrap(d, t, font(44), 700, 3)
        h = 40 + 58 * len(lines)
        w = max(d.textlength(x, font=font(44)) for x in lines) + 70
        x0 = SCREEN_W - 48 - w if mine else 48
        d.rounded_rectangle((x0, y, x0 + w, y + h), 44, fill=60 if mine else 232)
        for i, line in enumerate(lines):
            d.text((x0 + 35, y + 22 + 58 * i), line, font=font(44), fill=255 if mine else 15)
        y += h + 30
    d.rectangle((0, SCREEN_H - 230, SCREEN_W, SCREEN_H), fill=246)
    d.rounded_rectangle((150, SCREEN_H - 200, SCREEN_W - 150, SCREEN_H - 110), 45, outline=200, width=4, fill=255)
    d.text((190, SCREEN_H - 155), "Message", font=font(40), fill=160, anchor="lm")
    return img


def bank_screen(clock: str) -> Image.Image:
    """A banking app with the balance and payments. The server must pause and keep nothing from it."""
    img = Image.new("L", (SCREEN_W, SCREEN_H), 245)
    d = ImageDraw.Draw(img)
    status_bar(img, clock, dark=True)
    d.text((60, 250), "Accounts", font=font(84, "bold"), fill=10, anchor="ls")
    d.rounded_rectangle((48, 300, SCREEN_W - 48, 700), 40, fill=40)
    d.text((100, 380), "Current account", font=font(40), fill=210, anchor="lm")
    d.text((100, 470), "FI21 1234 5600 0007 85", font=font(38), fill=170, anchor="lm")
    d.text((100, 600), "2 418,56 EUR", font=font(96, "bold"), fill=255, anchor="lm")
    for i, label in enumerate(["Pay", "Transfer", "Cards", "More"]):
        cx = 170 + 290 * i
        d.ellipse((cx - 70, 760, cx + 70, 900), fill=225)
        d.text((cx, 950), label, font=font(36, "medium"), fill=30, anchor="mm")
    d.text((60, 1080), "Latest payments", font=font(50, "bold"), fill=10, anchor="lm")
    rows = [("K-Market Kamppi", "-23,40"), ("Anna Korhonen", "-640,00"), ("HSL mobile ticket", "-3,20"), ("Salary", "+3 105,00"),
            ("Pharmacy", "-18,90"), ("Elisa", "-29,99"), ("Wolt", "-31,50")]
    y = 1150
    d.rounded_rectangle((48, y, SCREEN_W - 48, y + 150 * len(rows)), 36, fill=255)
    for i, (who, amount) in enumerate(rows):
        ry = y + 150 * i
        d.ellipse((84, ry + 35, 164, ry + 115), fill=215)
        d.text((200, ry + 75), who, font=font(46), fill=10, anchor="lm")
        d.text((SCREEN_W - 96, ry + 75), amount, font=font(46, "medium"), fill=10, anchor="rm")
    d.text((SCREEN_W / 2, SCREEN_H - 120), "Log out", font=font(40), fill=120, anchor="mm")
    return img


# MARK: The extension's code paths, ported


class Resampler:
    """AudioDownmixer.resample: one-pole low-pass at 7 kHz, then linear resampling to 16 kHz, with state."""

    def __init__(self, in_rate: float):
        self.in_rate = in_rate
        self.position = 0.0
        self.last = 0.0
        self.low = 0.0
        self.alpha = 1 - math.exp(-2 * math.pi * 7000 / in_rate)
        self.step = in_rate / RATE

    def __call__(self, x: np.ndarray) -> np.ndarray:
        if len(x) == 0:
            return np.zeros(0, dtype=np.int16)
        a, y = self.alpha, self.low
        f = np.empty(len(x), dtype=np.float64)
        for i, v in enumerate(x.tolist()):
            y += a * (v - y)
            f[i] = y
        self.low = y
        n = len(f)
        count = max(0, math.ceil((n - 1 - self.position) / self.step))
        pos = self.position + self.step * np.arange(count)
        pos = pos[pos < n - 1]
        i = np.floor(pos).astype(np.int64)
        left = np.where(i < 0, self.last, f[np.clip(i, 0, n - 1)])
        right = f[np.clip(i + 1, 0, n - 1)]
        v = np.clip(left + (right - left) * (pos - i), -1, 1)
        self.position = (self.position + self.step * len(pos)) - n
        self.last = f[-1]
        return (v * 32767).astype(np.int16)


def mulaw(samples: np.ndarray) -> bytes:
    """MuLaw.encode from ChunkUploader.swift, the same bias, clip and bit layout."""
    s = samples.astype(np.int32)
    sign = np.where(s < 0, 0x80, 0)
    mag = np.minimum(np.abs(s), 32635) + 0x84
    # The Swift loop lowers the exponent while the mask bit is zero: find the highest set bit from 14 down.
    exponent = np.zeros(s.shape, dtype=np.int32)
    for e in range(7, 0, -1):
        hit = ((mag & (0x80 << e)) != 0) & (exponent == 0)
        exponent = np.where(hit, e, exponent)
    mantissa = (mag >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa) & 0xFF).astype(np.uint8).tobytes()


def signature(frame: np.ndarray) -> np.ndarray:
    """FrameSampler.signature: a 16 x 32 grid of mean grey levels over every third pixel."""
    h, w = frame.shape
    ys, xs = np.arange(0, h, 3), np.arange(0, w, 3)
    rows = np.minimum(31, ys * 32 // h)
    cols = np.minimum(15, xs * 16 // w)
    cells = (rows[:, None] * 16 + cols[None, :]).ravel()
    vals = frame[::3, ::3].astype(np.int64).ravel()
    sums = np.bincount(cells, weights=vals, minlength=512)
    counts = np.bincount(cells, minlength=512)
    return (sums // np.maximum(1, counts)).astype(np.int64)


def is_same(a: np.ndarray | None, b: np.ndarray | None) -> bool:
    if a is None or b is None or len(a) != len(b):
        return False
    d = np.abs(a - b)
    return float(d.mean()) < 2 and int(d.max()) < 16


def jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=50)  # ImageIO lossy quality 0.5
    return buf.getvalue()


# MARK: Uploaders (ChunkUploader.swift and FrameUploader.swift)


class ChunkUploader:
    """One upload at a time, in order, 3 attempts, 0.5 s before a retry, a queue of 6 that keeps the newest."""

    def __init__(self, run: "Replay"):
        self.run = run
        self.pending: deque = deque()
        self.cv = threading.Condition()
        self.busy = False
        self.stats = {"sent": 0, "bytes": 0, "failed": 0, "retries": 0, "dropped": 0, "lastMs": 0}
        threading.Thread(target=self.loop, daemon=True).start()

    def enqueue(self, chunk: dict):
        with self.cv:
            if len(self.pending) >= 6:
                old = self.pending[1] if self.busy else self.pending[0]
                self.pending.remove(old)
                self.stats["dropped"] += 1
                self.run.log("upload queue full, dropped chunk %d" % old["seq"])
                self.run.event("chunk_dropped", seq=old["seq"])
            chunk["attempts"] = 0
            self.pending.append(chunk)
            self.cv.notify()

    def idle(self) -> bool:
        with self.cv:
            return not self.pending and not self.busy

    def loop(self):
        while True:
            with self.cv:
                while not self.pending:
                    self.cv.wait()
                chunk = self.pending[0]
                self.busy = True
                chunk["attempts"] += 1
            r = self.run
            q = urllib.parse.urlencode({"session": r.session, "seq": chunk["seq"], "rate": 16000, "encoding": "mulaw", "attempt": chunk["attempts"]})
            t0 = now()
            code, body, err = http("POST", r.server + "/api/audio?" + q, chunk["mulaw"], "application/octet-stream", timeout=8)
            ms = int((now() - t0) * 1000)
            try:
                reply = json.loads(body or b"{}")
            except json.JSONDecodeError:
                reply = {}
            retry = False
            with self.cv:
                self.busy = False
                self.stats["lastMs"] = ms
                if self.pending and self.pending[0] is chunk:
                    if code == 200:
                        self.pending.popleft()
                        self.stats["sent"] += 1
                        self.stats["bytes"] += len(chunk["mulaw"])
                    elif chunk["attempts"] >= 3:
                        self.pending.popleft()
                        self.stats["failed"] += 1
                    else:
                        self.stats["retries"] += 1
                        retry = True
            if code == 200:
                r.chunk_sent(chunk, reply, ms)
            elif retry:
                r.log("audio POST attempt %d for chunk %d failed (%s), retrying" % (chunk["attempts"], chunk["seq"], err))
                r.event("chunk_retry", seq=chunk["seq"], attempt=chunk["attempts"], error=err, ms=ms)
                time.sleep(0.5)
            else:
                r.log("audio POST failed for chunk %d after %d attempts: %s" % (chunk["seq"], chunk["attempts"], err))
                r.chunk_failed(chunk, err, body[:200].decode(errors="replace"))


class TextUploader:
    """The on-device text path: POST /api/text in order, one at a time, 2 attempts. The reply carries the results of a
    final text, so a note is seen without waiting for the next poll, as the extension would."""

    def __init__(self, run: "Replay"):
        self.run = run
        self.pending: deque = deque()
        self.cv = threading.Condition()
        self.busy = False
        self.stats = {"sent": 0, "failed": 0, "discarded": 0, "lastMs": 0, "ms": []}
        threading.Thread(target=self.loop, daemon=True).start()

    def enqueue(self, item: dict):
        with self.cv:
            self.pending.append(item)
            self.cv.notify()

    def idle(self) -> bool:
        with self.cv:
            return not self.pending and not self.busy

    def loop(self):
        while True:
            with self.cv:
                while not self.pending:
                    self.cv.wait()
                item = self.pending.popleft()
                self.busy = True
            r = self.run
            body = {"session": r.session, "source": "speech", "text": item["text"], "lang": item["lang"], "t_start": item["tv0"], "t_end": item["tv1"],
                    "final": item["final"], "seq": item["seq"]}
            code, reply = 0, {}
            for attempt in (1, 2):
                t0 = now()
                code, reply = post_json(r.server, "/api/text", body, timeout=15)
                ms = int((now() - t0) * 1000)
                if code == 200:
                    break
                time.sleep(0.5)
            seen = now()
            with self.cv:
                self.busy = False
                self.stats["lastMs"] = ms
                if code == 200:
                    self.stats["sent"] += 1
                    if item["final"]:
                        self.stats["ms"].append(ms)
                    if reply.get("discarded"):
                        self.stats["discarded"] += 1
                else:
                    self.stats["failed"] += 1
            r.event("text_sent", t=item["t1"], seq=item["seq"], final=item["final"], ms=ms, code=code, words=len(item["text"].split()),
                    results=len(reply.get("results") or []), discarded=reply.get("discarded"), skipped=reply.get("skipped"),
                    detect_ms=reply.get("detectMs"), kind=item["kind"])
            if code != 200:
                r.error("text POST failed for seq %d: HTTP %s" % (item["seq"], code))
            for res in reply.get("results") or []:
                if isinstance(res, dict) and "id" in res:
                    r.on_result(res, seen)


class FrameUploader:
    """At most one frame in flight and one waiting; a new frame replaces the waiting one; 2 attempts."""

    def __init__(self, run: "Replay"):
        self.run = run
        self.cv = threading.Condition()
        self.in_flight = None
        self.waiting = None
        self.stats = {"sent": 0, "bytes": 0, "failed": 0, "retries": 0, "dropped": 0, "lastMs": 0}
        threading.Thread(target=self.loop, daemon=True).start()

    @property
    def full(self) -> bool:
        with self.cv:
            return self.in_flight is not None and self.waiting is not None

    def offer(self, item: dict):
        with self.cv:
            if self.waiting is not None:
                self.stats["dropped"] += 1
            item["attempts"] = 0
            self.waiting = item
            self.cv.notify()

    def idle(self) -> bool:
        with self.cv:
            return self.in_flight is None and self.waiting is None

    def loop(self):
        while True:
            with self.cv:
                while self.waiting is None:
                    self.cv.wait()
                item = self.waiting
                self.waiting = None
                item["attempts"] += 1
                self.in_flight = item
            r = self.run
            q = urllib.parse.urlencode({"w": item["w"], "h": item["h"], "ms": item["ms"], "session": r.session, "seq": item["seq"]})
            t0 = now()
            code, body, err = http("POST", r.server + "/api/frame?" + q, item["jpeg"], "image/jpeg", timeout=8)
            ms = int((now() - t0) * 1000)
            try:
                reply = json.loads(body or b"{}")
            except json.JSONDecodeError:
                reply = {}
            with self.cv:
                self.stats["lastMs"] = ms
                self.in_flight = None
                if code == 200:
                    self.stats["sent"] += 1
                    self.stats["bytes"] += len(item["jpeg"])
                elif item["attempts"] < 2 and self.waiting is None:
                    self.stats["retries"] += 1
                    self.waiting = item
                    self.cv.notify()
                    continue
                else:
                    self.stats["failed"] += 1
            if code == 200:
                r.frame_sent(item, reply, ms)
            else:
                r.log("frame POST %d failed: %s" % (item["seq"], err))
                r.event("frame_failed", seq=item["seq"], error=err, status=code)
                r.error("frame %d: %s %s" % (item["seq"], err, body[:120].decode(errors="replace")))


# MARK: The run


@dataclass
class Segment:
    kind: str  # "video", "other" (iOS Settings) or "private" (a chat or a banking app)
    index: int  # video index in the feed, -1 for another app
    t0: float  # content time where it starts
    seconds: float
    source: Source | None = None
    variant: str = ""  # "chat" or "bank" for a private screen
    audio_offset: float = 0  # where in source the sound of a private screen starts (the next part of the first video)


class Replay:
    def __init__(self, args, sources: list[Source]):
        self.args = args
        self.server = args.server.rstrip("/")
        self.session = args.session
        self.sources = sources
        self.out = Path(args.out).expanduser()
        self.out.mkdir(parents=True, exist_ok=True)
        self.timeline = open(self.out / "timeline.jsonl", "w")
        self.notes_file = open(self.out / "notes.jsonl", "w")
        self.lock = threading.RLock()
        self.speed = max(0.01, args.speed)
        self.chunk_seconds = float(args.chunk_seconds)
        self.fps = float(args.fps)
        self.state = "created"
        self.started_wall = now()
        self.start_mono = time.monotonic()
        self.content_t = 0.0
        self.log_lines: list[str] = []
        self.errors: list[str] = []
        self.c = dict(chunks_made=0, silent_skipped=0, frames_taken=0, frames_same=0, frames_skipped_full=0, polls=0, poll_failures=0,
                      audio_buffers=0, audio_seconds=0.0, chunks_paused=0, chunks_paused_while_tiktok=0)
        self.window_sq = 0.0
        self.window_n = 0
        self.max_rms = 0.0
        self.chunks: dict[int, dict] = {}
        self.frames: dict[int, dict] = {}
        self.results: dict[int, dict] = {}
        self.notes: list[dict] = []
        self.last_result_id = 0
        self.screen_reads = 0
        self.screen_tiktok_reads = 0
        self.screen_other_reads = 0
        self.first_frame_accepted: float | None = None
        self.first_chunk_accepted: float | None = None
        self.first_transcript: float | None = None
        self.first_screen_read: float | None = None
        self.modes_seen: list[tuple[float, str]] = []
        self.mode = None
        self.handles_seen: list[str] = []
        self.video_ids_seen: set = set()
        self.segment: Segment | None = None
        self.segments: list[Segment] = []
        self.uploader = ChunkUploader(self)
        self.frames_up = FrameUploader(self)
        self.texts_up = TextUploader(self)
        self.texts: dict[int, dict] = {}
        self.text_schedule: list[dict] = []
        self.first_text_final: float | None = None
        self.first_text_result: float | None = None
        self.private_hits: list[dict] = []
        self.stop_event = threading.Event()

    # Events and log.

    def where(self, t: float | None = None) -> dict:
        t = self.content_t if t is None else t
        seg = next((s for s in self.segments if s.t0 <= t < s.t0 + s.seconds), self.segments[-1] if self.segments else None)
        if not seg:
            return {"t": round(t, 3)}
        return {"t": round(t, 3), "video": seg.index, "tv": round(t - seg.t0, 3)}

    def event(self, name: str, t: float | None = None, **data):
        row = {"event": name, "wall": round(now(), 3)} | self.where(t) | data
        with self.lock:
            self.timeline.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.timeline.flush()

    def log(self, line: str):
        with self.lock:
            self.log_lines.append(line)
            del self.log_lines[:-50]

    def error(self, text: str):
        with self.lock:
            self.errors.append(text)
        self.event("error", text=text)

    def wall_at(self, t: float) -> float:
        return self.started_wall + t / self.speed

    def video_segment(self, index: int) -> Segment | None:
        return next((s for s in self.segments if s.kind == "video" and s.index == index), None)

    def segment_for(self, t0: float, t1: float) -> Segment | None:
        """The video a chunk belongs to: the video segment it overlaps most; another app only when it overlaps no video."""
        def overlap(s):
            return min(t1, s.t0 + s.seconds) - max(t0, s.t0)
        videos = [s for s in self.segments if s.kind == "video" and overlap(s) > 0]
        if videos:
            return max(videos, key=overlap)
        return self.segment_at((t0 + t1) / 2)

    def segment_at(self, t: float) -> Segment | None:
        return next((s for s in self.segments if s.t0 <= t < s.t0 + s.seconds + 1e-6), None)

    # Uploader callbacks.

    def chunk_sent(self, chunk: dict, reply: dict, ms: int):
        info = self.chunks[chunk["seq"]]
        info["sent_wall"] = now()
        info["reply"] = reply.get("reason") or ("transcribing" if reply.get("transcribing") else "")
        if reply.get("transcribing"):
            info["accepted"] = True
            if self.first_chunk_accepted is None:
                self.first_chunk_accepted = now()
        elif str(reply.get("reason", "")).startswith("paused"):
            self.c["chunks_paused"] += 1
            if info["kind"] == "video":
                self.c["chunks_paused_while_tiktok"] += 1
        self.event("chunk_sent", t=info["t1"], seq=chunk["seq"], attempt=chunk["attempts"], ms=ms, bytes=len(chunk["mulaw"]),
                   transcribing=bool(reply.get("transcribing")), reason=reply.get("reason"), server_rms=reply.get("rms"))

    def chunk_failed(self, chunk: dict, err: str, body: str):
        self.event("chunk_failed", t=self.chunks[chunk["seq"]]["t1"], seq=chunk["seq"], error=err, body=body)
        self.error("audio chunk %d failed after %d attempts: %s %s" % (chunk["seq"], chunk["attempts"], err, body))

    def frame_sent(self, item: dict, reply: dict, ms: int):
        info = self.frames[item["seq"]]
        info["reading"] = bool(reply.get("reading"))
        if not reply.get("reading") and reply.get("reason") in ("busy", "queue full"):
            self.last_sig = None  # FrameUploader.onNotRead: send the same screen again
        if reply.get("reading") and self.first_frame_accepted is None:
            self.first_frame_accepted = now()
        self.event("frame_sent", t=info["t"], seq=item["seq"], ms=ms, kb=round(len(item["jpeg"]) / 1024, 1), reading=bool(reply.get("reading")),
                   reason=reply.get("reason"))

    # Chunks (SampleHandler.flushChunk).

    def flush_chunk(self, pending: np.ndarray, pending_t0: float, force: bool) -> tuple[np.ndarray, float]:
        target = int(self.chunk_seconds * RATE)
        if len(pending) < (RATE // 5 if force else target):
            return pending, pending_t0
        limit = min(target, len(pending))
        cut = limit
        if not force:
            frame = RATE // 50
            search = max(RATE, target - RATE * 8 // 10)
            starts = np.arange(search, limit - frame + 1, frame)
            if len(starts):
                p = pending.astype(np.float64)
                energy = np.array([float(np.dot(p[s:s + frame], p[s:s + frame])) for s in starts])
                cut = int(starts[int(np.argmin(energy))] + frame)
        body = pending[:cut]
        rest = pending[cut:]
        self.c["chunks_made"] += 1
        seq = self.c["chunks_made"]
        seconds = len(body) / RATE
        t0, t1 = pending_t0, pending_t0 + seconds
        level = float(np.sqrt(np.mean(body.astype(np.float64) ** 2))) / 32768 if len(body) else 0
        seg = self.segment_for(t0, t1)
        info = {"seq": seq, "t0": t0, "t1": t1, "cut_wall": now(), "level": level, "kind": seg.kind if seg else "video",
                "index": seg.index if seg else -1, "seg_t0": seg.t0 if seg else 0, "accepted": False}
        self.chunks[seq] = info
        if level < SILENCE_RMS:
            self.c["silent_skipped"] += 1
            self.event("chunk_skipped_silent", t=t1, seq=seq, level=round(level, 5))
        else:
            self.event("chunk_made", t=t1, seq=seq, seconds=round(seconds, 3), t0=round(t0, 3), level=round(level, 5))
            self.uploader.enqueue({"seq": seq, "mulaw": mulaw(body), "seconds": seconds})
        return rest, t1

    # Frames (SampleHandler.handleVideo and process).

    def take_frame(self, screen: Image.Image):
        self.c["frames_taken"] += 1
        seq = self.c["frames_taken"]
        t0 = time.monotonic()
        if self.args.sim:
            # The real screen of an iOS simulator (the app in front, with the Dynamic Island), not a drawn one.
            path = self.out / "sim-screen.jpg"
            shot = subprocess.run(["xcrun", "simctl", "io", self.args.sim, "screenshot", "--type=jpeg", str(path)], capture_output=True, timeout=10)
            if shot.returncode != 0 or not path.exists():
                self.event("frame_failed", seq=seq, error="simulator screenshot failed: %s" % shot.stderr.decode(errors="replace")[:200])
                return
            real = Image.open(path).convert(screen.mode)
            real.load()
            small = real.resize((540, max(1, round(real.height * 540 / real.width))), Image.LANCZOS)
        elif self.args.raw_frames:
            small = screen.resize((540, max(1, round(screen.height * 540 / screen.width))), Image.LANCZOS)
        else:
            small = screen.resize((540, round(SCREEN_H * 540 / SCREEN_W)), Image.LANCZOS)
        arr = np.asarray(small)
        sig = signature(arr)
        if is_same(sig, getattr(self, "last_sig", None)):
            self.c["frames_same"] += 1
            self.event("frame_same", seq=seq)
            return
        if self.frames_up.full:
            self.c["frames_skipped_full"] += 1
            return
        data = jpeg(small)
        ms = int((time.monotonic() - t0) * 1000)
        self.last_sig = sig
        seg = self.segment
        self.frames[seq] = {"seq": seq, "t": self.content_t, "wall": now(), "kind": seg.kind if seg else "video", "index": seg.index if seg else -1,
                            "seg_t0": seg.t0 if seg else 0}
        self.event("frame_taken", seq=seq, w=small.width, h=small.height, kb=round(len(data) / 1024, 1), ms=ms, screen=seg.kind if seg else "")
        if self.args.save_frames:
            small.save(self.out / ("frame-%04d.jpg" % seq), quality=50)
        self.frames_up.offer({"seq": seq, "jpeg": data, "w": small.width, "h": small.height, "ms": ms})

    # Poll (SampleHandler.poll and handle).

    def poll_loop(self):
        while not self.stop_event.is_set():
            t0 = time.monotonic()
            self.poll_once()
            time.sleep(max(0.05, 1 - (time.monotonic() - t0)))

    def poll_once(self):
        q = urllib.parse.urlencode({"session": self.session, "since": self.last_result_id, "source": "broadcast"})
        code, body, err = http("GET", self.server + "/api/latest?" + q, timeout=4)
        try:
            data = json.loads(body) if code == 200 else None
        except json.JSONDecodeError:
            data = None
        if data is None:
            self.c["poll_failures"] += 1
            if self.c["poll_failures"] in (1, 5, 20):
                self.error("poll /api/latest failed: %s" % (err or "bad JSON"))
            return
        seen = now()
        self.c["polls"] += 1
        cfg = data.get("config") or {}
        if cfg.get("chunkSeconds") in (3, 4, 5, 8) and float(cfg["chunkSeconds"]) != self.chunk_seconds:
            self.log("chunk length now %d s" % cfg["chunkSeconds"])
            self.chunk_seconds = float(cfg["chunkSeconds"])
        if cfg.get("fps") in (0.5, 1, 2):
            self.fps = float(cfg["fps"])
        for r in data.get("results", []):
            self.last_result_id = max(self.last_result_id, r["id"])
            self.on_result(r, seen)
        sc = data.get("screen") or {}
        reads = int(sc.get("reads") or 0)
        if reads > self.screen_reads or (sc and sc.get("paused") != getattr(self, "last_paused", None)):
            new = reads > self.screen_reads
            self.screen_reads = max(self.screen_reads, reads)
            self.last_paused = sc.get("paused")
            if new and self.first_screen_read is None:
                self.first_screen_read = seen
            if sc.get("handle") and sc["handle"] not in self.handles_seen:
                self.handles_seen.append(sc["handle"])
            self.video_ids_seen.add(sc.get("videoId"))
            self.event("screen_read", reads=reads, app=sc.get("app"), paused=sc.get("paused"), handle=sc.get("handle"), videoId=sc.get("videoId"),
                       description=(sc.get("description") or "")[:120], onScreenText=(sc.get("onScreenText") or "")[:120],
                       likes=sc.get("likes"), comments=sc.get("comments"), shares=sc.get("shares"))
        st = data.get("state") or {}
        if st.get("mode") and st["mode"] != self.mode:
            self.mode = st["mode"]
            self.modes_seen.append((seen, self.mode))
            self.event("state", mode=self.mode, line=st.get("line"), count=st.get("count"), handle=st.get("handle"), flagLabel=st.get("flagLabel"))

    def origin(self, r: dict) -> dict | None:
        """The chunk, text or frame a result came from."""
        if r.get("textPath"):
            return self.texts.get(r.get("seq"))
        if r.get("source", "audio") == "audio":
            return None if r.get("forced") else self.chunks.get(r.get("seq"))
        return self.frames.get(r.get("seq"))

    def on_result(self, r: dict, seen: float):
        if r["id"] in self.results:
            return
        self.results[r["id"]] = r | {"seen": seen}
        source = r.get("source", "audio")
        org = self.origin(r)
        if org and org.get("kind") == "private" and (r.get("speech") or r.get("technique") or r.get("unsure")):
            # The privacy guard failed: something heard or read on a private screen came back.
            with self.lock:
                self.private_hits.append({"result_id": r["id"], "source": source, "text": (r.get("text") or "")[:120],
                                          "technique": r.get("technique"), "variant": org.get("variant")})
        base = dict(result_id=r["id"], seq=r.get("seq"), source=source, text=(r.get("text") or "")[:400], speech=r.get("speech"),
                    technique=r.get("technique"), unsure=r.get("unsure"), confidence=r.get("confidence"), backend=r.get("backend"),
                    latency=r.get("latency"), server_received=r.get("received"), videoId=r.get("videoId"), handle=r.get("handle"))
        if r.get("textPath"):
            if self.first_text_result is None:
                self.first_text_result = seen
            self.event("transcript", t=org["t1"] if org else None, result_id=r["id"], seq=r.get("seq"), source="text", text=(r.get("text") or "")[:400],
                       speech=r.get("speech"), technique=r.get("technique"), unsure=r.get("unsure"), backend=r.get("backend"))
            if self.first_transcript is None:
                self.first_transcript = seen
        elif source == "audio":
            if self.first_transcript is None:
                self.first_transcript = seen
            info = self.chunks.get(r.get("seq"))
            if info:
                info["result"] = r["id"]
            self.event("transcript", t=info["t1"] if info else None, **base)
        if not (r.get("technique") or r.get("unsure")):
            return
        if r.get("textPath"):
            info = org
            seg = self.video_segment(info["index"]) if info and info["kind"] == "video" else None
            tv0, tv1 = (info["tv0"], info["tv1"]) if info else (None, None)
            end_wall = self.wall_at(info["t1"]) if info else None
            t_from = "text"
            sentence = sentence_with(r.get("text", ""), r.get("mark"))
        elif source == "audio":
            info = self.chunks.get(r.get("seq"))
            seg = self.video_segment(info["index"]) if info else None
            if info:
                tv0, tv1 = info["t0"] - info["seg_t0"], info["t1"] - info["seg_t0"]
                end_wall = info["cut_wall"]
            else:
                tv0 = tv1 = None
                end_wall = None
            t_from = "chunk"
            sentence = sentence_with(r.get("text", ""), r.get("mark"))
            if seg and seg.source and seg.source.units and tv1 is not None:
                best, score = None, 0.3
                for u in seg.source.units:
                    if u["t_end"] < tv0 - 0.5 or u["t_start"] > tv1 + 0.5:
                        continue
                    s = difflib.SequenceMatcher(None, norm(sentence), norm(u.get("text", ""))).ratio()
                    if s > score:
                        best, score = u, s
                if best:
                    end = min(max(best["t_end"], tv0), tv1)
                    end_wall = end_wall - (tv1 - end) / self.speed
                    tv0, tv1, t_from = best["t_start"], end, "units"
        else:
            info = self.frames.get(r.get("seq"))
            seg = self.video_segment(info["index"]) if info else None
            tv0 = tv1 = (info["t"] - info["seg_t0"]) if info else None
            end_wall = info["wall"] if info else None
            t_from = "frame"
            sentence = r.get("text", "")
        src = seg.source if seg else None
        note = {
            "video_id": src.id if src else None,
            "url": src.url if src else None,
            "sentence": sentence,
            "source": "speech" if source == "audio" else "screen",
            "technique": r.get("technique"),
            "label": r.get("label"),
            "probs": r.get("probs"),
            "confidence": r.get("confidence"),
            "unsure": bool(r.get("unsure")),
            "t_video_start": round(tv0, 2) if tv0 is not None else None,
            "t_video_end": round(tv1, 2) if tv1 is not None else None,
            "t_note_wall": round(seen, 3),
            "delay_s": round(seen - end_wall, 2) if end_wall else None,
            "t_from": t_from,
            "result_id": r["id"],
            "seq": r.get("seq"),
            "text": r.get("text"),
            "mark": r.get("mark"),
            "backend": r.get("backend"),
            "detect_ms": r.get("detect_ms"),
            "app": src.app if src else None,
        }
        with self.lock:
            self.notes.append(note)
            self.notes_file.write(json.dumps(note, ensure_ascii=False) + "\n")
            self.notes_file.flush()
        self.event("note", t=(info.get("t1", info.get("t")) if info else None), **{k: note[k] for k in (
            "video_id", "source", "technique", "unsure", "sentence", "delay_s", "t_from")})

    # Heartbeat (SampleHandler.sendHeartbeat), with the same keys.

    def heartbeat_loop(self):
        while not self.stop_event.wait(2):
            self.send_heartbeat()

    def send_heartbeat(self):
        up, fr = self.uploader.stats, self.frames_up.stats
        with self.lock:
            rms = math.sqrt(self.window_sq / self.window_n) if self.window_n else 0
            self.window_sq, self.window_n = 0.0, 0
            self.max_rms = max(self.max_rms, rms)
            lines, self.log_lines = self.log_lines, []
        body = {
            "source": "broadcast", "session": self.session, "state": self.state, "replay": True,
            "note": "replay.py (simulated broadcast extension)", "bundle": "replay.py",
            "video": self.c["frames_taken"], "audioApp": self.c["audio_buffers"], "audioMic": 0,
            "audioAppSeconds": round(self.c["audio_seconds"], 1), "rms": round(rms, 4), "rmsMax": round(self.max_rms, 4), "micRms": 0,
            "chunkSeconds": self.chunk_seconds, "chunksMade": self.c["chunks_made"], "chunksSent": up["sent"], "bytesSent": up["bytes"],
            "postErrors": up["failed"], "retries": up["retries"], "droppedChunks": up["dropped"], "queued": len(self.uploader.pending),
            "inFlight": 1 if self.uploader.busy else 0, "lastUploadMs": up["lastMs"], "silentSkipped": self.c["silent_skipped"],
            "encoding": "mulaw", "updatePath": "auto", "updatePathActive": "none (replay has no Live Activity)", "activitiesVisible": 0,
            "activityUpdatesFromExtension": 0, "activityUpdatesVerified": 0, "activityAlerts": 0, "activityLastError": "",
            "polls": self.c["polls"], "pollFailures": self.c["poll_failures"], "format": "%d Hz, 2 ch, float32 (replay)" % IN_RATE,
            "uptime": int(now() - self.started_wall), "memoryMB": 0, "memoryPeakMB": 0, "memoryKernelPeakMB": 0,
            "screenPath": "jpeg", "fps": self.fps, "framesSeen": self.c["frames_taken"], "framesTaken": self.c["frames_taken"],
            "framesSame": self.c["frames_same"], "framesSent": fr["sent"], "framesDropped": fr["dropped"], "frameRetries": fr["retries"],
            "frameErrors": fr["failed"], "frameLastError": "", "frameBytes": fr["bytes"], "frameSize": "540x1174",
            "lastFrameMs": 0, "lastFrameKB": 0, "lastFrameUploadMs": fr["lastMs"], "ocrMs": 0, "ocrLines": 0,
        }
        code, _ = post_json(self.server, "/api/heartbeat", body)
        if code != 200:
            self.error("heartbeat failed: HTTP %s" % code)
        if lines:
            post_json(self.server, "/api/log", {"source": "broadcast", "session": self.session, "lines": lines})

    # Playback.

    def build_segments(self):
        t = 0.0
        for i, src in enumerate(self.sources):
            if i > 0 and self.args.visit_other > 0:
                self.segments.append(Segment("other", -1, t, self.args.visit_other))
                t += self.args.visit_other
            seconds = min(src.duration, self.args.max_seconds) if self.args.max_seconds else src.duration
            self.segments.append(Segment("video", i, t, seconds, src))
            t += seconds
        if self.args.visit_after > 0:
            self.segments.append(Segment("other", -1, t, self.args.visit_after))
            t += self.args.visit_after
        if self.args.private > 0:
            # Speech keeps playing on the private screens (a voice message, a video in the background): the next part of
            # the first video, so the server gets real speech it has not heard yet.
            first = next((sg for sg in self.segments if sg.kind == "video" and sg.source.kind == "video"), None)
            offset = 0.0
            if first:
                offset = max(0.0, min(first.seconds, first.source.duration - 2 * self.args.private))
            for variant in ("chat", "bank"):
                self.segments.append(Segment("private", -1, t, self.args.private, first.source if first else None, variant, offset))
                offset += self.args.private
                t += self.args.private
        if self.args.text_path:
            self.build_text_schedule()

    def build_text_schedule(self):
        """What the phone's recognizer would give for the reference transcript: a partial text in the middle of each
        sentence of 6 or more words, and the final text text_lag seconds after the sentence ends."""
        lag = self.args.text_lag
        out = []
        for sg in self.segments:
            src = sg.source
            if not src or src.kind != "video" or sg.kind not in ("video", "private"):
                continue
            lo = sg.audio_offset if sg.kind == "private" else 0.0
            hi = lo + sg.seconds
            for u in src.units:
                if u["t_end"] <= lo or u["t_start"] >= hi:
                    continue
                text = " ".join(str(u.get("text") or "").split())
                if not text:
                    continue
                tv0, tv1 = max(u["t_start"], lo), min(u["t_end"], hi)
                c0, c1 = sg.t0 + tv0 - lo, sg.t0 + tv1 - lo
                base = {"lang": u.get("lang") or "en", "tv0": round(tv0, 2), "tv1": round(tv1, 2), "t0": c0, "t1": c1, "kind": sg.kind,
                        "index": sg.index, "seg_t0": sg.t0, "variant": sg.variant}
                words = text.split()
                if len(words) >= 6:
                    out.append(base | {"at": (c0 + c1) / 2, "text": " ".join(words[:len(words) // 2]), "final": False})
                out.append(base | {"at": c1 + lag, "text": text, "final": True})
        out.sort(key=lambda x: x["at"])
        for i, item in enumerate(out, 1):
            item["seq"] = i
            self.texts[i] = item
        self.text_schedule = out
        if not any(x["final"] for x in out):
            raise SystemExit("the text path needs a reference transcript: no sentence units in %s/units/<id>.json for %s"
                             % (CACHE, ", ".join(sg.source.id for sg in self.segments if sg.source)))

    def play(self):
        self.build_segments()
        if not self.args.no_start:
            code, cfg = post_json(self.server, "/api/config", {"session": self.session, "start": True, "running": True, "screenPath": "jpeg",
                                                               "fps": self.fps, "chunkSeconds": self.chunk_seconds})
            if code != 200:
                raise SystemExit("server did not accept the run start: HTTP %s" % code)
        self.state = "running"
        self.started_wall = now()
        self.start_mono = time.monotonic()
        self.log("broadcast started (replay.py, %d video(s), speed %g)" % (len(self.sources), self.speed))
        self.event("start", session=self.session, server=self.server, speed=self.speed, sources=[s.ref for s in self.sources],
                   raw_frames=self.args.raw_frames, segments=[{"kind": s.kind, "video": s.index, "t0": s.t0, "seconds": round(s.seconds, 2),
                                                               "id": s.source.id if s.source else None} for s in self.segments])
        self.send_heartbeat()
        threading.Thread(target=self.heartbeat_loop, daemon=True).start()
        threading.Thread(target=self.poll_loop, daemon=True).start()

        resampler = Resampler(IN_RATE)
        pending = np.zeros(0, dtype=np.int16)
        pending_t0 = 0.0
        last_frame_t = -1e9
        step = BUFFER / IN_RATE
        texts = deque(self.text_schedule)
        for seg in self.segments:
            self.segment = seg
            src = seg.source
            self.event("segment", t=seg.t0, kind=seg.kind, id=src.id if src and seg.kind == "video" else None,
                       handle=src.handle if src and seg.kind == "video" else None, app=src.app if src and seg.kind == "video" else None,
                       variant=seg.variant or None, seconds=round(seg.seconds, 2))
            label = {"other": "Settings", "private": "private screen: " + seg.variant}.get(seg.kind) or "%s %s (%s)" % (src.handle, src.id, src.app)
            print("[%s] %s %s (%.0f s)" % (iso(now()), seg.kind, label, seg.seconds), flush=True)
            reader, screen = None, None
            if seg.kind == "video" and src.kind == "video":
                audio = decode_audio(src.video, seg.seconds)
                reader = FrameReader(src.video, src.width, src.height, seg.seconds)
                if not self.args.raw_frames:
                    screen = TikTokScreen(src) if src.app == "tiktok" else MediaScreen(src)
            elif seg.kind == "private" and src:
                audio = decode_audio(src.video, seg.seconds, start=seg.audio_offset)
                audio = np.pad(audio, (0, max(0, int(seg.seconds * IN_RATE) - len(audio))))
            else:
                audio = np.zeros(int(seg.seconds * IN_RATE), dtype=np.float32)
            pos = 0
            while pos < len(audio):
                t = seg.t0 + pos / IN_RATE
                self.content_t = t
                delay = self.start_mono + t / self.speed - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                buf = audio[pos:pos + BUFFER]
                pos += BUFFER
                with self.lock:
                    self.window_sq += float(np.dot(buf, buf))
                    self.window_n += len(buf)
                self.c["audio_buffers"] += 1
                self.c["audio_seconds"] += len(buf) / IN_RATE
                if self.args.text_path:
                    # The phone transcribes: no audio leaves it, only the text of each sentence.
                    while texts and texts[0]["at"] <= t:
                        item = texts.popleft()
                        if item["final"] and self.first_text_final is None:
                            self.first_text_final = now()
                        self.texts_up.enqueue(item)
                else:
                    pending = np.concatenate([pending, resampler(buf)])
                    if len(pending) >= int(self.chunk_seconds * RATE):
                        pending, pending_t0 = self.flush_chunk(pending, pending_t0, force=False)
                if t - last_frame_t >= 1 / max(0.25, self.fps):
                    last_frame_t = t
                    clock = time.strftime("%H:%M")
                    if seg.kind == "other":
                        img = settings_screen(clock)
                    elif seg.kind == "private":
                        img = chat_screen(clock) if seg.variant == "chat" else bank_screen(clock)
                    elif src.kind == "article":
                        img = article_screen(src, int((t - seg.t0) / ARTICLE_PAGE_SECONDS), clock)
                    elif self.args.raw_frames:
                        f = reader.at(t - seg.t0)
                        img = Image.fromarray(f, "L") if f is not None else Image.new("L", (540, 960), 0)
                    else:
                        img = screen.compose(reader.at(t - seg.t0), clock)
                    self.take_frame(img)
            if reader:
                reader.close()
        self.content_t = self.segments[-1].t0 + self.segments[-1].seconds if self.segments else 0
        for item in texts:  # the last finals land after the end of the playback
            self.texts_up.enqueue(item)
        if not self.args.text_path:
            pending, pending_t0 = self.flush_chunk(pending, pending_t0, force=True)
        self.event("playback_end")

    def finish(self, tail: float):
        """Waits for the last transcripts, then stops the run like the app's Stop and the end of the broadcast."""
        end = time.monotonic() + 40
        while time.monotonic() < end and not (self.uploader.idle() and self.frames_up.idle() and self.texts_up.idle()):
            time.sleep(0.2)
        last_change, count = time.monotonic(), len(self.results)
        deadline = time.monotonic() + max(tail, 5)
        while time.monotonic() < deadline:
            time.sleep(0.5)
            missing = [c for c in self.chunks.values() if c.get("accepted") and "result" not in c and now() - c.get("sent_wall", now()) < 20]
            if len(self.results) != count:
                last_change, count = time.monotonic(), len(self.results)
            if not missing and time.monotonic() - last_change > 6:
                break
        if not self.args.keep_running:
            self.state = "finished"
            self.log("broadcast finished")
            self.send_heartbeat()
            post_json(self.server, "/api/config", {"session": self.session, "running": False})
            # The Island state after the stop, for the smoke check.
            t_end = time.monotonic() + 12
            while time.monotonic() < t_end and self.mode not in ("idle",):
                time.sleep(1)
        self.stop_event.set()
        self.event("stop")
        time.sleep(0.2)

    # Summary and checks.

    def debug_snapshot(self) -> dict:
        code, body, _ = http("GET", self.server + "/debug.json", timeout=15)
        try:
            return json.loads(body) if code == 200 else {}
        except json.JSONDecodeError:
            return {}

    def summary(self, smoke: bool) -> dict:
        dbg = self.debug_snapshot()
        up, fr = self.uploader.stats, self.frames_up.stats
        transcripts = [r for r in self.results.values() if r.get("source") == "audio"]
        speech = [r for r in transcripts if r.get("speech")]
        accepted = [c for c in self.chunks.values() if c.get("accepted")]
        no_result = [c["seq"] for c in accepted if "result" not in c]
        delays = [n["delay_s"] for n in self.notes if n["delay_s"] is not None]
        speech_delays = [n["delay_s"] for n in self.notes if n["delay_s"] is not None and n["source"] == "speech"]
        backends = sorted({str(r.get("backend")) for r in self.results.values() if r.get("backend")})
        detector = ", ".join(backends) if backends else "keyword rules (results carry no backend field)"
        transcript_latency = [r["seen"] - self.chunks[r["seq"]]["cut_wall"] for r in transcripts if r.get("seq") in self.chunks]
        checks = []

        def check(name, ok, detail=""):
            checks.append({"check": name, "ok": bool(ok), "detail": detail})

        if self.args.text_path:
            ok = self.first_text_result is not None and self.first_text_final is not None and self.first_text_result - self.first_text_final <= 8
            check("a result within 8 s of the first final text (text path)", ok,
                  "%.1f s" % (self.first_text_result - self.first_text_final) if ok or (self.first_text_result and self.first_text_final) else "none arrived")
            check("no text POST failed", self.texts_up.stats["failed"] == 0, "%d failed" % self.texts_up.stats["failed"])
        elif self.first_chunk_accepted:
            ok = self.first_transcript is not None and self.first_transcript - self.first_chunk_accepted <= 15
            check("a transcript within 15 s of the first accepted chunk", ok,
                  "%.1f s" % (self.first_transcript - self.first_chunk_accepted) if self.first_transcript else "none arrived")
        else:
            check("the server accepted an audio chunk for transcription", False, "no chunk was accepted")
        if self.first_frame_accepted:
            ok = self.first_screen_read is not None and self.first_screen_read - self.first_frame_accepted <= 15
            check("a screen read within 15 s of the first accepted frame", ok,
                  "%.1f s" % (self.first_screen_read - self.first_frame_accepted) if self.first_screen_read else "none arrived")
        else:
            check("the server accepted a screen frame", False, "no frame was accepted")
        if not self.args.text_path:
            check("no audio chunk failed after its retries", up["failed"] == 0, "%d failed" % up["failed"])
        check("polls of /api/latest work", self.c["polls"] > 0 and self.c["poll_failures"] <= max(3, self.c["polls"] // 10),
              "%d polls, %d failed" % (self.c["polls"], self.c["poll_failures"]))
        modes = [m for _, m in self.modes_seen]
        if smoke:
            check("at least one speech transcript", len(speech) >= 1, "%d" % len(speech))
            check("the screen read found the TikTok handle", bool(self.handles_seen), ", ".join(self.handles_seen) or "no handle")
            check("the state went to listening", any(m in ("listening", "found") for m in modes), " > ".join(modes))
            if self.args.visit_after > 0 or self.args.visit_other > 0 or self.args.private > 0:
                check("the state paused while another app was in front", "paused" in modes, " > ".join(modes))
            if not self.args.keep_running:
                check("the state went back to idle after the stop", modes[-1:] == ["idle"], " > ".join(modes))
        private = [sg for sg in self.segments if sg.kind == "private"]
        if private:
            p_chunks = [c for c in self.chunks.values() if c["kind"] == "private"]
            p_texts = [x for x in self.texts.values() if x["kind"] == "private" and x["final"]]
            p_frames = [f for f in self.frames.values() if f["kind"] == "private"]
            hits_heard = [h for h in self.private_hits if not h["technique"]]
            hits_notes = [h for h in self.private_hits if h["technique"]]
            sent = "%d chunks with sound" % len([c for c in p_chunks if c["level"] >= SILENCE_RMS]) if not self.args.text_path else "%d texts" % len(p_texts)
            check("no transcript from a private screen", not hits_heard, "%s sent, %d came back%s" % (
                sent, len(hits_heard), (": " + "; ".join(h["text"] for h in hits_heard[:3])) if hits_heard else ""))
            check("no note from a private screen", not hits_notes, "%d frames sent, %d notes" % (len(p_frames), len(hits_notes)))
            p_t0 = self.wall_at(private[0].t0)
            p_t1 = self.wall_at(private[-1].t0 + private[-1].seconds) + 6
            check("the state paused on the private screens", any(m == "paused" and p_t0 <= t <= p_t1 for t, m in self.modes_seen),
                  " > ".join("%s@%.0f" % (m, t - self.started_wall) for t, m in self.modes_seen))
        cost = (dbg.get("cost") or {}).get(self.session) or {}
        out = {
            "session": self.session,
            "server": self.server,
            "server_started": dbg.get("server_started"),
            "model": dbg.get("model"),
            "vision_model": dbg.get("vision_model"),
            "detector": detector,
            "speed": self.speed,
            "raw_frames": self.args.raw_frames,
            "path": "text" if self.args.text_path else "audio",
            "sources": [{"ref": s.ref, "id": s.id, "url": s.url, "handle": s.handle, "seconds": round(s.duration, 1), "app": s.app, "kind": s.kind}
                        for s in self.sources],
            "apps_seen": sorted({str(r.get("app")) for r in self.results.values() if r.get("app")}),
            "text": {"sent": self.texts_up.stats["sent"], "failed": self.texts_up.stats["failed"], "discarded": self.texts_up.stats["discarded"],
                     "finals": len([x for x in self.texts.values() if x["final"]]),
                     "post_ms_median": percentile(self.texts_up.stats["ms"], 0.5), "post_ms_p95": percentile(self.texts_up.stats["ms"], 0.95),
                     "lag_s": self.args.text_lag} if self.args.text_path else None,
            "private": {"seconds": self.args.private * 2, "hits": self.private_hits} if private else None,
            "content_seconds": round(self.content_t, 1),
            "wall_seconds": round(now() - self.started_wall, 1),
            "chunks": {"made": self.c["chunks_made"], "silent_skipped": self.c["silent_skipped"], "sent": up["sent"], "failed": up["failed"],
                       "retries": up["retries"], "dropped": up["dropped"], "accepted_for_transcription": len(accepted),
                       "without_transcript": no_result, "discarded_paused": self.c["chunks_paused"],
                       "discarded_paused_while_tiktok": self.c["chunks_paused_while_tiktok"]},
            "frames": {"taken": self.c["frames_taken"], "same": self.c["frames_same"], "skipped_full": self.c["frames_skipped_full"],
                       "sent": fr["sent"], "failed": fr["failed"], "retries": fr["retries"], "dropped": fr["dropped"],
                       "accepted_for_reading": sum(1 for f in self.frames.values() if f.get("reading"))},
            "screen_reads": self.screen_reads,
            "handles_seen": self.handles_seen,
            "transcripts": len(transcripts),
            "speech_transcripts": len(speech),
            "no_speech_transcripts": len(transcripts) - len(speech),
            "transcript_latency_s": {"median": percentile(transcript_latency, 0.5), "p95": percentile(transcript_latency, 0.95)},
            "notes": len([n for n in self.notes if not n["unsure"]]),
            "unsure": len([n for n in self.notes if n["unsure"]]),
            "notes_by_source": {k: len([n for n in self.notes if n["source"] == k]) for k in ("speech", "screen")},
            "delay_s": {"median": percentile(delays, 0.5), "p95": percentile(delays, 0.95), "n": len(delays)},
            "speech_delay_s": {"median": percentile(speech_delays, 0.5), "p95": percentile(speech_delays, 0.95), "n": len(speech_delays)},
            "polls": self.c["polls"],
            "poll_failures": self.c["poll_failures"],
            "states": [{"t": round(t - self.started_wall, 1), "mode": m} for t, m in self.modes_seen],
            "cost": {"router_credits_audio": cost.get("audio_total"), "router_credits_screen": cost.get("screen_total"),
                     "result_credits_sum": round(sum(float(r.get("credits") or 0) for r in self.results.values()), 4),
                     "detect_ms_median": percentile([float(r["detect_ms"]) for r in self.results.values() if r.get("detect_ms") is not None], 0.5)},
            "errors": self.errors,
            "checks": checks,
            "ok": all(c["ok"] for c in checks),
        }
        (self.out / "summary.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
        return out


def main():
    p = argparse.ArgumentParser(description="Play the broadcast extension's part from real TikTok videos, without an iPhone.")
    p.add_argument("sources", nargs="*")
    p.add_argument("--feed")
    p.add_argument("--visit-other", type=float, default=0)
    p.add_argument("--visit-after", type=float, default=0, help="show another app for S seconds after the last video")
    p.add_argument("--session")
    p.add_argument("--speed", type=float, default=1)
    p.add_argument("--max-seconds", type=float)
    p.add_argument("--raw-frames", action="store_true")
    p.add_argument("--sim", help="the UDID of a booted iOS simulator: each frame is its real screen (the video plays there in a browser), the audio still comes from the video file")
    p.add_argument("--chunk-seconds", type=float, default=4)
    p.add_argument("--fps", type=float, default=1)
    p.add_argument("--server", default=os.environ.get("FOOTNOTE_SERVER", SERVER))
    p.add_argument("--out")
    p.add_argument("--keep-running", action="store_true")
    p.add_argument("--no-start", action="store_true")
    p.add_argument("--tail", type=float, default=20, help="seconds to wait for the last transcripts after playback")
    p.add_argument("--save-frames", action="store_true", help="also write each sent frame to --out (for a look at the simulated screen)")
    p.add_argument("--smoke", action="store_true")
    p.add_argument("--print-media", action="store_true", help="download or find the media, print its paths and metadata as JSON, and exit")
    p.add_argument("--private", type=float, default=0, help="after the last video, a chat and a banking app for S seconds each, with speech")
    p.add_argument("--app", choices=("tiktok", "youtube", "instagram", "web"), help="the app of the video sources when the metadata does not say")
    p.add_argument("--text-path", action="store_true", help="send the reference transcript to POST /api/text instead of audio chunks")
    p.add_argument("--text-lag", type=float, default=0.8, help="seconds from the end of a sentence to its final on-device text")
    args = p.parse_args()

    if args.smoke:
        if not args.sources:
            args.sources = [SMOKE_URL]
        args.max_seconds = args.max_seconds or 22
        args.private = args.private or 8
        args.speed = 1
    refs = list(args.sources)
    if args.feed:
        refs += [l.strip() for l in Path(args.feed).expanduser().read_text().splitlines() if l.strip() and not l.strip().startswith("#")]
    if not refs:
        p.error("give a TikTok URL, a video file, --feed FILE or --smoke")
    args.session = args.session or "replay-" + time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    if args.session == "lens-demo":
        p.error("lens-demo is the phone's session; pick another name")
    args.out = args.out or str(CACHE / "runs" / args.session)
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit("%s is not installed" % tool)
    try:
        sources = [resolve(r) for r in refs]
        for src in sources:
            src.app_override = args.app
    except subprocess.CalledProcessError as e:
        print("could not read the media: %s" % e, file=sys.stderr)
        sys.exit(2)
    except SystemExit as e:
        print(e, file=sys.stderr)
        sys.exit(2)

    if args.print_media:
        print(json.dumps([{"ref": s.ref, "id": s.id, "url": s.url, "video": str(s.video), "handle": s.handle, "seconds": round(s.duration, 2),
                           "width": s.width, "height": s.height, "app": s.app, "kind": s.kind} for s in sources], ensure_ascii=False))
        return
    run = Replay(args, sources)
    print("session %s, server %s, output %s" % (args.session, run.server, run.out), flush=True)
    try:
        run.play()
        run.finish(args.tail)
    except KeyboardInterrupt:
        run.state = "finished"
        run.send_heartbeat()
        run.stop_event.set()
    s = run.summary(args.smoke)
    print(json.dumps({k: s[k] for k in ("session", "detector", "path", "apps_seen", "text", "private", "chunks", "frames", "screen_reads", "handles_seen", "transcripts", "speech_transcripts",
                                        "notes", "unsure", "delay_s", "transcript_latency_s", "states", "cost", "errors")}, indent=1, ensure_ascii=False))
    for c in s["checks"]:
        print("%s %s%s" % ("PASS" if c["ok"] else "FAIL", c["check"], " (%s)" % c["detail"] if c["detail"] else ""))
    print("timeline: %s" % (run.out / "timeline.jsonl"))
    if not s["ok"]:
        print("the loop is broken: %s" % "; ".join(c["check"] + " - " + c["detail"] for c in s["checks"] if not c["ok"]), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
