#!/usr/bin/env python3
"""The hedge latch with fake backends: jev answers in 0.7 s, decision in 0.25 s. 3 slow jev calls (2 s) first, as at
the start of tonight's run. Before the patch, every later call is won by decision (streak never resets). After the patch,
jev wins again once it is fast. Usage: test_hedge_latch.py <dir with detect.py>"""
import importlib.util
import sys
import time

spec = importlib.util.spec_from_file_location("d", sys.argv[1] + "/detect.py")
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
d.HEDGE_SECONDS = 1.5
d.HEDGE_TO = "decision"
d.openrouter_key = lambda: "x"
d.router_key = lambda: "x"
slow = {"n": 3}


def jev(item):
    time.sleep(2.0 if slow["n"] > 0 else 0.7)
    slow["n"] -= 1
    return {"probs": {"claim": 0.9}}, None


d.BACKENDS["jev"] = jev
d.BACKENDS["decision"] = lambda item: (time.sleep(0.25), ({"probs": {"claim": 0.9}}, None))[1]
winners = []
for i in range(10):
    out, err, who = d._run("jev", {"text": str(i)})
    winners.append(who)
    time.sleep(2.2)  # let the losing call finish, as between sentences
print(" ".join(winners))
print("jev wins after the slow start:", winners[3:].count("jev"), "of", len(winners[3:]))
