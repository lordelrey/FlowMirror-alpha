# RUNBOOK (placeholder)

TODO(P3): end-to-end run instructions.

- Prepare `data/` -- see `data/DATA.md` and `script/fetch_data.sh`.
- Write a run config -- start from `tests/fixtures/run_mock_10x3.json`.
- Validate: `flowmirror validate <run.json> --schema run`.
- Launch: `bash script/run.sh <scenario_dir> <run.json>`.
- Read outputs: `runs/<run_tag>/event_log.jsonl`
  (schema: `config/schemas/event.schema.json`).
- Restore an overwritten output: `flowmirror.io.backups.backup_existing`.
