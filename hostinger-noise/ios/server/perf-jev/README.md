# Why Jev on the Hostinger AI Router is slow for Footnote

Measured on 14 November 2026, 22:36 to 22:53 UTC, from inside the Hostinger VPS (Frankfurt) and from a Mac in Norway.

## Root cause

1. **The router ends each response stream 1 to 9 s after the answer, and `detect.py` waits for that end.** The router sends the complete JSON answer in one chunk. Then it holds the stream open and sends the last chunk (HTTP/1.1 `0` chunk, or HTTP/2 END_STREAM) much later: p50 1.3 s, p95 3.2 s, max 8.8 s from the VPS. `urllib`'s `r.read()` returns only at the end of the stream. The chat endpoint does the same: a 5-token `gemini-3.5-flash-lite` answer arrived at 4.6 s, and its stream ended at 12.5 s. So the cause is a step in the router after the answer, probably usage or billing accounting, not Jev. This also fits the 503 "Billing unavailable" errors after 31 s that another agent saw.
2. **The hedge latches.** After the second call wins 3 times in a row, `_run` waits only 0.3 s for the first backend. Only a win by the first backend resets the streak. Jev on the router never answers in 0.3 + 0.2 s, so after 3 slow calls Jev cannot win again until the process restarts. `/api/info` showed `streak 437` with `calls 437`. After tonight's redeploy it showed `calls 3, hedged 3, second_won 3, streak 3`: the server latched again on its first 3 calls.

Result: with today's code, Jev finished within the 1.5 s hedge in 1 of 80 calls from the VPS. If the client reads only up to the end of the JSON, 54 % of the calls finish within 1.5 s and 26 % within 1.0 s.

The router's own latency is also high tonight. Its edge answers in 70 ms (`/v1/models`, a 403 for an unknown model, a 401 for a bad key). But a real Jev answer takes p50 1.1 to 1.8 s from the VPS. TypeSafe says direct calls take 70 to 500 ms, from its service on the US West Coast. We have no TypeSafe key, so we could not measure Jev without the router. The extra time is behind the router edge: the router's processing, the trip to the US and Jev itself. Batch size does not change this. With 1 question and with 13 questions, the p50 is the same within the noise. The TypeSafe docs say the same: "Adding questions barely changes the response time."

Connection reuse does not matter from the VPS. A new TCP and TLS connection to the router (Cloudflare FRA) takes 11 ms. HTTP/2 shows the same tail as HTTP/1.1 (curl). The response headers carry no rate-limit or queue information. The Hostinger status page shows "All Systems Operational" and has no AI Router component.

## Measurements

`Answer` is the time to the complete JSON: when the answer is usable. `Stream end` is the time until the stream ends: what `detect.py` waits for today. Both include the connection setup. `cold` uses a new connection per call, as `detect.py` does today. `warm` uses one keep-alive connection.

| Model | From | Questions | Connection | n | Errors | Answer p50 | Answer p95 | Stream end p50 | Stream end p95 | Answer < 1 s | Credits | USD | UTC |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| jev-latest | mac | 1 | cold | 20 | 0 | 2.08 | 4.33 | 4.11 | 14.61 | 3 of 20 | 0.056 | 0.0000 | 22:42-22:44 |
| jev-latest | mac | 13 | cold | 10 | 0 | 1.56 | 3.18 | 3.17 | 8.24 | 2 of 10 | 0.146 | 0.0000 | 22:44-22:45 |
| gemini | mac | 1 | cold | 20 | 0 | 0.27 | 0.37 | 0.27 | 0.37 | 20 of 20 | 0.000 | 0.0007 | 22:44-22:44 |
| jev-latest | vps | 1 | cold | 20 | 0 | 1.12 | 2.99 | 2.87 | 4.98 | 7 of 20 | 0.056 | 0.0000 | 22:40-22:41 |
| jev-latest | vps | 1 | warm | 20 | 0 | 1.77 | 2.46 | 2.52 | 4.29 | 3 of 20 | 0.056 | 0.0000 | 22:41-22:42 |
| jev-latest | vps | 13 | cold | 20 | 0 | 1.59 | 5.22 | 3.62 | 10.97 | 4 of 20 | 0.291 | 0.0000 | 22:42-22:43 |
| jev-latest | vps | 13 | warm | 20 | 0 | 1.09 | 3.91 | 3.14 | 6.39 | 7 of 20 | 0.291 | 0.0000 | 22:43-22:45 |
| jev-1.14 | vps | 1 | cold | 10 | 0 | 1.12 | 2.08 | 2.04 | 3.45 | 2 of 10 | 0.029 | 0.0000 | 22:46-22:46 |
| jev-1.14 | vps | 13 | cold | 10 | 0 | 1.72 | 4.98 | 3.52 | 7.01 | 0 of 10 | 0.157 | 0.0000 | 22:46-22:47 |
| jev-1.13 | vps | 1 | cold | 10 | 0 | 1.13 | 2.92 | 3.00 | 5.39 | 3 of 10 | 0.029 | 0.0000 | 22:47-22:47 |
| jev-1.13 | vps | 13 | cold | 10 | 0 | 1.12 | 2.04 | 2.45 | 4.34 | 3 of 10 | 0.157 | 0.0000 | 22:47-22:48 |
| microsoft-decision-1 | vps | 1 | cold | 10 | 0 | 1.83 | 13.81 | 2.66 | 15.55 | 1 of 10 | 0.015 | 0.0000 | 22:48-22:48 |
| microsoft-decision-1 | vps | 13 | cold | 10 | 0 | 1.52 | 27.40 | 3.90 | 34.12 | 2 of 10 | 0.128 | 0.0000 | 22:49-22:50 |
| gemini | vps | 1 | cold | 20 | 0 | 0.19 | 0.23 | 0.19 | 0.23 | 20 of 20 | 0.000 | 0.0007 | 22:40-22:40 |
| gemini | vps | 1 | warm | 20 | 0 | 0.17 | 0.31 | 0.17 | 0.31 | 20 of 20 | 0.000 | 0.0007 | 22:40-22:40 |
| gemini | vps | 13 | cold | 20 | 0 | 0.23 | 0.46 | 0.23 | 0.46 | 20 of 20 | 0.000 | 0.0061 | 22:40-22:40 |
| gemini | vps | 13 | warm | 20 | 0 | 0.20 | 0.48 | 0.20 | 0.48 | 20 of 20 | 0.000 | 0.0061 | 22:40-22:40 |

Total: 1.413 router credits, 0.0144 USD on OpenRouter.

Before and after on the real `detect._post` (`compare_post.py`). The original and the patched `detect.py` alternate on the same item, from the VPS:

| Questions | n per version | Before p50 | Before p95 | Before under 1.5 s | After p50 | After p95 | After under 1 s | After under 1.5 s | Errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 (claim) | 15 | 2.98 s | 4.54 s | 1 of 15 | 0.97 s | 2.49 s | 8 of 15 | 10 of 15 | 0 |
| 13 (all) | 10 | 2.87 s | 5.18 s | 0 of 10 | 1.00 s | 3.58 s | 5 of 10 | 9 of 10 | 0 |

The logs are `runs/vps-compare-post-q1.log` and `runs/vps-compare-post-q13.log` (22:50 to 22:53 UTC).

## Fix

`fix.patch` (applies with `git apply` to `hostinger-noise/ios/server/`):

1. `detect._read_json(r)`: read with `read1()` and return as soon as the body parses as JSON. `_post` and `server._router_chat` use it. That covers Jev, microsoft-decision-1, the router LLM, transcription and screen reads. Closing the connection with the stream unread does no harm, because urllib opens a new connection for each call anyway.
2. The hedge latch: when the first backend answers within `HEDGE_SECONDS`, also after it lost the race, the streak goes to 0. So the next call waits the full hedge time again. `test_hedge_latch.py` uses fake backends (jev 2 s for 3 calls, then 0.7 s; decision 0.25 s). Before the patch, jev won 0 of the 7 calls after the slow start. After the patch, it won 6 of 7.
3. Set `FOOTNOTE_HEDGE_SECONDS=1.0` (an environment variable, no code change). The worst case is then about 1.2 s (1.0 s plus Gemini Decision's 0.2 s), inside the 1 to 2 s goal. Tonight Jev wins about a third of the calls, and more on a normal router. 1.5 s gives Jev about 60 % of the calls tonight, with a worst case of about 1.7 s.

## Files

- `bench.py`: one model, n calls, the time to connect, headers, complete JSON and stream end. Standard library only.
- `run_matrix.sh`, `run_rest.sh`: the runs in `runs/`. The router was slow, so the second part has 10 calls per model instead of 20.
- `compare_post.py`: before and after on the real `detect._post`.
- `test_hedge_latch.py`: the hedge latch with fake backends.
- `summarize.py`: the table above, from `runs/*.jsonl`.
- `fix.patch`: the patch for `detect.py` and `server.py`.

## Recommendation for the three model roles

| Role | Setup | Latency | Cost | Risk |
| --- | --- | --- | --- | --- |
| a. Instant gate ("is this a claim worth checking") | Jev first, hedged to Gemini Decision after 1.0 s, with `fix.patch`. `/api/info` `second_won` shows the share Jev answers. | Worst case about 1.2 s. Jev answers about a third of the calls tonight, and gives 0.97 s p50 alone. | Jev 0.0028 credits (1 question) or 0.0146 credits (13 questions) per sentence. Gemini Decision 0.00004 USD (1 question) or 0.0003 USD (13 questions) per call it makes. | The router's tail is high tonight: Jev p95 2.5 to 5 s, microsoft-decision-1 p95 14 to 27 s. Without the hedge Jev does not meet 1 s. |
| b. Transcription and screen reading | Keep the router models, hedged to OpenRouter (2.5 s and 4 s), and apply the `server.py` part of the patch. | The router chat took 1 to 8 s to the answer tonight ("Say ok."), plus a stream tail of up to 20 s, which the patch removes. OpenRouter takes about 1 s. | Router 0.01 credits per transcription. OpenRouter 0.0001 USD per call. | The router LLM is the slow part tonight. Expect OpenRouter to answer most of these until the router recovers. |
| c. Fact check with web search | Keep the single OpenRouter call with the exa web plugin, in the background lane. Let Jev's claim answer decide whether a check runs: TypeSafe's "screen with a Noul first" pattern. | 1.5 to 3.9 s (PERF.md). A 1 to 3 s Jev answer is fine in this lane. | About 0.008 USD per check. | The Hostinger router has no web search (`web_search_options` is ignored), and Hermes Agent has no API. So no Hostinger path exists for this step today. |

The honest pitch: Jev on the Hostinger router decides whether a sentence is a claim and whether a fact check is worth it. Gemini Decision answers only when Jev is late, and `second_won` counts those cases. Do not say that Jev answers every gate in under 1 s. On this router tonight it does not.

## Spend

About 1.8 router credits (bench 1.41, compare_post about 0.38, curl probes about 0.03) and 0.015 USD on OpenRouter. The router's balance is not visible through the router API (`/v1/credits`, `/v1/balance`, `/v1/usage` return 404) or through the Hostinger API. Every call tonight returned HTTP 200.

## Not verified

- Jev without the router (`api.typesafe.ai`): we have no TypeSafe key. So we cannot split the 1 s between the router and Jev.
- The cause of the stream tail inside the router (billing or usage accounting is our guess) and of the 503 "Billing unavailable". The status page shows no AI Router component.
- The patch inside the live server: it is tested with `compare_post.py` (the real `_post`) and `test_hedge_latch.py` (the real `_run` with fake backends), not as a deploy.
- The credit balance in hPanel.
