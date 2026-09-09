# Demo data utility

`make_demo_nav.py` builds the deterministic synthetic NAV file used by the offline examples:

```bash
python data_pipeline/cn/make_demo_nav.py \
  --pool data/creatives/cn/content_pool_demo.jsonl \
  --out data/funds/nav_demo_2025q4.json
```

It does not fetch market data or make model calls. Raw-data import, annotation, database, and private experiment pipelines are intentionally outside the public repository.
