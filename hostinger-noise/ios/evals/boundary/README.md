# Video boundary eval

While a claim is under check, the server asks Jev for each frame: "is this the same video as the reference frame?"
(`server.SAME_Q` in `server.same_video`, called from `read_job`). A wrong answer moves or delays the alert. This eval
measures that decision and proposes a faster and more exact one (`proposal.patch`).

## How to run

```sh
uv run build.py                      # frames to ~/.cache/footnote-bench/boundary/frames/ (not in git), pairs.jsonl
uv run run.py                        # baseline: the server's question at 540 px; before and after the proposal
uv run run.py --variants all --gate  # every question and image size, and the media and private questions
```

- `build.py` draws phone screens with `tools/replay.py` (its TikTok screen, settings, chat and bank screens, 540 px,
  grey, JPEG quality 50), 1 frame per second, from replay.py's video cache. A missing video is downloaded with yt-dlp.
- `run.py` calls `detect.ask_frame` with `server.SAME_Q` and `server.tiktok_gate`: the server's own code and text.
  Raw scores are cached in `~/.cache/footnote-bench/boundary/cache.jsonl`, so a second run is free. `--budget` stops
  new calls at a summed cost. It writes `results.json`.
- `caption.py` is the local check. `proposal.patch` puts the same function in `server.py`.

## The set (185 pairs, 91 same, 94 different)

12 videos: the 4 test videos, 2 talking-head videos of one account, 2 fast-cut videos of one account, 2 talking-head
videos with text of one account, a fast-cut edit and a news report with captions.

| kind | n | truth | what |
|---|---|---|---|
| same_far | 40 | same | reference early in the video, frame now 3 to 30 s later |
| same_cut | 31 | same | the 2 frames around a hard cut inside one video |
| dark_same | 10 | same | a dark scene under the sharp app interface |
| blur_same | 10 | same | the whole screen blurred (a fade or an app switch) |
| diff_other | 30 | different | another account's video |
| diff_same_account | 30 | different | the same account, another video |
| transition | 16 | different | a scroll frame with 2 videos (35 % or 75 % of the way) |
| dark_diff | 6 | different | a dark scene of another video |
| nonvideo | 12 | different | settings, chat or bank app in front |

## Results

Jev (`typesafe/jev-1.14`), latency of the same call, cost from OpenRouter usage.

| variant | acc | false same | false different | p50 | p95 | USD per call |
|---|---|---|---|---|---|---|
| server question, 540 px, t 0.5 (now) | 0.897 | 0.117 | 0.088 | 294 ms | 427 ms | 0.00016 |
| server question, 540 px, best t 0.12 | 0.919 | 0.160 | 0.000 | | | |
| caption-focused question, 540 px, t 0.5 | 0.854 | 0.160 | 0.132 | 283 ms | 379 ms | 0.00016 |
| server question, 360 px, t 0.5 | 0.865 | 0.138 | 0.132 | 258 ms | 325 ms | 0.00008 |
| server question, 270 px | all errors: Jev refuses with "Invalid image dimensions" | | | 199 ms | | |
| **proposal: local check, then server question at 540 px, t 0.15** | **0.919** | **0.160** | **0.000** | 270 ms | 401 ms | 112 of 185 calls |

Per kind, accuracy now and with the proposal:

| kind | now | proposal |
|---|---|---|
| same_far | 0.93 | 1.00 |
| same_cut | 1.00 | 1.00 |
| dark_same | 1.00 | 1.00 |
| blur_same | 0.50 | 1.00 |
| diff_other | 1.00 | 1.00 |
| diff_same_account | 1.00 | 1.00 |
| transition (35 % / 75 %) | 0.31 (0.00 / 0.62) | 0.06 (0.00 / 0.12) |
| dark_diff | 1.00 | 1.00 |
| nonvideo | 1.00 | 1.00 |

- Jev scores another video at most 0.10 and the same video at least 0.17. The threshold 0.5 is too high: it reads a
  cut, a blur or a talking head as a new video (false different 8.8 %), and each costs a GPT-6 Luna read of 5 to 7 s.
  At 0.15 no same pair is lost.
- The only errors left are scroll frames. At 35 % the old caption is still on screen, so "same" is a fair answer; the
  next frame (1 s later) shows the new video in full and Jev then says "different". This costs about 1 s.
- The local check (white text with a shadow in the account and caption box, overlap with the reference) is 0.78 to
  1.00 for same pairs and at most 0.71 for different pairs. At 0.85 it decides 73 of the 91 same pairs with no call
  and makes no error. In a real claim check almost all frames are same-video frames, so most frames need no model.
- The caption-focused wording was worse. 360 px halves the cost and saves 36 ms at p50, but loses accuracy; keep 540.

Media and private questions (`server.tiktok_gate`): settings, chat and bank are media 0.01 and private 0.97 to 1.00
(all correct). Video, dark and scroll frames are all media (0.99) and not private. Blurred frames are mixed: 7 of 10
media, 2 of 10 private.

## The proposal (`proposal.patch`)

1. In `read_job`, before any Jev call: when the local caption check of the frame and the reference frame is 0.85 or
   more, the frame is the same video and public media. No model call: about 5 ms instead of 0.3 s, and no cost (the
   media call, 0.00008 USD, and the same call, 0.00016 USD, are both skipped).
2. Jev's same threshold goes from 0.5 to 0.15 (`SAME_T`, env `FOOTNOTE_SAME_T`).
3. The Dockerfile installs Pillow. Without Pillow the server works as before (every frame goes to Jev).

## Limits

- The frames are drawn by replay.py, not taken on a phone. The local check knows the TikTok For You layout only; on
  another app or a real TikTok screen with another layout it finds no text and Jev decides, as now. It was not tested
  on real phone frames.
- Two videos of one account with almost the same caption (such as "part 1" and "part 2") can pass the local check.
- 185 pairs: the thresholds fit this set. The gap between 0.10 and 0.17 is small.
- The patch was checked on a scratch copy (it applies, it parses, `caption_same` gives the expected answers). It was
  not run in a full server or on the phone.
