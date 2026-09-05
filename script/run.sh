#!/usr/bin/env bash
# FlowMirror v7 one-command run wrapper (engine lands in P3).
# Usage: run.sh <scenario_dir> <run.json>
set -euo pipefail

if [ "$#" -ne 2 ]; then
    echo "usage: run.sh <scenario_dir> <run.json>" >&2
    exit 1
fi

SCENARIO="$1"
RUN_JSON="$2"

if [ -d "$SCENARIO" ]; then
    SCENARIO_FILE="$SCENARIO/scenario.yaml"
else
    SCENARIO_FILE="$SCENARIO"
fi

python -m flowmirror.cli validate "$SCENARIO_FILE" --schema scenario
python -m flowmirror.cli validate "$RUN_JSON" --schema run

echo "engine not wired yet (P3)"
exit 0
