# FlowMirror

**An open, config-driven sandbox of a fund market living inside a social feed.**

Hundreds of investor agents -- each a live LLM persona drawn from a survey-anchored
population -- browse the same real marketing creatives that retail investors saw on a
content platform, like and comment on them, and subscribe to or redeem funds priced at
real net asset values. A CSRC-style suitability checkout sits inside the action space and
can be switched on and off as a randomized experimental factor. Every displayed social
signal is lagged by one day, every run replays byte-identically from its own cache, and a
rule-based null simulator ships alongside the LLM agents as a control.

FlowMirror is a research instrument for studying how distribution shapes retail fund
flows. It is not a forecast of any real market, and **this pre-alpha contains no results**.

## Try it in a browser

Run `python web/server.py` for the local server, or `python -m http.server 8765 --bind 127.0.0.1`
and open `http://127.0.0.1:8765/web/?run=<run tag>` for read-only replay of an existing
run; the GitHub Pages site carries one sample run that ships with the repository. That
sample's card copy is redacted real public marketing notes with institution names
anonymized -- it exists to demonstrate the interface and must not be used for any
analysis or redistribution. See section 14 of `docs/RUNBOOK.md` for the full walkthrough.

## What is different about it

| | |
|---|---|
| **Image-grounded input** | Agents read real marketing posts, not synthetic prompts. Three modality arms -- `T` text only, `TC` the image rendered as text (OCR plus a frozen neutral caption), `TV` the real image -- are randomized at agent, run or exposure level. |
| **Regulation in the action space** | A suitability checkout compares the investor's assessed class against the product's risk level and returns `match`, `confirm_signed`, `confirm_declined` or `purchase_blocked`. Subscriptions are gated; redemptions never are. The counterfactual outcome is logged next to the real one on every checkout. |
| **Fund mechanics** | Real NAVs, cost basis and reference points, unrealized profit and loss, optional subscription and redemption fees, and a wealth identity checked every day. |
| **Auditability first** | Named invariants checked every run, a frozen event schema, content-addressed prompt caching, and a `--replay-check` that reruns the whole simulation from cache and compares hashes. |

## Status

The engine is complete and its dry-run milestone passes: invariants hold, replays are
byte-identical, and the shipped demos run offline with zero API calls. A live smoke run
against a real provider has been executed. No experimental results exist yet, and none
are claimed anywhere in this repository.

## Install

Python 3.10+:

```bash
pip install -e .            # runtime (jsonschema, PyYAML, requests)
pip install -e ".[dev]"     # + pytest
```

Everything also works without installing, from the repository root, by substituting
`python -m flowmirror.cli ...` for `flowmirror ...`.

## Quick start

Two commands, fully offline, no API key:

```bash
python data_pipeline/cn/make_demo_nav.py --pool data/creatives/cn/content_pool_v1_masked.jsonl --out data/funds/nav_demo_2025q4.json
flowmirror demo two-arm
```

The first writes a deterministic **synthetic** NAV file, clearly marked as such, so that a
fresh clone can run without third-party market data. The second runs 40 agents for five
trading days with a mock model and prints where the outputs landed and what to read next.

```bash
flowmirror demo three-arm   # three modality arms, fees on
flowmirror demo null        # rule-based null policy: no LLM at all, by design
```

[docs/RUNBOOK.md](docs/RUNBOOK.md) is the full manual: running your own configs, live runs
against a provider, exporting the exact prompt an agent saw, reading the outputs, and
troubleshooting.

## What a run produces

`runs/out/<tag>/event_log.jsonl` **is** the simulation -- one JSON object per row, every
row validating against `config/schemas/event.schema.json`:

| row | meaning |
|---|---|
| `post` | an institution published a creative that day |
| `imp` | a card was shown to an agent, with its modality arm and feed slot |
| `dec` | one agent-day decision: prompt hash, engagement counts, mood, stated reason |
| `click` | an agent opened a product from a card |
| `co` | a suitability checkout, with the real and the counterfactual outcome |
| `act` | an executed subscription, redemption or plan instalment |
| `cmt` | a comment with its stance |
| `clim` | the comment climate shown the following day |
| `st` | a familiarity state transition |
| `refl` | a periodic reflection |

Alongside it: `llm_cache.jsonl` (content-addressed responses, which is what makes replay
free), `invariants_report.json`, run metadata, and `prompts/` when `--dump-prompt` is used.

`python -m flowmirror.analysis.modality <run_dir> [<run_dir> ...]` compares the modality
arms, reporting agent-level bootstrap intervals within a run and seed-level intervals
across runs. A single run is reported as descriptive only.

## Layout

```
config/          schemas/ (run, scenario, persona, event, fund_meta), engine_defaults.yaml, api_example.yaml
scenarios/       cn_xhs_2025q4 (live values), us_2025 (roadmap skeleton)
data/            L1 derived tables + DATA.md + MANIFEST.sha256
data_pipeline/   L0 -> L1 build scripts, the frozen captioner, the synthetic NAV generator
flowmirror/      core/ agents/ channels/ platform/ society/ regulator/ engine/ analysis/ population/ io/
script/          run.sh, fetch_data.sh
tests/           unit tests + fixtures
docs/            RUNBOOK, ARCHITECTURE, LAYERS, PERSONA, SCENARIOS, DECISIONS, research notes
runs/            shipped demo and research configs; outputs under runs/out/ are git-ignored
```

`flowmirror tree` prints the real package map, generated from the installed code.

## Scenarios

| scenario | status | platform | regulator |
|---|---|---|---|
| `cn_xhs_2025q4` | now | Xiaohongshu (zh), fund companies | `cn_cxr` suitability checkout |
| `us_2025` | roadmap | web ads (en) | `us_regbi` (Reg BI) |

## Data policy

- **L0** raw captures (crawler dumps, screenshots, full images) never enter this
  repository or any release.
- **L1** derived tables ship here with hashes in `data/MANIFEST.sha256`. They carry no
  author, account, location or device fields.
- Creative **images are never redistributed**; only metadata and derived text.
- Fund NAV history is third-party data and is not redistributed. The shipped
  `nav_demo_2025q4.json` is synthetic and marked `_meta.synthetic: true`.
- Credentials live in `config/api.yaml`, which is git-ignored. See `config/api_example.yaml`.

Full inventory: [data/DATA.md](data/DATA.md).

## Limits worth stating up front

The population's joint distribution is synthetic and anchored on published marginals. The
creative pool covers a small number of institutions over unequal time spans. Agent
behaviour is a property of one model, one prompt and one population. Nothing here should
be read as evidence about real investors, real marketing effectiveness, or the merits of
any regulation.

## Citing

A DOI and citation entry will be added with the first tagged release (see `CITATION.cff`).

## License

MIT -- see [LICENSE](LICENSE). Copyright (c) 2026 FlowMirror authors.
