# Social and financial sandbox

FlowMirror has two independent execution paths: the original daily simulation
engine and a bounded, step-by-step browsing runner. Existing daily logs retain
their batch-exposure meaning; the observer does not invent scrolling actions for
those logs. The new runner saves the input, action, result, and next view of each
browsing step.

## Start locally

Install the project as described in the README. All named CLI demos below are
offline, synthetic mechanism examples and make no LLM calls:

```bash
python -m flowmirror.platform.browse_cli --demo --out runs/browse_out/social_demo
python -m flowmirror.platform.browse_cli --account-demo --out runs/browse_out/account_demo
python -m flowmirror.platform.browse_cli --marketing-demo --out runs/browse_out/marketing_demo
python -m flowmirror.platform.browse_cli --replay --out runs/browse_out/marketing_demo
python web/community_server.py --port 8793
```

Use a new output directory for each new run. `--max-new-calls` bounds offline
policy invocations, not paid model calls. To continue a saved offline prefix, use
`--resume --out <same-directory>`. External policies require explicit Python API
integration; this CLI does not launch arbitrary model providers.

The local observer pages are:

| Page | Purpose |
| --- | --- |
| `/community.html?mode=browse&run=marketing_demo` | Per-agent posts, actions, public interactions, and available account history |
| `/market.html?run=marketing_demo` | Phase-level market, account, social, and institution summaries with drill-down |
| `/corpus.html` | Optional local corpus search; requires an exported corpus |
| `/calibration.html` | Optional precomputed temporal evaluation reports |

The community server listens only on loopback and has no experiment-launch
endpoint. It is a researcher observer, never an agent-facing information source.

## Social actions and isolation

Each `BrowseSession` receives its own private state and can expose scroll, open,
comment, like, save, follow, unfollow, and finish actions. `PublicBoard` publishes
comments and follower counts at explicit stage boundaries. Following immediately
changes comment priority for the actor's own session. Recommendations use existing
followers and are empty at cold start; no actor is forced to follow or become an
influencer. The observer's ability to inspect other actors is not an agent permission.

## Accounting and macro observation

The account demo separates submitted orders, fills, and settlement. Cash or units
are reserved, unavailable prices cannot execute orders, and unsettled proceeds
remain receivables. Account projections include fees, positions, and recorded P&L
where the run supports them. Browsing-only runs must not be interpreted as having
trades or measured investment returns.

Macro views aggregate the selected saved phase and retain currency and product
distinctions. They complement individual traces; they do not establish that social
attention causes asset returns. Prices and NAVs remain exogenous inputs. This is
not a real brokerage connection, live market terminal, or learned market model.

## Institution publication

Optional institution policies own separate creative libraries and finite publication
counts. The offline `rotate` and `feedback_select` rules use their own prior-phase
aggregate feedback, not investor private memories, wallets, or future observations.
Simulated reposts retain source identity and availability time. Post/order links are
recorded associations, not causal marketing attribution.

## Bring authorized local data

No third-party corpus, images, database, model cache, or run record is distributed.
The warehouse adapter opens an explicitly supplied SQLite database with `mode=ro`
and writes derived files only to the requested local output directory:

```bash
python -m flowmirror.platform.warehouse export --database /path/to/flowmirror.db --out output/local_corpus
python -m flowmirror.platform.warehouse build --corpus output/local_corpus --out output/local_probe.json --start 2025-11-01 --days 30 --agents 300 --market CN
python -m flowmirror.platform.browse_cli --spec output/local_probe.json --out runs/browse_out/local_probe
```

The adapter expects the supported warehouse schema, not an arbitrary database.
Larger populations are synthetic agents, not additional real participants. Dated
observations respect availability timestamps; undated posts remain outside the
simulation timeline. Product execution rules are not inferred from missing metadata.
Run each command with `--help` for optional corpus, image, index, and time settings.

Paired-stimulus helpers live in `flowmirror.platform.matched_probe` and
`flowmirror.analysis.browse_requests`. The offline evaluation entry point is
`flowmirror.analysis.calibration_run`; it separates training, calibration, and
held-out periods. Its unadjusted NAV-change targets are not total investment returns.
These tools construct and inspect local inputs; they do not validate human behavior.

## Persistence and testing

New CLI browsing runs use an append-only journal; the Python API also supports
snapshot storage. Replay compares the saved prefix and separately reports completion.
An unanswered external provider request remains uncertain and is not automatically
retried. Derived indexes speed observer reads but are not simulation evidence.

`runs/out/`, `runs/browse_out/`, and `output/` are ignored by Git. They may contain
private views and must not be served publicly or delivered to other agents. The
automated test suite uses temporary synthetic fixtures:

```bash
python -m pytest -q
```
