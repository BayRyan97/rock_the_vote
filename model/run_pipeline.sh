#!/usr/bin/env bash
# Run the model pipeline, stopping at the first failure.
#
#   bash model/run_pipeline.sh                  # everything
#   bash model/run_pipeline.sh --to baseline    # through the CatBoost baseline (minutes)
#   bash model/run_pipeline.sh --from graph     # graph_build onward (hours)
#   bash model/run_pipeline.sh --from etl --to features_history
#   bash model/run_pipeline.sh --quick          # smoke run (few iterations)
#   bash model/run_pipeline.sh --list
#
# Stage logs land in <config.ARTIFACTS>/NN_<stage>.log, numbered by position so a
# run reads in order. `--quick` is routed only to the stages that accept it.
# Anything after `--` is passed to EVERY stage, so it must be an option they all
# understand -- there is no such option today beyond what argparse gives free.
set -u
cd "$(dirname "$0")/.."
# Single source of truth: config decides where artifacts live (outside the
# OneDrive-synced repo), so ask it rather than duplicating the path here.
ART=$(python -c "import sys; sys.path.insert(0,'model'); import config; print(config.ARTIFACTS)") || {
  echo "could not resolve config.ARTIFACTS" >&2; exit 1; }
# Log redirection below is evaluated by the shell BEFORE python runs, so etl.py's
# own mkdir cannot help: without this, every stage fails on the redirect.
mkdir -p "$ART"

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
  "graph_serve:model/graph_build.py --serve"
  "score_gtn:model/score_gtn.py"
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

# Stages whose argparse defines --quick.
QUICK_STAGES=" baseline train "

FROM=0; TO=$((${#STAGES[@]} - 1)); PASS=(); QUICK=""
while [ $# -gt 0 ]; do
  case "$1" in
    --list)  names; exit 0 ;;
    --quick) QUICK="--quick"; shift ;;
    --from)  FROM=$(index_of "$2") || exit 1; shift 2 ;;
    --to)    TO=$(index_of "$2")   || exit 1; shift 2 ;;
    --)      shift; PASS=("$@"); break ;;
    *)       echo "usage: run_pipeline.sh [--from STAGE] [--to STAGE] [--quick] [--list] [-- ARGS]" >&2; exit 1 ;;
  esac
done
[ "$FROM" -le "$TO" ] || { echo "--from stage comes after --to stage" >&2; exit 1; }

for i in $(seq "$FROM" "$TO"); do
  entry="${STAGES[$i]}"; name="${entry%%:*}"; cmd="${entry#*:}"
  # a stage may carry fixed arguments (e.g. graph_build --serve)
  read -r script stage_args <<< "$cmd"
  log=$(printf "%s/%02d_%s.log" "$ART" "$((i + 1))" "$name")
  echo "=== ${name} :: $(date '+%H:%M:%S') ==="
  args=()
  case "$QUICK_STAGES" in *" $name "*) [ -n "$QUICK" ] && args+=("$QUICK") ;; esac
  [ -n "${stage_args:-}" ] && args+=($stage_args)
  args+=(${PASS+"${PASS[@]}"})
  python -u "$script" ${args+"${args[@]}"} > "$log" 2>&1 || {
    rc=$?   # `|| {}` does not invert, so this is python's status, not the test's
    echo "!!! ${name} FAILED (exit $rc) — tail of ${log}:"
    tail -25 "$log"
    exit 1
  }
  echo "    ok ($(date '+%H:%M:%S'))"

  # Surface the leakage guards and headline metrics where they are produced,
  # so a green run is provable rather than assumed.
  case "$name" in
    baseline)
      echo "--- leakage guards ---"
      grep -E "no linear recovery|never-voter cohort held out|constant across all|no constant features" "$log" \
        || echo "  (guard lines not found — check the log)"
      echo "--- turnout / party metrics ---"
      grep -E "^\[turnout\]|^\[party\]" "$log" ;;
    evaluate)
      echo "--- GTN test metrics ---"
      grep -E "^\[turnout|^\[party" "$log" ;;
  esac
done

echo "=== pipeline complete :: $(date '+%H:%M:%S') ==="
