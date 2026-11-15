# Footnote server performance

The goal: a median under 3 s from the end of a sentence to its note, and a p95 under 6 s. The replay tool measures the delay (`delay_s` in `summary.json`: from the end of the chunk that ends the sentence to the note in `/api/latest`). Change one thing at a time and keep what wins.

## Changes, end to end

The English Kyiv video (`uv run replay.py "https://www.tiktok.com/@sonyaturova120/video/7704085270265744672"`, 2 min, 3 sentences with fear, uncertainty and doubt) on the live server, unless the row says otherwise. Cost per minute is router credits plus OpenRouter USD for one session.

| Date (UTC) | Change | Notes / unsure | Delay median | Delay p95 | Transcript latency median | Cost per minute | Quality |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 14 Nov 18:30 | Baseline (ed468777): chunk fragments, Gemini Decision | 6 / 9 (coordinator run) | 5.77 s | 18.09 s | - | - | fud over-fires on fragments |
| 14 Nov 19:40 | Baseline again (ed468777), OpenRouter at 402 | 0 / 5 | 4.77 s | 6.01 s | - | - | no notes: detector errors |
| 14 Nov 20:20 | Sentence buffer (56108f5f), router LLM as fallback | 2 / 0 | 10.04 s | 14.23 s | 11.1 s | - | 1 correct, 1 false note |
| 14 Nov 21:05 | Local smoke run: jev, hedge to OpenRouter (detector 1.5 s, transcription 2.5 s, screen 4 s) | 0 / 1 | 9.12 s (n=1) | - | 8.67 s | - | 9 of 9 smoke checks |
| 14 Nov 21:03 | Live (d43d4238): jev primary, hedged to OpenRouter. Smoke run | 0 / 2 | 6.20 s | 7.02 s | 4.42 s | - | 9 of 9 smoke checks |
| 14 Nov 21:06 | Live (d43d4238), Kyiv video. The Hostinger router took 15 to 30 s, so the hedge answered 106 of 107 transcriptions, all 108 screen reads and all 86 detections (Gemini Decision) | 0 / 6 | 3.61 s | 4.90 s | 3.21 s | about 0.065 USD OpenRouter, 0 router credits | gold lines: 13 and 14 unsure (fud 0.57 to 0.64), 01 missed (0.42); 3 other unsure (fud 0.55 to 0.65); no false note |

## Detector backends on `testdata/war_sentences.jsonl`

50 sentences (30 with a gold technique, 20 negatives; 10 per language plus negatives), run inside the live container on 14 November, 4 workers, no fallback, no hedge. "Correct" is the gold technique as a note at 0.7. Cost is per 1000 sentences.

| Backend | Model | Correct notes | False notes | en | ru | uk | Median | p95 | Cost per 1000 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| decision | google/gemini-decision-1 (OpenRouter) | 27/30 (top 29) | 0/20 | 10/10 | 9/10 | 8/10 | 0.22 s | 0.31 s | 0.28 USD |
| jev | jev-latest | 27/30 (top 30) | 0/20 | 10/10 | 8/10 | 9/10 | 5.19 s | 12.63 s | 13.4 credits |
| jev | jev-1.14 | 27/30 (top 30) | 0/20 | 10/10 | 8/10 | 9/10 | 4.12 s | 9.96 s | 14.5 credits |
| msdecision | microsoft-decision-1 | 27/30 (top 29) | 0/20 | 10/10 | 9/10 | 8/10 | 3.75 s | 11.50 s | 11.6 credits |
| router | gemini-3.5-flash-lite, JSON | 27/30 (top 27) | 0/20 | 9/10 | 9/10 | 9/10 | 4.94 s | 12.67 s | 126.6 credits |

- One call at a time (12 sentences in a row, from the VPS): jev median 1.05 s (0.95 to 1.34 s), msdecision 1.17 s (1.09 to 1.42 s). The long latencies above come from 4 parallel calls on a loaded router.
- `testdata/real_videos.jsonl` (23 lines of real videos: 15 English Kyiv sentences with 3 gold fear and doubt lines, 8 Russian lines without a technique): jev 1 false note and 2 false unsure; msdecision 3 false notes (two on the Russian lines) and 2 false unsure. Neither finds the 3 gold lines as a note (jev has 1 as unsure). So the default is `jev`.
- Calibration (deciles of all technique probabilities): jev 65 % below 0.1, 7 % at 0.9 or above, the rest spread out; msdecision 71 % and 9 %, also spread. Both are graded, not saturated. The router LLM is saturated: 92 % below 0.1 and 7 % at 0.9 or above, nothing between, so it never gives `unsure`.
- An image part in `state` is accepted by both Hostinger decision models, but an all-white test image got 0.65 and 0.47 for "is this image white", and the input token count fits the base64 text: they read text only.

## Router latency on 14 November (evening)

A trivial chat call ("Say ok.") on the Hostinger AI Router took 4 to 34 s for every model (gemini-3.5-flash-lite 10 to 31 s, mimo-v2.6-flash 6 to 9 s), from the VPS and from a laptop; jev on System One took 3 to 16 s. The same call on OpenRouter took 0.5 to 1.2 s, and Gemini Decision 0.5 s. That is why each slow call is hedged to OpenRouter.

## Transcription models

12 real clips (4 English and 4 Russian with TikTok captions as the reference, 4 Ukrainian with a whole-video router transcript as the reference), each cut in three 4 s chunks, run from the VPS with the language hint. WER and CER are against a 12 s reference window, so chunk edges add errors to every model alike: compare the rows, not the absolute values. The router was loaded during this run, so the latency shows the load more than the model.

| Model | Median | p95 | Errors of 36 | en WER | ru WER | uk WER | uk CER | Credits for 36 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gemini-3.5-flash-lite (live) | 13.8 s | 23.5 s | 4 | 0.44 | 0.46 | 0.68 | 0.49 | 0.37 |
| gemini-3.1-flash-lite | 10.8 s | 21.2 s | 3 | 0.47 | 0.37 | 0.61 | 0.44 | 0.31 |
| gemini-3-flash-preview | 12.7 s | 23.3 s | 1 | 0.46 | 0.32 | 0.68 | 0.50 | 4.76 |
| OpenRouter google/gemini-3.5-flash-lite (from a laptop) | 1.00 s | 1.51 s | 0 | 0.44 | 0.38 | 0.70 | 0.50 | 0.0035 USD |
| OpenRouter google/gemini-3.1-flash-lite (from a laptop) | 0.94 s | 1.66 s | 0 | 0.40 | 0.33 | 0.59 | 0.43 | 0.0035 USD |
| mimo-v2.6-flash | 21.4 s | 22.5 s | 32 (timeouts) | - | - | - | - | 0.03 |
| whisper-1, whisper-1-eu | - | - | 36 (HTTP 403, a Cloudflare challenge page, also with a custom User-Agent and from the VPS) | - | - | - | - | 0 |

Kept: gemini-3.1-flash-lite for transcription, on the Hostinger router and as the hedge on OpenRouter. The screen read stays on gemini-3.5-flash-lite (one change at a time).

## Which backend answers in production

On 14 November from 21:00 to 21:45 UTC, `/api/info` showed hedge calls 333, hedged 333 and second_won 333: Gemini Decision on OpenRouter gave every detector answer, and jev gave none. The reason is the Hostinger router's latency, not the endpoint or the hedge time. One call at a time from the VPS at 21:40: jev with the 12 questions took 4.6 to 12.3 s (4 of 6 timed out at 12 s), msdecision 4.7 to 12.3 s (3 of 6 timed out), and a jev call with 1 question 2.3 to 27.8 s. The same item on Gemini Decision took 0.21 to 0.49 s. A chat call on the router took 20 to 29 s at the same time. At 20:50 the same jev call took 0.95 to 1.34 s, so the hedge time of 1.5 s is right for a normal router. Transcription and screen reads are the same: the hedge on OpenRouter answered 106 of 107 transcriptions and all 108 screen reads in the Kyiv run. Later (perf-jev/, 12626d69): the router sends the JSON early but ends the stream 1 to 9 s later, and urllib waited for the end; after 3 lost races the hedge started at once and stayed there. detect.py now reads until the JSON parses and resets the streak when jev answers in time, and the hedge time is 1.0 s (Jev p50 from the VPS 2.98 s to 0.97 s). To measure the Hostinger models alone, set `{"hedgeSeconds": 0, "transcribeHedge": 0, "visionHedge": 0}` with `POST /api/detector`.

## Claim lane (fact checks)

Since 15 November the claim lane is the product, and the 10 technique questions are off (spec 2026-11-15.1). Each sentence gets one decision call with 4 gates: war, quote, claim and public (public affairs: politics, war, health, money or news).

Claim gate (Gemini Decision) on the 78 lines of the two test files plus the demo claims: 17 of 78 pass at 0.7. Opinions and questions score below 0.2. The demo claims: "The mansion is worth $20 million." with context 0.74; "Ukraine bought mansions with US aid" 0.90; Zelenskyy's 100-year UK deal 0.97; NATO admits 0.94; rare earths 0.58. So the live threshold is 0.7, and "deliver now" takes a claim from 0.5.

### Hermes Agent against the single web-search call

10 claims from the demo and bench videos, 6 in parallel, from a laptop on 14 November at 22:30 UTC. Hermes: our own container `footnote-hermes` (hvps-hermes-agent v0.23.1, claude-haiku-5.5 on the Hostinger AI Router, web search through Oxylabs, at most 8 turns). Single: google/gemini-3.1-flash-lite on OpenRouter with the web plugin (exa, 3 results).

| Claim | Hermes | Single |
| --- | --- | --- |
| Zelenskyy owns a $20 million mansion in Florida | timeout at 120 s (an earlier run: 56 s, differ, Reuters and DW) | 3.0 s, differ (Reuters, TCPalm, Euronews) |
| Zelenskyy's wife spent $1.1 million at Cartier | 52 s, differ | 3.6 s, differ |
| Ukrainian officials bought mansions with US aid | 51 s, differ | 5.1 s, differ |
| The UK signed a 100-year deal with Ukraine | 14 s, support | 5.2 s, support |
| Ukraine gives its rare earths to the US | 40 s, mixed | 5.3 s, support |
| NATO admits it lost the war | 54 s, none | 4.6 s, differ |
| Ukraine extended martial law | 52 s, support | 3.3 s, support |
| Zelenskyy's wife bought a Bugatti | 47 s, differ | 5.2 s, differ |
| 40 missiles hit in one night | 57 s, mixed | 6.2 s, differ (doubtful; a markdown link in the line, now stripped) |
| Ukraine banned the Orthodox church | 22 s, differ | 1.6 s, differ |

| Checker | Median | Range | Cost per check | Sources |
| --- | --- | --- | --- | --- |
| single (OpenRouter, exa) | 4.9 s | 1.6 to 6.2 s | about 0.008 USD | article URLs; fact-checkers when they exist |
| Hermes (router, Oxylabs) | about 52 s | 14 s to over 120 s | 6k to 22k router tokens | article URLs; Google redirect links are dropped |

- The two agree on 7 of 10 stances. Hermes is more careful (mixed or none where the single call says differ or support), and 10 times slower.
- Press to result decides the Action button: with the single call a press on a new claim has the result in 2 to 6 s; with Hermes in 14 to 120 s. So `pressChecker` is `single`. The automatic lane runs both in parallel: the first usable answer is published and Hermes replaces it only when it is better sourced (`autoChecker: "hermes"` means both).
- A stored check answers a press in under 0.1 s. Stored checks are on disk and match transcript variants (75 % of the content words, or a string ratio of 0.85).

Limits: 90 s per check, 2 checks per video play, 30 new checks per hour for the server (at most about 0.25 USD an hour on the single call). Kill switch: `"factCheck": false` in `POST /api/detector` (all sessions) or in a session's `POST /api/config`.

Earlier tests of the single call: native Google search instead of exa took 2.5 s and about 0.028 USD and gave redirect links; gpt-4o-eu on the Hostinger router with `web_search_options` took 23.5 s and ignored the option.
