#!/bin/zsh
# CI03a 本地实测：A 全量（不带 --shard）→ B 两片顺序 → C 两片并发。树在跑的期间不动。
set -u
ROOT=/Volumes/Projects/tavotto-wt/ci-foundation
OUT=/private/tmp/claude-501/-Volumes-Projects-Tavotto/5c9342cf-07b8-423a-b77b-e0640fab1cf4/scratchpad/ci03a/runs
PY=$ROOT/.venv/bin/python
cd $ROOT
echo "HEAD $(git rev-parse HEAD) started $(date -u +%FT%TZ)" > $OUT/driver.log
git status --short >> $OUT/driver.log

echo "== A full $(date -u +%FT%TZ)" >> $OUT/driver.log
/usr/bin/time -l $PY -m pytest --junitxml "$OUT/full.xml" -rs > $OUT/full.log 2> $OUT/full.time; echo "A_EXIT=$?" >> $OUT/driver.log
tail -1 $OUT/full.log >> $OUT/driver.log

for k in 1 2; do
  echo "== B s$k sequential $(date -u +%FT%TZ)" >> $OUT/driver.log
  /usr/bin/time -l $PY -m pytest --shard $k/2 --shard-manifest "$OUT/m$k.json" --junitxml "$OUT/s$k.xml" --durations=50 -rs > $OUT/s$k.log 2> $OUT/s$k.time; echo "B${k}_EXIT=$?" >> $OUT/driver.log
  tail -1 $OUT/s$k.log >> $OUT/driver.log
done

echo "== C concurrent $(date -u +%FT%TZ)" >> $OUT/driver.log
( /usr/bin/time -l $PY -m pytest --shard 1/2 --shard-manifest "$OUT/c1.json" --junitxml "$OUT/c1.xml" --durations=50 -rs > $OUT/c1.log 2> $OUT/c1.time; echo "C1_EXIT=$? $(date -u +%FT%TZ)" >> $OUT/driver.log ) &
P1=$!
( /usr/bin/time -l $PY -m pytest --shard 2/2 --shard-manifest "$OUT/c2.json" --junitxml "$OUT/c2.xml" --durations=50 -rs > $OUT/c2.log 2> $OUT/c2.time; echo "C2_EXIT=$? $(date -u +%FT%TZ)" >> $OUT/driver.log ) &
P2=$!
wait $P1 $P2
tail -1 $OUT/c1.log >> $OUT/driver.log; tail -1 $OUT/c2.log >> $OUT/driver.log
echo "DRIVER_EXIT $(date -u +%FT%TZ)" >> $OUT/driver.log
