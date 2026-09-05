# Scenarios (v7)

A scenario is a data plugin bundle validated by
`config/schemas/scenario.schema.json`; a run config (`run.schema.json`) selects
scenario + experimental arms. Four parts plug in per country:

| part | CN -- `cn_xhs_2025q4` (now) | US -- `us_2025` (roadmap) |
|---|---|---|
| population | `persona_grid_v3.json` + `agents_seed2027.json`; reported classes C2-C4 | `data/us/population/*` (TODO) |
| platform / creatives | Xiaohongshu (zh); one shared masked pool `content_pool_v1_masked.jsonl`, filtered per org; 4 orgs (GF, Penghua, Guolian, HTF) | google_ads_web (en); ads pool TODO |
| regulator | `cn_cxr`: suitability checkout; `gate_redemptions=false`, `qdii_limits=true` | `us_regbi` (Reg BI best-interest) |
| market data | `nav_cache.json`, `fund_meta_v1.json`, `cn_trading`, T+1, fees 1.2% / 0.5% | `data/us/funds/*` (TODO) |

Attention source: `guba` weekly signal (CN) vs `none` (US for now, reddit
candidate). External discipline: `flow_panel_v2.json` holdout with lead
placebo quarter `2025Q3` (CN); US holdout TODO. The US skeleton carries
`# TODO` markers on every placeholder path.
