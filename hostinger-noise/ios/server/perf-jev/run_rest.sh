#!/usr/bin/env bash
# The shorter second part of the matrix (the router was slow, so the full run_matrix.sh took too long):
# jev-1.14, jev-1.13 and microsoft-decision-1, 1 and 13 questions, cold, 10 calls each. Then the before and after of
# detect._post (compare_post.py) for jev-latest, 1 and 13 questions.
set -u
cd "$(dirname "$0")"
WHERE=$1; N=${2:-10}
for m in jev-1.14 jev-1.13 microsoft-decision-1; do for q in 1 13; do
  python3 bench.py --model $m --questions $q --n $N --mode cold --label $WHERE --out runs/$WHERE-$m-q$q-cold.jsonl | grep SUMMARY
done; done
python3 compare_post.py .. "${PATCHED:-../patched}" 15 1 jev-latest > runs/$WHERE-compare-post-q1.log 2>&1; tail -1 runs/$WHERE-compare-post-q1.log
python3 compare_post.py .. "${PATCHED:-../patched}" 10 13 jev-latest > runs/$WHERE-compare-post-q13.log 2>&1; tail -1 runs/$WHERE-compare-post-q13.log
echo done
