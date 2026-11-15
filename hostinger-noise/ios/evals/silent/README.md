# Silent set and latency budget

One harness, two evals for the Footnote server (`../../server/`):

- **A. Silent set.** 17 real public short videos that must give no alert, and 3 known positives as a recall check (`set.jsonl`: URL, kind, why).
- **B. Latency budget.** The seconds per stage, from the timestamps that the server (`/debug.json`) and `replay.py` (`timeline.jsonl`) already keep.

Run on 15 November 2026 against a local copy of `server.py` (server code of commit `ac5c3b63`), Jev by `jevor` on OpenRouter, the checker `footnote-agent` on the VPS.

## Results

### A. Silent set (baseline server)

| | Result |
| --- | --- |
| False alerts | **1 of 17** silent videos: `satire-onion-robot` |
| Recall | **2 of 3** positives: `pos-dougwilliams` missed |
| Claims that passed the gate | 16 in 20 videos; 9 silent videos had none |

Why the passed claims in silent videos stayed silent: stance `support` or `mixed` (6); the speaker question said the video reports or refutes the claim (3: two Lead Stories debunks, PolitiFact quoting Trump); a duplicate of an earlier check (1); one Onion `differ` was held and not delivered in the run (see the miss below: the same cause).

**False alert, `satire-onion-robot`** (The Onion, "SWAT Team Robot Successfully Dismantles Black Man").
The claim "Police evacuated a 2-mile radius due to a gunman" passed Jev (claim 0.95). footnote-agent failed with HTTP 502 "The web search failed", so the single OpenRouter checker answered `differ` with apnews.com and axios.com pages about another event. The speaker question (`ASSERT_Q`) then gave 0.79: it does not know satire. Two causes: the speaker question has no satire case, and the single fallback accepts sources that are not about the claim. footnote-agent returned 502 on 3 of 14 calls in this run.

**Missed positive, `pos-dougwilliams`.** The check gave `differ` with 2 sources at 25 s, and the result was held for the next video. The next video never became confirmed: the screen reader read its account as "Gordon Ramsay" and "Gordon Ramsey" in turns, and a new video needs two reads with the same account (`same_read` compares `handle_key` exactly). Four reads, no confirmation, no delivery.

### B. Latency budget (baseline server, seconds)

| Stage | n | p50 | p95 |
| --- | --- | --- | --- |
| claim spoken (end of its audio chunk) to transcript | 15 | 1.6 | 2.2 |
| transcript to gate passed (sentence buffer and Jev) | 15 | 3.0 | 7.8 |
| gate passed to check done (footnote-agent) | 14 | **7.4** | **17.3** |
| next video on screen to first read of it | 3 | 4.8 | 5.3 |
| first read to second read that confirms the new video | 3 | 7.0 | 7.0 |
| confirmed to delivered (second speaker question, 1 s loop) | 3 | 1.2 | 1.6 |
| **next video on screen to delivered** | 3 | **13.0** | 13.4 |
| claim spoken to delivered | 3 | 40.3 | 66.5 |

The slowest server stage is the check (footnote-agent). The time from the check to the next video is the person watching, not server time.

Language model screen reads (GPT-6 Luna): 44 reads, p50 4.8 s, p95 8.6 s.

**Where the 14 s delivery delay goes.** The held result goes out only when the server confirms a new video. That takes two language model reads of frames with the same new account:
first frame of the new video and the Jev frame gate (about 1 s), the first read (about 5 s; the candidate), the 1 s gap between reads (`VISION_GAP`) and the wait for the next frame, the second read (about 5 to 7 s; the confirmation), then the second speaker question and the 1 s deliver loop (about 1 s). In the smoke run: next video at 0 s, first read done at 7.8 s, second read at 15.8 s, delivered at 16.5 s. In the simulator run (29.3 s to 43.7 s) the same two reads explain the 14 s. The agent's 18 s is not in this gap: the agent had finished before the next video opened.

## Proposals (`proposal.patch`, a diff against `server/server.py`)

1. **Deliver on the first read of the next video** (`due_because`). A frame is read by the language model only when Jev says it is not the claim's video (`same_video` below 0.5). When that read names another account than the claim's, the held result is due ("next video (first read)"). This removes the confirming read from the delivery path, and it also fixes the missed positive, since the account misread no longer blocks delivery. The video identity rules (two reads) stay as they are for everything else.
   Risk: a watermark or a misread account on the claim's own video can deliver early, but only when Jev also says the frame is another video.
2. **Satire in the speaker question** (`ASSERT_Q`): "Also false for satire or parody: a satirical outlet or account (such as The Onion or The Babylon Bee), a comedy sketch, or a fake news report made as a joke."
   Offline, on the saved context of each `differ` check: the Onion claim 0.72 to 0.08; positives 0.99, 0.55 to 0.63, 0.74 (no loss); debunks and the quote stay at or below 0.25.

Before and after, on the local server, the 3 positives and the 5 silent videos with a passed `differ` claim (the claim cache of the baseline run kept):

| | Baseline | With the patch |
| --- | --- | --- |
| Recall on the 3 positives | 2 of 3 | **3 of 3** |
| False alerts in the 5 silent videos | 1 | **0** |
| Next video to delivered, p50 / max (s) | 13.0 / 13.4 | **7.8 / 9.8** |
| Next video to first read, p50 (s) | 4.8 | 6.2 |

Not yet tested with the patch: the other 12 silent videos (none of them had a `differ` claim in the baseline, so the patch cannot change them unless the checker answers differently), a phone or simulator run, and a feed where the claim's video shows a watermark account.

Other findings, no patch:
- footnote-agent returned HTTP 502 ("The web search failed") on 3 of 14 calls. The single fallback then made the false alert. A fallback `differ` could require that a source names the claim's place or person.
- `transcript to gate` p95 is 8 to 15 s: a sentence waits in the buffer for the next chunk or for `FLUSH_SECONDS` (8 s) when the speaker does not end it.
- A second check on the same sentence (a caption or on-screen text claim) is linked to the newest sentence, so its sentence times are not its own. The harness leaves them out of the first two stages.

## How to run

```sh
# 1. A local server copy, never the live one. Keys outside the repo; a fresh cache directory.
mkdir -p "$T/keys" "$T/cache" "$T/wav"                    # T: a temp dir
grep '^AI_ROUTER_API_KEY=' infra/hostinger/secrets.env > "$T/keys/router.env"
grep '^FOOTNOTE_AGENT_KEY=' footnote-agent/.env.hostinger > "$T/keys/agent.env"
cp -r hostinger-noise/ios/server "$T/srv"                  # apply proposal.patch here to test it
(cd "$T/srv" && PORT=8791 LENS_CACHE_DIR="$T/cache" LENS_KEY_FILE="$T/keys/router.env" \
  LENS_AGENT_KEY_FILE="$T/keys/agent.env" LENS_WAV_DIR="$T/wav" python3 server.py) &
# The OpenRouter key comes from ~/.config/footnote/openrouter.env.

# 2. The eval: about 26 minutes for 20 videos, each followed by an 18 s filler video.
python3 hostinger-noise/ios/evals/silent/run.py --raw "$T/raw"
python3 hostinger-noise/ios/evals/silent/run.py --raw "$T/raw" --only pos-dougwilliams   # one case
python3 hostinger-noise/ios/evals/silent/run.py --raw "$T/raw" --report-only            # analyse again
```

`run.py` refuses the live server. Per video it keeps the replay output and that session's `/debug.json` in `--raw`, and writes `report.json` there. A silent video passes with 0 alerts on its own video; a positive needs 1.

Cost: the baseline run of 20 videos used 0.11 USD on OpenRouter by the server's own count (`detect_cost.usd_total`: Jev gate and frame questions, transcription hedges, the single checker). footnote-agent runs on the Hostinger AI Router and is not in that number.
