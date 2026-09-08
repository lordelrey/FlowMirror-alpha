# FlowMirror v7 RUNBOOK

End-to-end instructions for installing, running and inspecting simulations.
Everything in sections 0-1 works on a fresh `git clone` with no API key and no
real market data. All console output is ASCII; the CLI forces UTF-8 on its own
streams, so CJK content in configs/logs still prints correctly.

## 0. Install (once)

```
git clone <repo-url> flowmirror_v7
cd flowmirror_v7
python -m venv .venv
.venv\Scripts\activate          # Windows  (source .venv/bin/activate on macOS/Linux)
pip install -e .
python -m pytest -q             # must be all green
```

## 1. Quickstart: a full simulation in two commands

```
python data_pipeline/cn/make_demo_nav.py --pool data/creatives/cn/content_pool_v1_masked.jsonl --out data/funds/nav_demo_2025q4.json
flowmirror demo two-arm
```

`make_demo_nav.py` writes a deterministic **synthetic** NAV file
(`_meta.synthetic: true` - offline demos only, never for empirical claims).
`flowmirror demo two-arm` validates the config, runs 40 agents for 5 trading
days with the mock LLM (zero API calls), and prints where the outputs landed
plus what to look at next. Alternatives:

```
flowmirror demo three-arm   # 3 arms + fees, 60 agents
flowmirror demo null        # agent_policy "null": zero LLM calls by design
```

(`python -m flowmirror.cli ...` works anywhere `flowmirror ...` does.)

Every `flowmirror demo` variant is offline AND text-only by design: none of
the shipped offline demo configs sets `images_root`. The demo that exercises
real image attachment on the TV arm needs the local image store; see
section 4.

## 2. Running your own configs

Primary entry point:

```
flowmirror run <run.json> [--mock] [--days N] [--agents N] [--seed N]
                         [--out DIR] [--replay-check] [--dump-prompt AGENT@DAY|first]
```

`run` first validates the config against `config/schemas/run.schema.json`
(exit 1 with a readable error on failure), then calls the engine
(`flowmirror.engine.loop.main`) and propagates its exit code; it adds no logic
of its own. The engine can also be driven directly:

```
python -m flowmirror.engine.loop <run.json> [same flags]
python -m flowmirror.engine.loop --self-test
```

| flag | meaning |
|------|---------|
| `--mock` | force `mock_llm: true` (no provider calls) |
| `--days N` | override `window.max_trading_days` |
| `--agents N` | override `n_agents` |
| `--seed N` | override the run seed |
| `--out DIR` | override `out_dir`; relative paths resolve against the repo root; the LLM cache moves to `<out>/llm_cache.jsonl`, so an `--out` override never silently reuses another run's cached responses |
| `--replay-check` | run twice; require a byte-identical `event_log.jsonl` (prints `replay-check identical=True`; exit 3 on mismatch) |
| `--dump-prompt ...` | see section 5 |

Exit codes: `0` success; `1` config validation/usage failure; `2` argument
error; `3` engine failure or replay mismatch.

Bash wrapper doing validate-then-run in one step (from the repo root):

```
bash script/run.sh <scenario_dir|scenario.yaml> <run.json> [extra flowmirror-run flags]
```

## 3. Research configs vs demo configs

* `runs/mock_10x3.json`, `runs/mock_10x3_3arm.json`, `runs/mock_10x3_null.json`
  are the research-grade configs. They read the real NAV cache
  `data/funds/nav_cache.json`, which is **git-ignored** (4.4 MB, third-party);
  fetch it per `data/DATA.md` before using them.
* `runs/demo_two_arm.json`, `runs/demo_three_arm.json`, `runs/demo_null.json`
  (each carries a `_what` note) point at the synthetic NAV file from section 1
  and run anywhere, offline. Demonstration only, not research.
* `runs/demo_three_arm_images.json` additionally needs the LOCAL image store
  (section 4) and is therefore excluded from `flowmirror demo` on purpose;
  everything else about it matches `runs/demo_three_arm.json`.

Validate any config (schema is auto-detected for `scenario.yaml`):

```
flowmirror validate runs/demo_two_arm.json --schema run
flowmirror validate <scenario.yaml> --schema scenario
```

`mock_options` is required only when `mock_llm` is true; the legacy
`arm_level` / `p_image` keys are still accepted as aliases of
`modality_level` / `modality_arms`.

## 4. Images on the TV arm (`images_root`, `image_pick`)

The content pool (`data/creatives/cn/content_pool_v1_masked.jsonl`) ships
`image_ids` + `image_sha256` and deliberately NO file paths, so no pixels and
no absolute paths enter the repository. Two optional run keys control image
attachment on the TV arm:

| key | meaning |
|-----|---------|
| `images_root` | Directory holding the pre-resized creative images, one file per `image_id` (flat, 0-based names like `68f06272000000000503bd5b_0.jpg`). `null` (default) = text-only: the engine prints `[world] WARNING: no images_root configured -- TV arm degrades to text-only; the modality comparison measures nothing.` at run start. TV is then text-only but **not** byte-identical to T: `render_card` gives the T card its own `配图不展示。` line, which a TV card never receives, so a text-only run is NOT a T==TV null baseline and a T/TV contrast measured on one measures the presence of that sentence. `m_tv_arm_carries_images` reports the case in its `reason`. |
| `image_pick` | Which of a note's OWN images to show: `"first"` (default; the note's first image) or `"random"` (one of the note's own images, drawn per impression from a DEDICATED derived stream, `rng_for(run_tag, "img", agent_id, day, post_id)` -- deliberately **not** the agent's own `inv.rng`, because consuming that stream would shift every later draw for that investor and make a TV agent diverge from a T agent for reasons having nothing to do with the picture). The same agent-day-post therefore picks the same image on replay. Both policies hold the text, the landing fund and the institution constant, so `"random"` is a clean within-stimulus randomisation for later image-property work. |

Mechanics: the engine joins `<images_root>/<image_id>` and verifies the
file's sha256 against the pool's `image_sha256` before attaching anything; a
missing file or a hash mismatch attaches nothing and records an
`image_missing` / `image_sha_mismatch` prompt note (the same degradation-note
channel as always). Arm T never receives pixels or image fields; arm TC keeps
OCR + the frozen caption and still receives no pixels. TV impressions log the
chosen index as `img_idx` on the `imp` row (integer, or null when nothing
attached), and `run_meta.json` records
`images: {root, attached, missing, sha_mismatch, policy}`.

The invariant `m_tv_arm_carries_images` **reports, and never gates**: it always
passes, and its `reason` says which case the run is in (no `images_root`, no TV
arm, or both present with the three counts). Gates belong at irreversible,
cross-system, security or release boundaries, and a simulation run is none of
those. The question "did pixels actually reach the agents" is answered by
`run_meta.images.attached`, which is a number you can read at a glance -- it
does not need a failed run to express it.

`runs/demo_three_arm_images.json` is `runs/demo_three_arm.json` plus
`images_root` + `image_pick: "first"`. It is NOT part of the offline demo set
(`flowmirror demo` never runs it) because it needs the local image store,
which comes from the sibling fundmarket-sim checkout:

* location on this machine:
  `D:/Desktop/ABM paper/fundmarket-sim/sim/content_pool_v1/images`
* 801 files, flat, 0-based; every `image_id` referenced by the 200 pool notes
  resolves there with an exact sha256 match (all verified).

That absolute path inside the demo config is the one sanctioned machine-local
example in a tracked run config; edit it to wherever the store lives on your
machine. Images are never copied into this repo, and no other tracked file
carries an absolute path outside documentation examples.

```
python -m flowmirror.engine.loop runs/demo_three_arm_images.json --mock --days 5 --agents 60 --out runs/out/imgs --replay-check
```

A successful run prints `[world] images: <n> resolvable under <images_root>`,
reports a non-zero `images.attached` in `run_meta.json`, and shows `img_idx` on
TV impressions. On the store above a 3-day 24-agent run attaches 144 of 144 TV
impressions with `missing: 0` and `sha_mismatch: 0`, `img_idx` appears on TV
rows and on no others, and the TV prompt carries an `image_url` part whose
digest is the file's own while arm T carries neither -- the two arms are no
longer the same stimulus.

## 5. Exporting the exact prompt an agent saw

```
flowmirror run runs/mock_10x3.json --mock --days 5 --agents 40 --out runs/out/prompt_demo --dump-prompt first
flowmirror run runs/mock_10x3.json --mock --days 5 --agents 40 --out runs/out/prompt_demo --dump-prompt A007@3
```

This writes `<out>/prompts/<agent_id>_d<day>.txt` containing the exact
rendered system+user text (image parts appear as
`[image: <sha256 prefix>, <bytes> bytes]`, never base64), plus a small JSON
sidecar with `prompt_sha`, `arm`, the card ids shown and the channel shas.
It is a side artifact only: the event log and every hash are unchanged, so
`--replay-check` output stays byte-identical whether or not the flag is used.

## 6. Live runs (real provider)

Any OpenAI-compatible `chat/completions` endpoint works: the engine sends
`{model, messages, temperature, max_tokens}` with a Bearer header and attaches
images as `image_url` data URIs. Credentials are resolved in this order:

1. `config/api.yaml` - flat shape; copy the template `config/api_example.yaml`
   (it carries commented-out OpenAI, vLLM and Ollama examples) and fill in
   `endpoint`, `api_key`, `vision_model`, `text_model`;
2. env vars `FLOWMIRROR_ENDPOINT`, `FLOWMIRROR_VISION_MODEL`,
   `FLOWMIRROR_TEXT_MODEL` override the file's endpoint and model names (CI and
   containers configure this way);
3. if the file has no key: env `FLOWMIRROR_API_KEY`, then the older alias
   `FLOWMIRROR_GLM_KEY`;
4. env var `FLOWMIRROR_LEGACY_KEY_FILE` pointing at a one-line key file
   (opt-in; no path is baked into the repo).

If no key is found, a live run stops immediately with a message naming every
option. Keys are never printed or logged; `run_meta.json` records the endpoint
host and model names under `provider` and nothing else. Two traps: the run
config's `llm.model` / `llm.text_model` override the template's model names,
so keep both places consistent; and the native Anthropic API has a different
request shape, so put an OpenAI-compatible proxy in front of it. Responses are
cached in `<out>/llm_cache.jsonl` (rows: `key/parsed/provenance/raw/ts`), so
re-running the same config is free and deterministic. Transient provider
errors (timeouts, non-200s) are retried on the frozen schedule (one initial
attempt + at most 4 retries); only after the final attempt does a row land
with `parsed: null` and the run continues.

Start small: `flowmirror run <run.json> --days 5 --agents 20 --out runs/out/live_probe`.

### 6.1 What the first live probes measured (2026-09-08, glm-4.6v on the Zhipu coding-plan endpoint)

| probe | agents x days | workers | calls | outcome |
|---|---|---|---|---|
| 0a | 3 x 1 | 1 | 3 | all 200; one decision needed a second attempt (schema_invalid, then ok) |
| 0b | 20 x 5, three arms, real images | 4 | 120 | all 200, 0 terminal failures, 9 first-attempt failures recovered on the ladder; 210 images attached, 0 missing, 0 sha mismatch; **203 calls/hour**; CNY 0.99 in total |
| 0c | 20 x 5, same | 12 | 15 (stopped) | 429s from the fifth call on, no `Retry-After` header; throughput fell to **77 calls/hour** |

Three consequences worth knowing before your own run:

- **Concurrency.** The provider caps concurrent requests somewhere between 4 and 12. Four workers ran clean;
  twelve spent more time waiting out 429s than working. Start at `llm.workers: 4` to `6` and let the adaptive
  gate (halves on a 429, restores after twenty clean calls) find the ceiling. Do not set 12.
- **One key, one consumer.** The only earlier live attempt saw 400 consecutive 429s; its key was being used at
  the same time by a separate code-generation job. Never share a key between the engine and anything else.
- **Cost is not the constraint; wall time is.** A decision call carries about 3k prompt tokens (about 5k with
  images) and returns about 1.8k completion tokens from a reasoning model. A 400-agent, 12-day run is roughly
  5,600 calls, about CNY 46, and about 28 hours at four workers. Budget by the hour, not by the yuan.

Two switches exist because of these probes: `llm.rate_limit_cap_s` and `llm.rate_limit_max_wait_s` bound how
long one call waits on rate limits, and `--retry-transport-holes` makes a re-run ask the provider again for
cached transport failures (429s, timeouts, empty bodies) while keeping model-side failures terminal. Without the
flag a replay is byte-identical, holes included.

## 7. Outputs

`<out_dir>/` contains:

* `event_log.jsonl` - one JSON object per row; every row validates against
  `config/schemas/event.schema.json`. This file **is** the simulation.
* `llm_cache.jsonl` - provider responses (`key/parsed/provenance/raw/ts`).
* `prompts/` - only when `--dump-prompt` is used (section 5).
* `invariants_report.json` - every registered invariant with its detail, plus a
  summary (`total` / `passed` / `failed` / `skipped` / `all_passed`).
* `run_meta.json` - run metadata and counters, including:
  * `images` (`root` / `policy` / `attached` / `missing` / `sha_mismatch`)
  * `counters.decision_failures_transport` and `counters.decision_failures_model`
    -- the two kinds always sum to `counters.decision_failures`. The run summary
    line prints them as `decision_failure_rate=... (transport=N model=N)`.
  * `investors.initial_pnl_misses` when `initial_pnl.mode` is `"target"`

To restore an overwritten output directory, see
`flowmirror.io.backups.backup_existing`.

## 8. Determinism rules (apply to any code you add)

* Seeds come only from `flowmirror.io.hashing.rng_seed_from` (or sha256);
  never Python's built-in `hash()`.
* Sort before iterating sets (and before logging dict order) whenever the
  order can reach an output file.
* Same config + same cache state => byte-identical `event_log.jsonl`
  (`--replay-check` proves it; a mismatch exits 3).
* `image_pick: "random"` draws from a DEDICATED derived stream
  (`rng_for(run_tag, "img", agent_id, day, post_id)` via `rng_seed_from`, never
  `hash()`), so the same agent-day-post always picks the same image on replay.
  It deliberately does not consume the agent's own `inv.rng`: a draw inserted
  there would shift every later draw for that investor, so the arms would differ
  for a reason unrelated to the treatment. A null `images_root` behaves exactly
  as the pre-image code did -- no attachment, no counters, no extra draw, and no
  `img_idx` field on the `imp` row at all.
* A new per-day or per-agent value that the agents can see must be derived from
  data dated `t-1` or earlier. Invariant (a) enforces it and this codebase has
  shipped exactly that bug once, in a week key that stepped back one day instead
  of one week.

## 9. Troubleshooting

| symptom | fix |
|---------|-----|
| `[FATAL] missing input nav_cache: ...nav_cache.json` | research config needs the real NAV cache (section 3) |
| `...nav_demo_2025q4.json` missing | rerun the `make_demo_nav.py` command from section 1 |
| `[world] WARNING: no images_root configured ...` at run start | text-only run by design (TV degrades to T); set `images_root` per section 4 to make the TV arm multimodal |
| `image_sha_mismatch` notes / `images.sha_mismatch > 0` in run_meta.json | a file under `images_root` does not match the pool's sha256 (tampered or stale store); re-sync the store from fundmarket-sim (section 4) |
| `run_meta.images.attached` is 0 on a run that configured `images_root` | the store path resolves nothing: read the `[world] images:` line and the `images` block, then fix the path (section 4). `m_tv_arm_carries_images` reports this and does not fail the run |
| `decision_failure_rate` above the halt threshold, `transport=` equal to the whole count | not the model: the provider is unreachable, rate-limiting, or the credential is wrong (section 6). Only `model=` failures mean unparseable output |
| `market.benchmark_path is configured but unreadable` | the benchmark file is missing; `data/market/` is git-ignored third-party data, so regenerate it or unset the key (section 12) |
| a `qdii_blocked` calendar that never suspends anything | the run prints `[world] note: qdii_blocked is configured but no fund in this universe is QDII`; the key exists so the path is reachable, and no shipped config sets it (section 11) |
| `[flowmirror] FAIL ... (run): ...` | config error; the message names the problem; check `config/schemas/run.schema.json` |
| `exit 3` after `--replay-check` | replay mismatch: compare the two `replay-check sha*=` lines; nondeterminism was introduced somewhere |
| provider 401/403 or immediate credential error | credentials resolution failed; see section 6 |
| garbled CJK on the Windows console | `chcp 65001` (the CLI already forces UTF-8 on its own streams) |

## 10. Reference commands

```
flowmirror schemas        # list bundled schemas
flowmirror tree           # real package map, generated from the installed code
flowmirror validate <f>   # validate any config against its schema
flowmirror export-bundle <run_dir> [--out DIR] [--anonymise-orgs] [--max-bytes N]
```

`export-bundle` turns a finished run directory into the four files the web
viewer reads: `bundle.json` (run metadata, the `images` block, the invariants
summary and entries, a per-day aggregate, and a `truncation` record),
`posts.json` (per post, the text as EACH arm rendered it -- produced by calling
the engine's own `_feed_card` -> `render_card`, so the bundle cannot drift from
what the agents were shown -- plus per-arm reach and engagement, and the image
DIGEST only), `agents.json` (per investor: cell, arm, opening cash and
holdings, a per-day record, familiarity over time) and `events.json`.

It never emits an image byte, an absolute filesystem path, or a key-shaped
string. Over the byte budget it drops `imp` then `st` rows and records what it
dropped, so a truncation is always visible to the reader.
`--anonymise-orgs` replaces institution names with stable pseudonyms for
double-blind review.

Also useful, and NOT wired into the CLI:

```
python -m flowmirror.analysis.modality <run_dir> [<run_dir> ...]   # arm contrasts
python -m flowmirror.analysis.export_bundle <run_dir>              # same exporter, direct
python -m flowmirror.channels.feed                                 # channel self-test
python -m flowmirror.engine.world --self-test
python -m flowmirror.engine.loop  --self-test
```

The three self-tests must all exit 0. They cover 63, 50 and 41 assertions
respectively and are not part of `pytest`, so run them after touching the
engine.

## 11. Parameters the decision record pins

Every key below is optional. **Except where the table says otherwise, each
default equals the literal the code used before it became configurable**, so a
config that omits the block behaves byte-identically. The values come from
`docs/AUDIT_AND_REMEDIATION_PLAN_2026-09-07.md` section 5, which is the
authority when code and documentation disagree.

`config/schemas/run.schema.json` sets `"additionalProperties": false`, so a key
that is not in the schema is not merely ignored -- the whole config is rejected.
That is why several of these mechanisms were unreachable before: the code read
`cfg["fam_decay"]` and `cfg["qdii_blocked"]`, and no config could legally
supply either.

| key | default | note |
|-----|---------|------|
| `dynamics.fam_decay` | `0.2` | familiarity EMA decay. **Changed** from an unreachable 0.1. |
| `dynamics.lambda_attention` | `0.8` | attention adstock retention. **Changed**: attention used to share one constant with familiarity decay, so no run could vary them independently. |
| `dynamics.beta_guba` | `0.0` | coefficient on the lagged week's `z_abnormal` in the attention update. **Zero by decision**, and since decision 17 the channel is genuinely end to end: `feed.w_att` gives the attention stock a reader, so raising both values enables it with no code change. Do not set 0.1. |
| `dynamics.lambda_trust` | `0.9` | institution-trust adstock |
| `dynamics.fam_threshold` | `1.0` | exposure/affinity stock at which familiarity reaches level 1 |
| `dca.pct` | `0.02` | monthly plan ticket as a share of cash. **Known limitation**: the plan fires on `dt_cur.day == 1`, i.e. only when the calendar first of a month is itself a trading day, so a month whose 1st falls on a weekend or inside a holiday is skipped entirely (2025-11-01 is a Saturday; the real calendar's 1-8 October break skips October too). A real plan rolls to the next trading day. Changing this alters the number of instalments and the flow panel, so it is an open mechanism question rather than a bug fix. |
| `dca.min_ticket` | `100.0` | below this the plan skips and counts `dca_skipped` |
| `modality_run_arm` | **unset** | decision 19. Only meaningful at `modality_level: "run"`, where the engine defaults it to the first configured arm and requires it to be one of `modality_arms`. It used to default to `"TV"` unconditionally, which made every arm set omitting TV fail validation -- including agent-level runs, where the key does nothing. |
| `feed.climate_margin` | `1/6` | comment-climate majority margin. **Changed** from a hardcoded 1/3, which was twice as strict as decided. |
| `fees.subscribe_rate` | `0.0012` | **Changed** from 0.0; `act.fee` is non-zero now and the wealth identity already carried the fee term. Since decision 15 a plan (`kind="dca"`) instalment pays it too -- it was the one purchase channel exempt. |
| `fees.redeem_rate` | `0.005` | **Changed** from 0.0 |
| `llm.temperature` | `0.3` | equals the old module constant, and now reaches `run_meta.json` -- a published experiment has to record its own sampling temperature |
| `llm.max_provider_attempts` | `5` | physical HTTP retries. Distinct from `llm.max_attempts`, the macro retry switch; the two used to share a name in different namespaces. |
| `initial_pnl.mode` | `"lookback"` | `"lookback"` reproduces the historical behaviour exactly and is what every demo uses. `"target"` aims at a P&L DISTRIBUTION instead, for research configs -- see below. |
| `initial_pnl.lookback_days_min` / `_max` | `60` / `250` | the old literals |
| `initial_pnl.share_at_loss` | **none** | `"target"` mode only, and **required** there (decision 18). Its old 0.05 default was exactly the ~4.9% one-sided environment target mode exists to escape, so a config that asked for the mode and forgot the share silently reproduced the pathology. `lookback` never reads it. |
| `initial_pnl.tolerance` | `0.02` | `"target"` mode only; misses count as `investors.initial_pnl_misses` |
| `qdii_blocked` | unset | `{"YYYY-MM-DD": {"codes": [...]}}`. In the schema so the suspension path is reachable; **no shipped config sets it**, because a real suspension calendar is a data task. A configured calendar with no QDII fund in the universe prints a note. |
| `feed.w_att` | `0.0` | decision 17. Weight on the agent's per-fund attention stock in the feed score, bounded by `tanh` like `w_trust`. At 0.0 attention has no effect and runs are byte-identical to before the key existed. |
| `feed.fit_band_narrow` / `_wide` | `0.15` / `0.25` | `feed.fit()`'s two adjustments, equal to the literals it carried before. |
| `market.benchmark_path` | `null` | see section 12 |
| `market.benchmark_label` | `null` | **required whenever `benchmark_path` is set** (decision 6): it is the subject of the agent-facing market line, so it must disclose the proxy. A configured path with no label is refused. |

### Why `initial_pnl` has a target mode

The lookback draw randomises the LOOKBACK LENGTH, not the gain or loss. On the
real NAV series that produced only 4.9% of holdings at a loss, median +39%. In
a live smoke run every one of 77 decisions came back with mood 4, no comment
was bearish, and no affinity delta was ever negative -- the behaviour counts
varied fine, the ENVIRONMENT was one-sided. Under the synthetic demo NAVs the
same code gives roughly 48% at a loss, so the demos never showed the problem.
This matters because the disposition effect is one of the stylised facts the
paper reproduces, and reproducing it needs losers to exist. Every run now
prints its own opening split, e.g.
`[world] initial P&L (lookback): 18 of 43 opening holding(s) at a loss (41.9%)`,
and the `m_env_valence_warning` invariant carries the same numbers -- as a
report, never a gate.

## 12. The news channel and its benchmark

Three lines can appear in the news block. All three read data dated `t-1` or
earlier:

* **the market line** -- a five-trading-day benchmark return, present only when
  `market.benchmark_path` is set and the series covers the day
* **the holdings line** -- the holdings-weighted one-day return, using the NAVs
  of `t-1` and `t-2`
* **guba lines** -- for a fund whose lagged week has posts, the discussion
  volume relative to its trailing baseline

**The benchmark series is a disclosed PROXY.** The owner asked for the SSE
Composite (上证指数). The data source serves real index closes only for CSI 300
(000300), ChiNext (399006), the Dow, the Nasdaq and London gold -- not for the
SSE Composite -- so the unit NAV of fund `510760`, an SSE-Composite tracking
ETF, stands in. Two consequences bind the whole project:

* the values are fund unit NAVs, not index points, so a level is meaningless
  and **only a return may be used or shown**; `index_5d` is
  `v[t-1] / v[t-6] - 1` and nothing prints the level
* every prompt, report and paper sentence naming it says
  "上证综指ETF（510760）单位净值，作为上证综指的代理" and **never** "上证指数"

The file lives at `data/market/benchmark_sse_composite_etf_510760.json` as
`{"_meta": {...}, "series": {"YYYY-MM-DD": float}}`. `data/market/` is
git-ignored: third-party data, held to the same rule as the NAV cache and
never redistributed. A missing date, a gap, or fewer than six prior
observations omits the key entirely rather than interpolating -- a fabricated
quote in a market line is worse than a missing line. A configured but
unreadable path raises instead of quietly producing a text-only market line.

Switching to a real index means fetching `000300` daily closes and pointing
`benchmark_path` and `benchmark_label` at the result; no code changes.

**The guba stance seed is not available yet.** The signal file carries
`n_posts`, `reply_n`, `read_n`, `z_abnormal` and `ratio_vs_baseline`, and its
own `_meta` says no stance is computed there. So `bull_ratio` is absent, every
run prints
`[world] WARNING: guba stance seed unavailable -- 0 of N fund-weeks carry bull_ratio`,
the cold-start climate falls back to agent comments, and the guba line omits
its stance clause rather than printing a measured-looking 5:5. Neither the
README nor the paper may claim a "guba sentiment seed" until the labelling
task runs; the code accepts `bull_ratio` the moment it appears, with no
further change.

## 13. Failure kinds

`llm.decision_failure_halt` (default `0.02`) stops a run whose decision failure
rate passes the threshold. That rule exists to catch a MODEL that cannot
produce parseable output, and the engine used to count a dead socket, an HTTP
error and malformed JSON into the same bucket -- so a revoked key presented as
model instability. In one live smoke run three rate-limited calls looked like
an unstable model while the parse rate was 77 of 77.

Each `dec` row now carries `failure_kind`:

| value | meaning |
|-------|---------|
| `"transport"` | no model output at all: exception, HTTP error, empty response, or a rejected reasoning salvage |
| `"model"` | a response arrived and JSON or schema parsing failed |
| `null` | the decision parsed |

**The threshold and the halt condition are unchanged** -- the split is a
diagnosis, not a new gate; changing the gate would be a pre-registration
change. The identity `transport + model == decision_failures` holds on every
run. If a halt fires with `transport` equal to the whole count, fix the
credential or the rate limit; the model was never the problem.

## 14. The replay viewer

The repository now ships a replay interface: native ES modules under `web/`, with no
build step, no framework, and no CDN. It routes across seven pages via the URL hash.
The contract lives in `docs/WEB_CONTRACT_2026-09-07.md` — read it before changing
anything about the interface. The acceptance record lives in
`docs/WEB_ACCEPTANCE_2026-09-08.md`.

### Three ways to open it

| Mode | Command | What you get |
|---|---|---|
| Local mini-server | `python web/server.py [--port 8765] [--images-root <local image store>]` | Everything. Binds 127.0.0.1 only. Lists the runs under `runs/out/`; can launch a run from the page and stream engine stdout; with `--images-root` it serves real images (sha256 re-computed and compared on every request) |
| Any static server | `python -m http.server 8765 --bind 127.0.0.1`, then open `http://127.0.0.1:8765/web/?run=<tag>` | All read-only pages work; the run-launch controls are disabled and the equivalent command is shown |
| GitHub Pages | — | The repository distributes exactly one sample bundle, `web/samples/demo_three_arm/`, so a real run is one click away: open `?run=demo_three_arm` (three arms T/TC/TV, 42 investors × 12 trading days, synthetic NAVs, institution names anonymized). With no `?run=` at all the four run-scoped pages say so and point at the picker — the viewer does not pick a run for you |

### The seven pages, one sentence each

| Page | Sentence |
|---|---|
| Home | What this instrument is, what the three modality arms are, and the checkout vocabulary |
| Configure & run | Launch a run, five-step ladder plus raw engine stdout; fully disabled with the equivalent command shown when no local server is present |
| Replay | The population field (age on the horizontal axis, assets on the vertical, three risk-tolerance bands per cell), the day's posts, an inspector panel, transport controls, a diffusion heatmap, and the suitability checkout table. Space toggles play/pause; left/right arrows step |
| Investor | A day-by-day timeline of one investor across the whole run: decisions, commentary, trades, checkouts, familiarity changes |
| Modality comparison | The same post as it appeared under each arm that actually occurred in this run. Two arms means two columns; no empty slots |
| Audit | Invariants one by one, the honesty panel (data scale, synthetic equity, mock/live, agent policy, image counts, input hashes), and every notice raised during load |
| Data & scenarios | The list of runs that can be opened, and which files assemble this sandbox |

### Four things to know

1. **`export-bundle` comes before the viewer**: to see per-arm card copy, opening
   positions, or day-by-day agent records, run
   `python -m flowmirror.analysis.export_bundle runs/out/<tag>` beforehand. Runs
   without an export bundle still open; the interface states plainly that only raw
   artifacts are present and card copy is unavailable.
2. **The "local-only" badge**: appears only when you are on your own machine and the
   server was started with `--images-root`. Images never enter the repository and
   never ship with an export bundle; the bundle carries only their sha256.
3. **The interface never accepts credentials**: credentials are resolved inside the
   engine (`config/api.yaml` → environment variables → legacy key file); the server
   answers a single boolean "is one configured", and the browser never touches them.
4. **One console 404 is guaranteed in static mode**: the page probes whether
   `/api/runs` exists, and a static server has no such endpoint. That is the probe
   itself, not an error.
