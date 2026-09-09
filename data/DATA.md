# Distributed data

This directory contains only inputs needed by the offline examples and tests.

| path | purpose | status |
|---|---|---|
| `population/agents_seed2027.json` | synthetic demo cohort | distributable demo data |
| `population/persona_grid_v3.json` | synthetic population cells | distributable demo data |
| `population/population_10k_v3.json` | synthetic population source | distributable demo data |
| `creatives/cn/content_pool_demo.jsonl` | synthetic posts for four fictional organisations | demonstration only |
| `attention/attention_demo.json` | synthetic weekly discussion signal | demonstration only |
| `funds/nav_demo_2025q4.json` | deterministic synthetic NAV series | demonstration only |
| `funds/fund_meta_demo.json` | empty optional metadata overlay | synthetic placeholder |
| `flows/flow_holdout_demo.json` | empty holdout shape | synthetic placeholder |

Every organisation name, product code, post, engagement count, attention value, and NAV in the demo inputs is fictional or generated. The repository does not distribute raw platform captures, screenshots, creative images, proprietary flow panels, real NAV histories, model-generated experiment outputs, or local database files.

`MANIFEST.sha256` records the distributed files. Generated outputs belong under `runs/out/` and are ignored by Git.
