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
| `images_root` | Directory holding the pre-resized creative images, one file per `image_id` (flat, 0-based names like `68f06272000000000503bd5b_0.jpg`). `null` (default) = text-only: the engine prints `[world] WARNING: no images_root configured -- TV arm degrades to text-only; the modality comparison measures nothing.` at run start, TV is byte-identical to T, and the `m_tv_arm_carries_images` invariant is skipped with that reason. |
| `image_pick` | Which of a note's OWN images to show: `"first"` (default; the note's first image, the intended original behaviour) or `"random"` (one of the note's own images, drawn per impression from the agent's deterministic RNG stream, so the same agent-day-post picks the same image on replay). Both hold the text, the landing fund and the institution constant, so `"random"` is a clean within-stimulus randomisation for later image-property work. |

Mechanics: the engine joins `<images_root>/<image_id>` and verifies the
file's sha256 against the pool's `image_sha256` before attaching anything; a
missing file or a hash mismatch attaches nothing and records an
`image_missing` / `image_sha_mismatch` prompt note (the same degradation-note
channel as always). Arm T never receives pixels or image fields; arm TC keeps
OCR + the frozen caption and still receives no pixels. TV impressions log the
chosen index as `img_idx` on the `imp` row (integer, or null when nothing
attached), and `run_meta.json` records
`images: {root, attached, missing, sha_mismatch, policy}`. The invariant
`m_tv_arm_carries_images` FAILS a run where TV is active, `images_root` is
configured, and zero impressions carried an image -- that silent no-op is
exactly what the invariant layer exists to catch.

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
reports a non-zero `images.attached` in `run_meta.json`, passes the
`m_tv_arm_carries_images` invariant, and shows `img_idx` on TV impressions.

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

Credentials are resolved in this order (first hit wins):

1. `config/api.yaml` - flat shape; copy the template `config/api_example.yaml`
   and fill in `endpoint`, `api_key`, `vision_model`, `text_model`;
2. env var `FLOWMIRROR_GLM_KEY` (the key only; endpoint/models from defaults);
3. env var `FLOWMIRROR_LEGACY_KEY_FILE` pointing at a file that contains the
   key (opt-in convenience; no path is baked into the repo).

If none of these is present, a live run stops immediately with a message
naming all three options. Keys are never printed or logged. Responses are
cached in `<out>/llm_cache.jsonl` (rows: `key/parsed/provenance/raw/ts`), so
re-running the same config is free and deterministic. Transient provider
errors (timeouts, non-200s) are retried on the frozen schedule (one initial
attempt + at most 4 retries); only after the final attempt does a row land
with `parsed: null` and the run continues.

Start small: `flowmirror run <run.json> --days 5 --agents 20 --out runs/out/live_probe`.

## 7. Outputs

`<out_dir>/` contains:

* `event_log.jsonl` - one JSON object per row; every row validates against
  `config/schemas/event.schema.json`. This file **is** the simulation.
* `llm_cache.jsonl` - provider responses (`key/parsed/provenance/raw/ts`).
* `prompts/` - only when `--dump-prompt` is used (section 5).
* run metadata / counters used by `--replay-check`; includes the `images`
  block (`root` / `attached` / `missing` / `sha_mismatch` / `policy`) from
  the image card.

To restore an overwritten output directory, see
`flowmirror.io.backups.backup_existing`.

## 8. Determinism rules (apply to any code you add)

* Seeds come only from `flowmirror.io.hashing.rng_seed_from` (or sha256);
  never Python's built-in `hash()`.
* Sort before iterating sets (and before logging dict order) whenever the
  order can reach an output file.
* Same config + same cache state => byte-identical `event_log.jsonl`
  (`--replay-check` proves it; a mismatch exits 3).
* `image_pick: "random"` draws from the per-agent deterministic stream (via
  `rng_seed_from`, never `hash()`), so the same agent-day-post always picks
  the same image on replay; a null `images_root` behaves exactly as the
  pre-image code did.

## 9. Troubleshooting

| symptom | fix |
|---------|-----|
| `[FATAL] missing input nav_cache: ...nav_cache.json` | research config needs the real NAV cache (section 3) |
| `...nav_demo_2025q4.json` missing | rerun the `make_demo_nav.py` command from section 1 |
| `[world] WARNING: no images_root configured ...` at run start | text-only run by design (TV degrades to T); set `images_root` per section 4 to make the TV arm multimodal |
| `image_sha_mismatch` notes / `images.sha_mismatch > 0` in run_meta.json | a file under `images_root` does not match the pool's sha256 (tampered or stale store); re-sync the store from fundmarket-sim (section 4) |
| invariant `m_tv_arm_carries_images` FAILED | TV ran with `images_root` configured but zero attachments; read the `[world] images:` line and the `images` block in run_meta.json, fix the store path (section 4) |
| `[flowmirror] FAIL ... (run): ...` | config error; the message names the problem; check `config/schemas/run.schema.json` |
| `exit 3` after `--replay-check` | replay mismatch: compare the two `replay-check sha*=` lines; nondeterminism was introduced somewhere |
| provider 401/403 or immediate credential error | credentials resolution failed; see section 6 |
| garbled CJK on the Windows console | `chcp 65001` (the CLI already forces UTF-8 on its own streams) |

## 10. Reference commands

```
flowmirror schemas        # list bundled schemas
flowmirror tree           # real package map, generated from the installed code
flowmirror validate <f>   # validate any config against its schema
```
