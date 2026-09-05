# legacy/

Frozen artifacts from pre-v7 iterations (v5/v6 prototypes), kept for
traceability only. Nothing here is imported by the `flowmirror` package, and
this directory will not receive updates. New work belongs in the packages
listed by `flowmirror tree`. Do not cite legacy code in new results.

## Reference implementations (research repo only)

The modules below remain in the research repository (`fundmarket-sim`)
as frozen reference implementations. The `flowmirror` package does NOT
import them at runtime; unit tests read them only to pin the package
copies byte-for-byte (guarded by the `FLOWMIRROR_RESEARCH_ROOT`
environment variable, skipped with a reason when the tree is absent).

- `sim/engine_v5.py` -- the C x R suitability rule (`cxr_outcome`),
  `classify_fund`, and the fund/market wrappers. Mirrored by
  `flowmirror/regulator/cn_cxr.py`; equivalence pinned by
  `tests/unit/test_cn_cxr.py`.
- `sim/elicit_base.py` -- the elicitation baseline; kept as reference
  only, not migrated in phase P2.

Migrated in P2 (originals untouched in the research repo):
`sim/feed.py` -> `flowmirror/channels/feed.py` (pinned by
`tests/unit/test_feed_migration.py`) and `sim/select_agents.py` ->
`flowmirror/population/sampler.py` (frozen-cohort pin in
`tests/unit/test_sampler.py`).
