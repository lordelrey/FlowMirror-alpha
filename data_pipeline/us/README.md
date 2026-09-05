# data_pipeline/us (roadmap, P2+)

US counterpart for `scenarios/us_2025`. Everything here is TODO until the US
data agreements are in place:

- `01_persona_grid.py` -- US persona grid (TODO mapping to US investor survey)
- `02_ads_pool.py` -- masked web-ad creatives pool (TODO licensing check)
- `03_fetch_nav.py` -- US fund NAV cache (TODO source)
- `04_flow_panel.py` -- US flow holdout (TODO provider)
- attention: optional reddit-derived signal (TODO; scenario currently `none`)

Same rules as the CN pipeline: no L0 in the repo, checksums into
`data/MANIFEST.sha256`, images never redistributed.
