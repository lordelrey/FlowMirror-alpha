# runs/

Simulation outputs. One directory per run (`<run_tag>/`) containing a copy of
the run config, `event_log.jsonl` (schema: `config/schemas/event.schema.json`),
`llm_cache.jsonl`, and `snapshots/`. Everything here except this README is
git-ignored. Do not commit raw event logs; export small anonymized samples via
the analysis package (P4) instead. Restore old outputs from the
`*.bak_<timestamp>` files created by `flowmirror.io.backups`.
