#!/usr/bin/env bash
# The latency matrix: each model, 1 and 13 questions, cold (new connection per call, as detect.py today) and warm
# (keep-alive). Each call records both the time to the complete JSON and the time to the end of the stream.
# Usage: run_matrix.sh <where> [n]   (where: mac or vps; the output goes to runs/<where>-...jsonl)
set -u
cd "$(dirname "$0")"
WHERE=$1; N=${2:-20}
mkdir -p runs
router() {
  for m in jev-latest jev-1.14 jev-1.13 microsoft-decision-1; do
    for q in 1 13; do for mode in cold warm; do
      python3 bench.py --model $m --questions $q --n $N --mode $mode --label $WHERE --out runs/$WHERE-$m-q$q-$mode.jsonl | grep SUMMARY
    done; done
  done
}
gemini() {
  for q in 1 13; do for mode in cold warm; do
    python3 bench.py --model gemini --questions $q --n $N --mode $mode --label $WHERE --out runs/$WHERE-gemini-q$q-$mode.jsonl | grep SUMMARY
  done; done
}
router > runs/$WHERE-router.log 2>&1 &
gemini > runs/$WHERE-gemini.log 2>&1 &
wait
echo done
