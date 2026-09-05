# data_pipeline/cn (P2)

Scripts that turn L0 raw captures into the L1/L2 artifacts of the CN scenario
(`scenarios/cn_xhs_2025q4`). Planned port from the v6 toolchain:

- `01_crawl_guba.py` -- weekly sentiment counts -> `data/attention/guba_signal_v1.json`
- `02_mask_creatives.py` -- post/ad metadata masking -> `data/creatives/cn/content_pool_v1_masked.jsonl` (L2)
- `03_build_persona_grid.py` -> `data/population/persona_grid_v3.json`
- `04_sample_cohort.py` -> `data/population/agents_seed2027.json` (seed 2027)
- `05_fetch_nav.py` -> `data/funds/nav_cache.json` (never uploaded)
- `06_flow_panel.py` -> `data/flows/flow_panel_v2.json` (restricted L2)

Rules: L0 inputs never leave the machine that collected them; every emitted
file gets a row in `data/MANIFEST.sha256`; images are referenced but never
copied. See `data/DATA.md`.
