# Architecture

FlowMirror is a local simulation application with a Python backend and a browser replay frontend. A run is fully described by a validated JSON configuration and a set of local input files.

## Execution flow

```text
run config
    |
    v
schema validation --> world loader --> daily simulation loop
                                      |
                     +----------------+----------------+
                     |                |                |
                 platform          agents          regulator
                 ranking       perceive/decide      checkout
                     |                |                |
                     +----------------+----------------+
                                      |
                                      v
                              append-only event log
                                      |
                           +----------+----------+
                           |                     |
                        analysis             web replay
```

## Main packages

- `flowmirror.engine` owns world construction, the daily loop, state transitions, accounting, and event emission.
- `flowmirror.agents` builds model inputs, calls an optional provider, parses decisions, and maintains the append-only response cache.
- `flowmirror.channels` implements feed ranking, modality assignment, social signals, and lag rules.
- `flowmirror.regulator` implements suitability outcomes independently from the agent's requested action.
- `flowmirror.analysis` reads completed event logs without mutating them.
- `web/` is a localhost-only companion service and static replay interface.

## Determinism and replay

Random streams are derived from stable SHA-256-based seeds. Agent calls are keyed by model, temperature, schema version, prompt digest, and image digests. When a cache is complete, `--replay-check` runs the simulation again without provider calls and compares the reconstructed event log with the original.

## Time and ownership rules

The platform exposes only information available at the current simulated time. Social counters and comments shown on day `t` come from closed state at or before day `t-1`. Agents propose actions; the game master applies suitability, balance, settlement, and accounting rules, then writes the canonical event log.

## Local application boundary

The web server binds to `127.0.0.1`. Credentials are loaded by the backend from local configuration or environment variables and are never accepted from the browser request body. Generated outputs remain under `runs/out/` unless the user explicitly exports a replay bundle.
