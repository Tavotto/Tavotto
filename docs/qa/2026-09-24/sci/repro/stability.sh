#!/usr/bin/env bash
# 新增用例连跑 N 轮（默认 5），任一轮红即退出非零——排除「靠重试才绿」。
HERE="$(cd "$(dirname "$0")" && pwd)"
N="${N:-5}"
rc=0
for i in $(seq 1 "$N"); do
  echo "---- round $i/$N ----"
  bash "$HERE/pytest.sh" tests/test_write_back_real_409.py tests/test_export_anchor_readback.py \
    tests/test_export_version_snapshot.py tests/test_appearance_edits_keep_science.py -q -p no:randomly || rc=1
done
echo "stability rc=$rc"
exit $rc
