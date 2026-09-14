# FlowMirror

FlowMirror is a local, config-driven fund-market social simulation sandbox. It combines a Python simulation engine, optional LLM investor agents, fund suitability rules, an append-only event log, analysis modules, and a browser-based replay interface.

This repository is source software, not a hosted trading service. Clone it to a local machine, run an offline demo or provide your own model credentials and inputs, then inspect the resulting simulation in the local web interface. It does not place real orders, forecast prices, or provide investment advice.

[中文说明](README_zh.md)

## What is included

- A Python CLI and simulation engine under `flowmirror/`.
- Deterministic mock and rule-based policies that run without an API key.
- Optional text and vision LLM execution through local configuration.
- Social-feed, memory, suitability, fund-accounting, and institutional-policy components.
- JSON schemas, example configurations, tests, and a local replay viewer.
- A small synthetic demo dataset. Generated run outputs and credentials stay outside version control.
- A step-by-step social-feed sandbox with private agent views, public comments, and voluntary following.
- Simulated order, fill, and settlement accounting, institution publication policies, and a macro market observer.
- Optional read-only local data adapters and an offline temporal evaluation workbench.

## Install

Python 3.10 or newer is required.

```bash
git clone https://github.com/lordelrey/FlowMirror-alpha.git
cd FlowMirror-alpha
python -m venv .venv
python -m pip install -e ".[dev]"
```

Activate the virtual environment if desired, or keep invoking its Python executable directly.

## Run an offline demo

The demos use a deterministic mock model and synthetic NAV series, so they require no API key and make no network calls.

```bash
python -m flowmirror.cli demo two-arm
python -m flowmirror.cli demo three-arm --replay-check
python -m flowmirror.cli demo null
```

Outputs are written under `runs/out/` and are ignored by Git.

## Open the local application

```bash
python web/server.py --port 8765
```

Open `http://127.0.0.1:8765/web/`. The server binds only to localhost. It can display the bundled sample, replay a completed local run, and launch supported local configurations.

### Social and market observer

```bash
python web/community_server.py --port 8793
```

Open `http://127.0.0.1:8793/community.html` for the social feed or
`http://127.0.0.1:8793/market.html` for the macro observer. This separate server is
read-only: opening either page never launches an experiment or calls a model.
Without saved runs, the community page offers a fictional, scripted demonstration;
the market page requires a saved browsing run.

To create a small offline example with institution publication, browsing, and
simulated accounts:

```bash
python -m flowmirror.platform.browse_cli --marketing-demo --out runs/browse_out/marketing_demo
python -m flowmirror.platform.browse_cli --replay --out runs/browse_out/marketing_demo
```

Select `marketing_demo` in the observer. It uses synthetic inputs and preset rules,
not autonomous LLM behavior. See [Sandbox guide](docs/SANDBOX.md) for browsing,
accounting, macro views, optional local data, and capability boundaries.

## Run with an LLM

Copy `config/api_example.yaml` to the ignored file `config/api.yaml`, add credentials locally, and prepare a run JSON that validates against `config/schemas/run.schema.json`.

```bash
python -m flowmirror.cli validate path/to/run.json --schema run
python -m flowmirror.cli run path/to/run.json
```

Never commit `config/api.yaml`, model caches, event logs, or raw source data.

## Main outputs

Each run writes an append-only `event_log.jsonl`, a response cache, run metadata, and invariant results. Event rows cover publication, impression, decision, click, suitability checkout, trade, comment, social climate, state transition, and reflection events. A completed run can be exported for the browser viewer:

```bash
python -m flowmirror.cli export-bundle runs/out/<tag>
```

## Repository layout

```text
flowmirror/      simulation engine, agents, channels, regulation, and analysis
config/          defaults, API template, and JSON schemas
runs/            offline demo configurations; generated outputs are ignored
data/            distributable demo inputs only
scenarios/       example scenario bundles
web/             localhost server and replay interface
tests/           automated tests
docs/            public architecture and usage documentation
```

See [Architecture](docs/ARCHITECTURE.md) and the [Runbook](docs/RUNBOOK.md) for details.

## Data and safety

All bundled inputs, including NAV series, posts, attention signals, organisations, and product codes, are synthetic demonstration data described in [data/DATA.md](data/DATA.md).

FlowMirror is an experimental software environment. Simulated actions and model-generated text must not be interpreted as observations about real investors or as financial advice.

## License

Code is released under the [MIT License](LICENSE). Data files may have narrower terms documented in [data/DATA.md](data/DATA.md).
