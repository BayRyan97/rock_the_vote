#!/usr/bin/env bash
# Run the model pipeline, stopping at the first failure.
#
#   bash model/run_pipeline.sh                  # everything
#   bash model/run_pipeline.sh --to baseline    # through the CatBoost baseline (minutes)
#   bash model/run_pipeline.sh --from graph     # graph_build onward (hours)
#   bash model/run_pipeline.sh --from etl --to features_history
#   bash model/run_pipeline.sh --list
#
# Stage logs land in model/artifacts/NN_<stage>.log, numbered by position so a
# run reads in order. Extra args after `--` go to every stage (e.g. `-- --quick`).
set -u
cd "$(dirname "$0")/.."
ART=model/artifacts

# name:script — the single place stage order is defined.
STAGES=(
  "etl:model/etl.py"
  "splits:model/splits.py"
  "features_acs:model/features_acs.py"
  "features_history:model/features_history.py"
  "baseline:model/baseline_catboost.py"
  "graph:model/graph_build.py"
  "rwse:model/pe_rwse.py"
  "train:model/train.py"
  "evaluate:model/evaluate.py"
)

names () { for s in "${STAGES[@]}"; do echo "${s%%:*}"; done; }
index_of () {
  local i=0
  for s in "${STAGES[@]}"; do
    [ "${s%%:*}" = "$1" ] && { echo "$i"; return 0; }
    i=$((i + 1))
  done
  echo "unknown stage '$1'; known: $(names | tr '\n' ' ')" >&2
  return 1
}

FROM=0; TO=$((${#STAGES[@]} - 1)); PASS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --list) names; exit 0 ;;
    --from) FROM=$(index_of "$2") || exit 1; shift 2 ;;
    --to)   TO=$(index_of "$2")   || exit 1; shift 2 ;;
    --)     shift; PASS=("$@"); break ;;
    *)      echo "usage: run_pipeline.sh [--from STAGE] [--to STAGE] [--list] [-- ARGS]" >&2; exit 1 ;;
  esac
done
[ "$FROM" -le "$TO" ] || { echo "--from stage comes after --to stage" >&2; exit 1; }

for i in $(seq "$FROM" "$TO"); do
  entry="${STAGES[$i]}"; name="${entry%%:*}"; script="${entry#*:}"
  log=$(printf "%s/%02d_%s.log" "$ART" "$((i + 1))" "$name")
  echo "=== ${name} :: $(date '+%H:%M:%S') ==="
  if ! python -u "$script" ${PASS+"${PASS[@]}"} > "$log" 2>&1; then
    echo "!!! ${name} FAILED (exit $?) — tail of ${log}:"
    tail -25 "$log"
    exit 1
  fi
  echo "    ok ($(date '+%H:%M:%S'))"

  # Surface the leakage guards and headline metrics where they are produced,
  # so a green run is provable rather than assumed.
  case "$name" in
    baseline)
      echo "--- leakage guards ---"
      grep -E "no exact linear recovery|never-voter cohort held out" "$log" \
        || echo "  (guard lines not found — check the log)"
      echo "--- turnout / party metrics ---"
      grep -E "^\[turnout\]|^\[party\]" "$log" ;;
    evaluate)
      echo "--- GTN test metrics ---"
      grep -E "^\[turnout|^\[party" "$log" ;;
  esac
done

echo "=== pipeline complete :: $(date '+%H:%M:%S') ==="
