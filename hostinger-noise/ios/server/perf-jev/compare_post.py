#!/usr/bin/env python3
"""Before and after for detect._post on the real Jev endpoint: the original detect.py (r.read() waits for the end of
the stream) and a patched copy (_read_json returns when the JSON is complete). Calls alternate, so both see the same
router load. Usage: compare_post.py <dir with the original detect.py> <dir with the patched detect.py> [n] [questions]"""
import importlib.util
import json
import statistics
import sys
import time


def load(name, d):
    spec = importlib.util.spec_from_file_location(name, d + "/detect.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


before, after = load("before", sys.argv[1]), load("after", sys.argv[2])
n = int(sys.argv[3]) if len(sys.argv) > 3 else 20
nq = int(sys.argv[4]) if len(sys.argv) > 4 else 1
model = sys.argv[5] if len(sys.argv) > 5 else "jev-latest"
item = {"text": "On 3 March 2024 the parliament voted 312 to 41 to extend martial law for another 90 days.",
        "before": "The host talks about the war in Ukraine and the new mobilisation law."}
qs = {"claim": before.QUESTIONS["claim"]} if nq == 1 else before.QUESTIONS
payload = {"model": model, "state": before.context_lines(item), "questions": qs}
times = {"before": [], "after": []}
errors = {"before": 0, "after": 0}
for i in range(n):
    for name, m in (("before", before), ("after", after)) if i % 2 == 0 else (("after", after), ("before", before)):
        t = time.perf_counter()
        data, err = m._post(m.SYSTEMONE_URL, payload, m.router_key(), 12)
        dt = time.perf_counter() - t
        if err:
            errors[name] += 1
        else:
            times[name].append(dt)
        print(json.dumps({"i": i, "v": name, "s": round(dt, 3), "err": (err or "")[:80], "at": time.strftime("%H:%M:%SZ", time.gmtime())}), flush=True)
        time.sleep(0.3)
out = {"model": model, "q": nq, "n": n}
for name, xs in times.items():
    xs.sort()
    out[name] = {"p50": round(statistics.median(xs), 3) if xs else None, "p95": round(xs[min(len(xs) - 1, round(0.95 * (len(xs) - 1)))], 3) if xs else None,
                 "under_1s": sum(x < 1 for x in xs), "under_1_5s": sum(x < 1.5 for x in xs), "errors": errors[name]}
print("SUMMARY " + json.dumps(out), flush=True)
