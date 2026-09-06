"""Simulation engine: world construction, the daily loop, and the append-only,
replay-checkable event log behind every run.

Entry points:
  python -m flowmirror.engine.loop <config.json> [--mock --days N --agents N
      --out runs/out/<tag> --replay-check]   -- run the simulator (see --help)
  python -m flowmirror.engine.world --self-test   -- engine self-tests

Per-run artefacts (event log, invariants_report.json, run_meta.json, ...)
land under runs/out/<tag>/; --replay-check re-executes the run and asserts
the event log is byte-identical, so every reported number stays auditable
against its recorded event stream.
"""
