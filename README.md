# FlowMirror

**An open, config-driven sandbox of a fund market living inside a social feed.**
A few thousand investor agents -- each a live, vision-LLM persona -- browse the
same real marketing creatives (text *and* images) that retail investors saw on
the platform, post and comment, and make subscribe/hold/redeem decisions priced
at real fund NAVs. A CSRC-style suitability checkout can be switched on and off
as a randomized experimental factor, and the simulated market is disciplined
externally against real fund-flow panels. FlowMirror is a research instrument
for studying how distribution shapes retail fund flows; it is not a forecast of
any real market, and this pre-alpha contains no results.

## Status

Scaffold (M0): schemas, configs, scenarios and the CLI are in place. The
engine lands in milestone P3 (`bash script/run.sh ...` currently validates the
inputs and reports `engine not wired yet (P3)`).

## Layout

```
config/          api_example.yaml, engine_defaults.yaml, schemas/
scenarios/       cn_xhs_2025q4 (live values), us_2025 (roadmap skeleton)
data/            L1 derived tables + DATA.md + MANIFEST.sha256
data_pipeline/   cn/, us/ -- L0 -> L1/L2 build scripts (P2)
flowmirror/      package: config + io live; population..engine..analysis planned
script/          run.sh, fetch_data.sh
tests/           unit tests (schemas, io) + fixtures
docs/            PERSONA.md, SCENARIOS.md, RUNBOOK.md
runs/            simulation outputs (git-ignored)
legacy/          frozen pre-v7 artifacts, reference only
```

## Install

Python 3.10+:

```bash
pip install -e .            # runtime (jsonschema, PyYAML)
pip install -e ".[dev]"     # + pytest
pip install -e ".[images]"  # optional image handling (Pillow)
```

## Quick start

```bash
cp config/api_example.yaml config/api.yaml   # fill in your own key; git-ignored
flowmirror tree
flowmirror schemas
flowmirror validate tests/fixtures/run_mock_10x3.json --schema run
flowmirror validate scenarios/cn_xhs_2025q4/scenario.yaml   # schema auto-detected
bash script/run.sh scenarios/cn_xhs_2025q4 tests/fixtures/run_mock_10x3.json
```

The bundled fixture runs with `mock_llm: true`, fully offline; no API key is
needed for validation or for the tests.

## Data policy (summary)

- **L0** raw captures (posts, screenshots, crawler dumps) never enter the repo or releases.
- **L1** small derived tables live in `data/` and are committed.
- **L2** larger artifacts live on Hugging Face / Zenodo; checksums in `data/MANIFEST.sha256`.
- Creative **images are never redistributed**; only metadata and derived text.

Full inventory: [data/DATA.md](data/DATA.md).

## Scenarios

| scenario | status | platform | regulator |
|---|---|---|---|
| `cn_xhs_2025q4` | now | Xiaohongshu (zh), 4 fund companies | `cn_cxr` suitability checkout |
| `us_2025` | roadmap | web ads (en) | `us_regbi` (Reg BI) |

## Citing

TODO: a DOI and citation will be added with the first tagged release
(see `CITATION.cff`).

## License

MIT -- see [LICENSE](LICENSE). Copyright (c) 2026 FlowMirror authors.
