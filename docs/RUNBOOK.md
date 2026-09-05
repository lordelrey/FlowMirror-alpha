# FlowMirror v7 RUNBOOK

End-to-end instructions for installing, running and inspecting simulations.
Everything in sections 0-1 works on a fresh `git clone` with no API key and no
real market data. All console output is ASCII; the CLI forces UTF-8 on its own
streams, so CJK content in configs/logs still prints correctly.

## 0. Install (once)

```
git clone <repo-url> flowmirror_v7
cd flowmirror_v7
python -m venv .venv
.venv\Scripts\activate          # Windows  (source .venv/bin/activate on macOS/Linux)
pip install -e .
python -m pytest -q             # must be all green
```

## 1. Quickstart: a full simulation in two commands

```
python data_pipeline/cn/make_demo_nav.py --pool data/creatives/cn/content_pool_v1_masked.jsonl --out data/funds/nav_demo_2025q4.json
flowmirror demo two-arm
```

`make_demo_nav.py` writes a deterministic **synthetic** NAV file
(`_meta.synthetic: true` - offline demos only, never for empirical claims).
`flowmirror demo two-arm` validates the config, runs 40 agents for 5 trading
days with the mock LLM (zero API calls), and prints where the outputs landed
plus what to look at next. Alternatives:

```
flowmirror demo three-arm   # 3 arms + fees, 60 agents
flowmirror demo null        # agent_policy "null": zero LLM calls by design
```

(`python -m flowmirror.cli ...` works anywhere `flowmirror ...` does.)

## 2. Running your own configs

Primary entry point:

```
flowmirror run <run.json> [--mock] [--days N] [--agents N] [--seed N]
                         [--out DIR] [--replay-check] [--dump-prompt AGENT@DAY|first]
```

`run` first validates the config against `config/schemas/run.schema.json`
(exit 1 with a readable error on failure), then calls the engine
(`flowmirror.engine.loop.main`) and propagates its exit code; it adds no logic
of its own. The engine can also be driven directly:

```
python -m flowmirror.engine.loop <run.json> [same flags]
python -m flowmirror.engine.loop --self-test
```

| flag | meaning |
|------|---------|
| `--mock` | force `mock_llm: true` (no provider calls) |
| `--days N` | override `window.max_trading_days` |
| `--agents N` | override `n_agents` |
| `--seed N` | override the run seed |
| `--out DIR` | override `out_dir`; relative paths resolve against the repo root; the LLM cache moves to `<out>/llm_cache.jsonl`, so an `--out` override never silently reuses another run's cached responses |
| `--replay-check` | run twice; require a byte-identical `event_log.jsonl` (prints `replay-check identical=True`; exit 3 on mismatch) |
| `--dump-prompt ...` | see section 4 |

Exit codes: `0` success; `1` config validation/usage failure; `2` argument
error; `3` engine failure or replay mismatch.

Bash wrapper doing validate-then-run in one step (from the repo root):

```
bash script/run.sh <scenario_dir|scenario.yaml> <run.json> [extra flowmirror-run flags]
```

## 3. Research configs vs demo configs

* `runs/mock_10x3.json`, `runs/mock_10x3_3arm.json`, `runs/mock_10x3_null.json`
  are the research-grade configs. They read the real NAV cache
  `data/funds/nav_cache.json`, which is **git-ignored** (4.4 MB, third-party);
  fetch it per `data/DATA.md` before using them.
* `runs/demo_two_arm.json`, `runs/demo_three_arm.json`, `runs/demo_null.json`
  (each carries a `_what` note) point at the synthetic NAV file from section 1
  and run anywhere, offline. Demonstration only, not research.

Validate any config (schema is auto-detected for `scenario.yaml`):

```
flowmirror validate runs/demo_two_arm.json --schema run
flowmirror validate <scenario.yaml> --schema scenario
```

`mock_options` is required only when `mock_llm` is true; the legacy
`arm_level` / `p_image` keys are still accepted as aliases of
`modality_level` / `modality_arms`.

## 4. Exporting the exact prompt an agent saw

```
flowmirror run runs/mock_10x3.json --mock --days 5 --agents 40 --out runs/out/prompt_demo --dump-prompt first
flowmirror run runs/mock_10x3.json --mock --days 5 --agents 40 --out runs/out/prompt_demo --dump-prompt A007@3
```

This writes `<out>/prompts/<agent_id>_d<day>.txt` containing the exact
rendered system+user text (image parts appear as
`[image: <sha256 prefix>, <bytes> bytes]`, never base64), plus a small JSON
sidecar with `prompt_sha`, `arm`, the card ids shown and the channel shas.
It is a side artifact only: the event log and every hash are unchanged, so
`--replay-check` output stays byte-identical whether or not the flag is used.

## 5. Live runs (real provider)

Credentials are resolved in this order (first hit wins):

1. `config/api.yaml` - flat shape; copy the template `config/api_example.yaml`
   and fill in `endpoint`, `api_key`, `vision_model`, `text_model`;
2. env var `FLOWMIRROR_GLM_KEY` (the key only; endpoint/models from defaults);
3. env var `FLOWMIRROR_LEGACY_KEY_FILE` pointing at a file that contains the
   key (opt-in convenience; no path is baked into the repo).

If none of these is present, a live run stops immediately with a message
naming all three options. Keys are never printed or logged. Responses are
cached in `<out>/llm_cache.jsonl` (rows: `key/parsed/provenance/raw/ts`), so
re-running the same config is free and deterministic. Transient provider
errors (timeouts, non-200s) are retried on the frozen schedule (one initial
attempt + at most 4 retries); only after the final attempt does a row land
with `parsed: null` and the run continues.

Start small: `flowmirror run <run.json> --days 5 --agents 20 --out runs/out/live_probe`.

## 6. Outputs

`<out_dir>/` contains:

* `event_log.jsonl` - one JSON object per row; every row validates against
  `config/schemas/event.schema.json`. This file **is** the simulation.
* `llm_cache.jsonl` - provider responses (`key/parsed/provenance/raw/ts`).
* `prompts/` - only when `--dump-prompt` is used (section 4).
* run metadata / counters used by `--replay-check`.

To restore an overwritten output directory, see
`flowmirror.io.backups.backup_existing`.

## 7. Determinism rules (apply to any code you add)

* Seeds come only from `flowmirror.io.hashing.rng_seed_from` (or sha256);
  never Python's built-in `hash()`.
* Sort before iterating sets (and before logging dict order) whenever the
  order can reach an output file.
* Same config + same cache state => byte-identical `event_log.jsonl`
  (`--replay-check` proves it; a mismatch exits 3).

## 8. Troubleshooting

| symptom | fix |
|---------|-----|
| `[FATAL] missing input nav_cache: ...nav_cache.json` | research config needs the real NAV cache (section 3) |
| `...nav_demo_2025q4.json` missing | rerun the `make_demo_nav.py` command from section 1 |
| `[flowmirror] FAIL ... (run): ...` | config error; the message names the problem; check `config/schemas/run.schema.json` |
| `exit 3` after `--replay-check` | replay mismatch: compare the two `replay-check sha*=` lines; nondeterminism was introduced somewhere |
| provider 401/403 or immediate credential error | credentials resolution failed; see section 5 |
| garbled CJK on the Windows console | `chcp 65001` (the CLI already forces UTF-8 on its own streams) |

## 9. Reference commands

```
flowmirror schemas        # list bundled schemas
flowmirror tree           # real package map, generated from the installed code
flowmirror validate <f>   # validate any config against its schema
```
