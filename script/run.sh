#!/usr/bin/env bash
# FlowMirror v7 one-command wrapper: validate the scenario, then run the engine
# through the real entry point. The run config itself is validated by
# `flowmirror run` before the engine starts (one source of truth).
#
# Usage (from the repo root):
#   bash script/run.sh <scenario_dir|scenario.yaml> <run.json> [extra `flowmirror run` flags]
#
# Example:
#   bash script/run.sh path/to/scenario.yaml runs/demo_two_arm.json --replay-check
#
# Exit codes: whatever `flowmirror run` (and the engine) return; validation
# failure aborts before the engine starts (set -e).
set -euo pipefail

if [ "$#" -lt 2 ]; then
    echo "usage: run.sh <scenario_dir|scenario.yaml> <run.json> [flowmirror-run flags...]" >&2
    exit 1
fi

SCENARIO="$1"
RUN_JSON="$2"
shift 2

if [ -d "$SCENARIO" ]; then
    SCENARIO_FILE="$SCENARIO/scenario.yaml"
else
    SCENARIO_FILE="$SCENARIO"
fi

python -m flowmirror.cli validate "$SCENARIO_FILE" --schema scenario

# `flowmirror run` validates RUN_JSON against run.schema.json (exit 1 on
# failure), then hands off to flowmirror.engine.loop and propagates its code.
exec python -m flowmirror.cli run "$RUN_JSON" "$@"
