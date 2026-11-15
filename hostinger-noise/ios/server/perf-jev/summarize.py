#!/usr/bin/env python3
"""A markdown table from runs/*.jsonl: per model, place, questions and mode, the time to the complete JSON (the answer
is usable) and to the end of the stream (what urllib's r.read() waits for), p50 and p95, errors and spend."""
import glob
import json
import os
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, round(p / 100 * (len(xs) - 1)))]


rows = []
for path in sorted(glob.glob(os.path.join(HERE, "runs", "*.jsonl"))):
    recs = [json.loads(l) for l in open(path)]
    calls = [r for r in recs if "summary" not in r]
    if not calls:
        continue
    ok = [r for r in calls if not r.get("error") and r.get("json")]
    first = calls[0]
    j = [r["connect"] + r["json"] for r in ok]
    e = [r["connect"] + r["end"] for r in ok if r.get("end")]
    rows.append((first["model"], first["label"], first["q"], first["mode"], len(calls), len(calls) - len(ok),
                 j, e, sum(r.get("credits") or 0 for r in calls), sum(r.get("cost_usd") or 0 for r in calls),
                 min(r["at"] for r in calls), max(r["at"] for r in calls)))

order = {"jev-latest": 0, "jev-1.14": 1, "jev-1.13": 2, "microsoft-decision-1": 3, "gemini": 4}
rows.sort(key=lambda r: (r[1], order.get(r[0], 9), r[2], r[3]))
f = lambda xs, p: "%.2f" % pct(xs, p) if xs else "-"
print("| Model | From | Questions | Connection | n | Errors | Answer p50 | Answer p95 | Stream end p50 | Stream end p95 | Answer < 1 s | Credits | USD | UTC |")
print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
tot_c = tot_u = 0
for m, where, q, mode, n, err, j, e, cr, usd, t0, t1 in rows:
    tot_c += cr
    tot_u += usd
    print("| %s | %s | %d | %s | %d | %d | %s | %s | %s | %s | %d of %d | %.3f | %.4f | %s-%s |" % (
        m, where, q, mode, n, err, f(j, 50), f(j, 95), f(e, 50), f(e, 95), sum(x < 1 for x in j), len(j), cr, usd, t0[:5], t1[:5]))
print("\nTotal: %.3f router credits, %.4f USD on OpenRouter." % (tot_c, tot_u))
