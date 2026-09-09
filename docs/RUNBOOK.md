# Runbook

## 1. Install

From the repository root:

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
```

Check the installation:

```bash
python -m flowmirror.cli --version
python -m flowmirror.cli schemas
python -m pytest -q
```

## 2. Run offline

Offline demos use bundled inputs, synthetic NAVs, and a deterministic mock model.

```bash
python -m flowmirror.cli demo two-arm
python -m flowmirror.cli demo three-arm
python -m flowmirror.cli demo null
```

Useful overrides:

```bash
python -m flowmirror.cli demo three-arm --agents 21 --days 3 --out runs/out/my_demo
python -m flowmirror.cli demo three-arm --replay-check
```

## 3. Validate and run a configuration

```bash
python -m flowmirror.cli validate runs/demo_two_arm.json --schema run
python -m flowmirror.cli run runs/demo_two_arm.json --out runs/out/custom_demo
```

Run configuration fields are defined in `config/schemas/run.schema.json`; defaults are in `config/engine_defaults.yaml`. Keep output paths under `runs/out/` so generated artifacts remain outside version control.

## 4. Optional provider configuration

Copy `config/api_example.yaml` to `config/api.yaml`. The destination is ignored by Git. Fill only the local copy, or use the environment variables documented by the template.

Before a live run, first validate the file and run the same shape with `mock_llm: true`. Provider calls may incur cost. Stop on authentication or rate-limit failures instead of blindly retrying an entire run.

## 5. Outputs

A run directory can contain:

- `event_log.jsonl`: canonical append-only simulation events.
- `llm_cache.jsonl`: model-response cache used for free replay.
- `run_meta.json`: resolved settings and run status.
- `invariants_report.json`: end-of-run checks.
- `snapshots/`: optional state snapshots.
- `prompts/`: optional exported model input for a selected decision.

Do not edit these files while a run is active. To resume an interrupted run, execute the same configuration and output directory; completed cache entries are reused.

## 6. Replay verification

```bash
python -m flowmirror.cli run path/to/run.json --out runs/out/<tag> --replay-check
```

Run this only after the original run has finished. A successful check reports `replay-check identical=True`.

## 7. Analysis

Analysis modules read a completed run directory. For example:

```bash
python -m flowmirror.analysis.modality runs/out/<tag> --out runs/out/<tag>/analysis/modality.json
python -m flowmirror.analysis.society_metrics runs/out/<tag>
python -m flowmirror.analysis.influence runs/out/<tag>
```

Use multiple run directories for across-run summaries where supported. Treat a single run as descriptive.

## 8. Browser replay

Start the localhost service:

```bash
python web/server.py --port 8765
```

Open `http://127.0.0.1:8765/web/`. To export a completed run into a portable viewer bundle:

```bash
python -m flowmirror.cli export-bundle runs/out/<tag>
```

Creative images are not included. If you own a compatible image directory, keep it outside the repository and pass it explicitly with `--images-root`.

## 9. Troubleshooting

- `missing input`: check every path in the run JSON relative to the repository root.
- `no live credentials configured`: use an offline demo or create the ignored local API configuration.
- `PermissionError` on an output file: close programs that hold the file and rerun the same configuration; cache append-open uses a short bounded retry.
- replay mismatch: preserve the run directory and inspect the logs; do not delete the cache or original event log.
- browser cannot load a run: export a bundle or start `web/server.py` from the repository root.

## 10. Development checks

```bash
python -m pytest -q
python -m flowmirror.agents.runtime --self-test
python -m flowmirror.engine.loop --self-test
```

Tests must use temporary output directories. Credentials and generated run data must never be committed.
