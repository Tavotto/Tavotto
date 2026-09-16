#!/bin/zsh
set -e
cd /Volumes/Projects/tavotto-wt/ci-foundation
E=docs/implementation/ci-foundation/evidence
SP=/private/tmp/claude-501/-Volumes-Projects-Tavotto/5c9342cf-07b8-423a-b77b-e0640fab1cf4/scratchpad/ci05
mkdir -p $E/ci05/timing
run_group() {
  local name=$1 wf=$2 kinds=$3; shift 3
  local d=$SP/subset_$name
  rm -rf $d; mkdir -p $d/runs $d/jobs
  for r in "$@"; do
    cp $E/ci05/actions/runs/run_${r}*.json $d/runs/
    cp $E/ci05/actions/jobs/jobs_${r}*.raw.json $d/jobs/
  done
  .venv/bin/python scripts/ci/ci_baseline.py analyze --compact --workflow $wf --evidence $d --edge-kinds $kinds --out $E/ci05/timing/timing_${name}.json
  echo "rc=$? group=$name"
}
run_group ci_8b95256c tests/fixtures/ci_baseline/ci_8b95256c.yml $E/dag_edge_kinds.json 34993304390 34957615294 34970490865 34990070055 35002647792 35015416419 35027644355
run_group ci_79c5aa38 $E/ci05/workflows/ci_79c5aa38.yml $E/ci01/dag_edge_kinds_after.json 34994534095
run_group ci_d9e8dd72 $E/ci05/workflows/ci_d9e8dd72.yml $E/ci01/dag_edge_kinds_after.json 35004450721 35007730894
run_group ci_35b912a0 $E/ci05/workflows/ci_35b912a0.yml $E/ci01/dag_edge_kinds_after.json 35011613925
run_group ci_1f7f13e8 $E/ci05/workflows/ci_1f7f13e8.yml $E/ci01/dag_edge_kinds_after.json 35024490379 35028309531
run_group ci_162f54c6 $E/ci05/workflows/ci_162f54c6.yml $E/ci01/dag_edge_kinds_after.json 35031461863
run_group ci_bb27bdaf $E/ci05/workflows/ci_bb27bdaf.yml $E/ci01/dag_edge_kinds_after.json 35031790918 35031800904
