# Eval of footnote-agent

This eval checks the stance and the sources of `footnote-agent` (`POST /api/check`). It does not check the Jev gate or the server.

## Set

`set.jsonl` holds 60 claims: 30 false and 30 true. Each claim is written as a speaker says it in a video. Each one has a published verdict from a fact-checker or a primary source (`verdict_by`, `verdict_url`).

The `kind` field marks the hard kinds:

- `old`: an old claim, such as "Obama was born in Kenya".
- `wrong-number`: the core is true, but the number is wrong, such as "Finland has ten million saunas" (about 3 million).
- `sounds-false`: a true claim that sounds false, such as "Oxford is older than the Aztec Empire".
- `recent`: a claim from 2024 to 2026, such as "The US just lost its measles elimination status" (13 November 2026).
- `non-english`: a claim that started outside English, said in English, such as the Sanna Marin four-day-week story.
- `myth`, `plain`: common myths and plain facts.

Some verdict URLs answer 403 to scripts (Britannica, Smithsonian, CDC). They open in a browser.

## Run

```sh
cd footnote-agent
set -a; . ./.env.hostinger; . ~/.config/footnote/openrouter.env; set +a
npx vp dev --port 3017 --strictPort --host 127.0.0.1 > /tmp/agent.log 2>&1 &

cd ../hostinger-noise/ios/evals/agent
python3 run.py --tag mytag --agent-log /tmp/agent.log   # local agent, exact cost from its log
python3 run.py --live --tag live-now                    # the deployed agent
python3 run.py --tag mytag --report                     # report from the cache, no calls
python3 run.py --only t03,f11 --tag probe               # some claims only
```

- The script reads `FOOTNOTE_AGENT_KEY` from `footnote-agent/.env.hostinger`. It never prints a key.
- Raw answers go to `cache/<tag>/<id>.json`. Delete a file to call that claim again. Fetched source pages go to `cache/urls.json`. `cache/` is not in git.
- Cost: the local agent logs the exact OpenRouter cost of each call (`usage.cost`). `--agent-log` adds those lines. Without it, the script uses the change in OpenRouter key usage. That number lags by minutes, and other users of the same key change it, so use it only as a rough value.
- A full run of 60 claims costs about 0.45 USD and takes about 1.5 minutes with 4 workers.

## Metrics

- **False alert rate**: the share of true claims that get `differ`. The app alerts only on `differ`, so this is the most important number.
- **Recall**: the share of false claims that get `differ`.
- **Two domains**: the share of results that have sources on 2 or more registrable domains.
- **Opens**: the share of source URLs that answer HTTP 200 to a script. Bot blocks (401, 403, 429) are counted apart.
- **About the claim**: the source opens, and the page text, the title or the URL contains at least 3 (or 40%) of the claim's content words. This is a word-overlap heuristic, not a judgement.
- **Real site**: the `site` field is a domain name that matches the URL host.
- **Latency**: the `ms` field of the answer, p50 and p95.

## Results (15 November 2026)

Before: the live agent (commit `561428ac`), all 60 claims. After: the local agent with the fixes, 40 claims (all 30 true claims and 10 hard false claims), because of the 1 USD budget.

| Metric | Before, 60 | Before, same 40 | After, 40 |
| --- | --- | --- | --- |
| False alert rate | 0% (0/30) | 0% (0/30) | 3% (1/30) |
| Recall on false claims | 90% (27/30) | 100% (10/10) | 80% (8/10) |
| Errors ("The web search failed") | 10 of 60 (17%) | 7 of 40 | 0 of 40 |
| Two or more domains | 96% | 94% | 98% |
| Sources that open (200) | 76% (bot-blocked 21%) | 70% | 71% (bot-blocked 25%) |
| Open and about the claim | 72% | 68% | 68% |
| `site` is the real domain | 100% | 100% | 100% |
| Latency p50 / p95 | 6.8 / 8.6 s | 7.4 / 8.6 s | 4.0 / 5.0 s |
| Cost per check | about 0.0074 USD | | 0.0074 USD (exact) |

Read the "after" column with care:

- The 40-claim "after" run used a stricter stance prompt. That prompt moved two wrong-number claims (f11 Eiffel Tower 500 m, f12 US debt 100 trillion) from `differ` to `mixed`, and it did not stop the one false alert. I reverted that prompt. The committed code has the original stance prompt. Run the eval again on the committed code before deploy.
- The committed fix (reasoning effort and token limit) was checked on the 10 claims that failed before: 0 errors, 3.0 to 6.8 s, and t03 answered `differ` there too.
- Before, 7 of the 30 true claims failed with an error. The false alert rate of 0% before is on 23 answers, not 30. t03 was one of the failed claims.

## Findings

1. **Errors came from the OpenRouter call.** The call sent `max_tokens: 600` and no reasoning effort. GPT-6 Luna spent the tokens on reasoning. The answer was then empty, or the JSON was cut off, and the check failed with "The web search failed". The fix sends `reasoning: { effort: AGENT_REASONING (default "none"), exclude: true }` and `max_tokens: 1200`. Errors went from 17% to 0, and latency went from 6.8 s to 4.0 s (p50), because the model no longer reasons first.
2. **The one false alert, t03** ("honey found in Egyptian tombs was still edible"). The agent found an American Bee Journal article and a 1975 paper by Leek that say the tomb contents could not be identified as honey. Smithsonian repeats the story. The verdict source of the set (Smithsonian) supports the claim, but the evidence is in fact contested. This item is a weak label more than an agent defect. A better label is "contested", or drop it.
3. **The `site` defect is not in footnote-agent.** In the agent, `site` is always the URL host (100% here). The label "Fact check" for a usatoday.com link comes from `site_of()` in `hostinger-noise/ios/server/server.py`: it takes the last part of the title after " - ", so "… - Fact check" becomes "Fact check". That code runs on the server's own fallback path (`checker: single`). The fix belongs in the server: use the domain, or a name map per domain.
4. **A cut line.** The line of t03 was "Economic Times and Egyptologist F." The first-sentence split broke after an initial. The split now skips a single capital letter and a dot.
5. **Sources.** About 1 in 4 source URLs refuse a script (403). They are real pages (Britannica, Smithsonian, Japan Times). Very few sources answer 404.
