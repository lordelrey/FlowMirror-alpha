# SCRIPT_AUDIT_2026-09-06.md - audit of the experiment scripts against the paper's claims

Five independent lenses over the engine, the analysis layer and the pipeline. Every finding was then sent to a separate agent instructed to **refute** it; the verdict and the evidence below belong to that agent, and a finding that could not be substantiated from the code was dropped.

Ground truth at the time of the audit: HEAD `b6f93e9`, `pytest` reports 108 passed. Workflow run `wf_92a84b7b-480`.

**37 findings survived: 4 critical, 18 high, 12 medium, 3 low.** The two most damaging were re-verified by hand against the source and by running the engine.


| # | severity | file | title |
|---|---|---|---|
| 1 | critical | `flowmirror/engine/loop.py` | Inverted `core` polarity: a run where every invariant PASSES exits 4, and a run with a FAILING invariant exits 0 |
| 2 | critical | `flowmirror/engine/loop.py` | The TV arm never attaches pixels: `images_root`/`image_pick` are schema-only, so every image impression silently degrades to text |
| 3 | critical | `D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\world.py` | Invariant (a), the no-look-ahead check, compares an ISO week key against a calendar date and therefore fails on every run of two or more days |
| 4 | critical | `flowmirror/engine/loop.py` | The TV arm never attaches a pixel: _feed_card looks for note keys the shipped pool does not have |
| 5 | high | `flowmirror/engine/world.py` | Invariant (a) compares an ISO-week key against an ISO date, so it can never pass on a run longer than one day |
| 6 | high | `flowmirror/engine/world.py` | Invariant (l), the byte-identical-replay invariant the paper cites as its replay evidence, is not in the registry and is never checked |
| 7 | high | `flowmirror/engine/loop.py` | The event log records only the text half of the cache key: `image_shas` and the prompt degradation notes never reach any row |
| 8 | high | `D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\loop.py` | The invariant exit code is polarity-inverted: a failing run exits 0, a clean run exits 4 |
| 9 | high | `D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\world.py` | The same-day-leakage clause of invariant (a) is unreachable, and would raise TypeError if it were ever reached |
| 10 | high | `D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\world.py` | Invariant (c) reports a vacuous PASS on a branch the cohort can never enter, where the paper promises a SKIP |
| 11 | high | `D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\world.py` | Registry key (h) checks modality-arm balance; the paper's invariant (h) claims persona-cell cohort composition, which no invariant checks |
| 12 | high | `D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\world.py` | The appendix claims twelve invariants checked at the end of every run; the registry has eleven and invariant (l) never appears in any report |
| 13 | high | `flowmirror/engine/loop.py` | The TV arm never attaches an image with the shipped content pool: TV is byte-identical to T |
| 14 | high | `flowmirror/engine/world.py` | Invariant (a) compares an ISO week key to an ISO date, so it is False on every run and can never detect a real leak |
| 15 | high | `flowmirror/engine/loop.py` | Per-impression delivery notes (image_missing, image_unsupported, tc_no_caption) are computed and then discarded |
| 16 | high | `flowmirror/engine/world.py` | The engine's invariant registry does not match the twelve invariants the paper's appendix prints |
| 17 | high | `flowmirror/engine/world.py` | The m_tv_arm_carries_images invariant documented in three places does not exist in the registry |
| 18 | high | `flowmirror/engine/loop.py` | imp.img_idx is declared in the event schema and never emitted; image_pick=random is a byte-identical no-op |
| 19 | high | `flowmirror/engine/world.py` | Invariant pass/fail polarity is inverted: a run with failing invariants exits 0, a clean run exits 4 |
| 20 | high | `flowmirror/analysis/modality.py` | I2 detection never matches engine logs, so click_rate and subscribe_conversion are structurally dead on every real run |
| 21 | high | `flowmirror/analysis/modality.py` | The bounded_null branch has no lower bound, so a large effect in the opposite direction is reported as practical equivalence |
| 22 | high | `flowmirror/analysis/modality.py` | Run-level pairing silently discards runs that share a (seed, arm) key, contradicting the function's own no-silent-drop guarantee |
| 23 | medium | `flowmirror/engine/loop.py` | `--replay-check` prints the warm-run provider-call count but never asserts it is zero |
| 24 | medium | `D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\loop.py` | The engine-side extra checks are injected only when they FAIL, so a passing midday-mutation or cap-stop check is silently absent from the report |
| 25 | medium | `flowmirror/engine/loop.py` | oc_cf is identical to oc in every suitability-on run, so the within-run gate counterfactual carries no information |
| 26 | medium | `flowmirror/engine/loop.py` | The null policy's 'preregistered' parameters are ordinary run-config keys with no pin or equality check |
| 27 | medium | `config/schemas/run.schema.json` | The sha256 verification of image files against the pool's image_sha256 does not exist in the engine |
| 28 | medium | `flowmirror/engine/loop.py` | No per-agent-day cap on image-bearing TV cards exists, contradicting the paper's \maximgday = 3 |
| 29 | medium | `flowmirror/analysis/modality.py` | SESOI never gates the supported branch: a statistically nonzero effect a hundred times smaller than the SESOI is reported as supported |
| 30 | medium | `flowmirror/analysis/modality.py` | Exposure-concentration Gini is computed only over posts that received at least one impression, understating concentration and compressing the very contrast it measures |
| 31 | medium | `flowmirror/analysis/modality.py` | The single-seed guard keys off the difference df instead of the effect df, so an effect that could not be computed at all is reported as indeterminate with no warning |
| 32 | medium | `flowmirror/analysis/modality.py` | Impressions from decisions that failed to parse stay in the rate denominator, so a differential parse-failure rate across arms becomes a spurious contrast |
| 33 | medium | `flowmirror/analysis/common.py` | The t table stops at df=10 and falls back to the normal quantile, producing anti-conservative intervals for larger seed counts |
| 34 | medium | `flowmirror/analysis/modality.py` | The within-run agent-clustered bootstrap the paper declares retired is still computed, exported and printed as a headline parameter |
| 35 | low | `flowmirror/engine/loop.py` | `card["image_sha"]` hashes the path string, not the file, while the schema documents it as verified against the pool's `image_sha256` |
| 36 | low | `flowmirror/engine/loop.py` | Displayed NAV, three-month/one-year returns and the trend channel are computed from day t, not day t-1 |
| 37 | low | `flowmirror/engine/loop.py` | Dollar-cost-averaging subscriptions pay no subscription fee |

---

## 1. CRITICAL  -  Inverted `core` polarity: a run where every invariant PASSES exits 4, and a run with a FAILING invariant exits 0

**Where.** `flowmirror/engine/loop.py` 839 (with flowmirror/engine/world.py:840-842)

**What.**

`world.check_invariants` builds `core` as the LIST OF FAILING invariant keys and returns `bool(core)` (world.py:840-842), so the returned boolean is True exactly when at least one invariant FAILED. `loop._finish` consumes it with the opposite meaning: `ok = bool(core) and all(bool(v.get("pass", True)) for v in extras.values())` (loop.py:839), i.e. it treats True as "everything is fine". The two halves of the contract disagree, and `world.write_reports` computes the report status independently and correctly, so `invariants_report.json` and the process exit code contradict each other on every run. The test suite does not catch this because `tests/unit/test_invariant_wiring.py:test_failing_invariant_exits_4_and_is_named` monkeypatches `loop.check_invariants` with a stub that returns `checks, False` for a failing run — it encodes loop.py's convention and never exercises the real world.py return — and `test_mock_run_reports_real_invariant_detail` asserts `run_simulation(cfg) == 0` for the mock acceptance run, which only passes because that run has a genuinely failing invariant (see the `a_lagged_signals_only` finding). The two bugs mask each other.

**How it fails.**

Reproduced verbatim. `python -m flowmirror.engine.loop runs/demo_two_arm.json --agents 20 --days 1 --out runs/out/_probe1d` -> `invariants_report.json` says `{"total": 11, "passed": 11, "failed": 0, "skipped": 0, "all_passed": true}`, console prints `invariants=FAIL()` plus `invariant FAIL core: {"core": false, "note": "core=False but no individual check entry is failing"}`, process exit code = 4. The same command with `--replay-check` exits 3 — the code the paper's audit reads as a byte-identical-replay mismatch — without ever computing sha1/sha2. Conversely `python -m flowmirror.engine.loop runs/mock_10x3.json --mock --agents 40 --days 5` fails `a_lagged_signals_only`, prints `status=invariant_failure (invariants: 10 pass, 1 fail)`, and returns exit code 0. Any harness that gates on the exit code (as §5's "the affected runs are listed and withdrawn from every analysis" requires) therefore withdraws every clean run and keeps every dirty one.

**Paper claim at risk.** appendix.tex:145-147 and 5_validation.tex:18-20 ("The audit reports per-invariant pass or skip... An invariant failed or was skipped... the affected runs are listed with their replay hashes, and they are withdrawn from every analysis"), and 3_environment.tex:96 ("an invariant failure exits 4").

**Fix.**

Make the contract explicit in one direction and pin it with a test that calls the real `world.check_invariants`. Either return `not core` from world.py:842 (rename to `core_ok`) or change loop.py:839 to `ok = (not core) and all(...)`. Whichever side moves, update world.py:703-706's docstring, and add a test that runs a real 1-day mock config end to end and asserts `rc == 0` when `invariants_report.json` reports `all_passed: true` — the current stub-based test cannot detect the inversion.

**Verification (confirmed).**

Verified from source and reproduced at runtime; could not refute any part.

PRODUCER (world.py:839-841, end of check_invariants):
    core = [k for k, v in checks.items() if v.get("pass") is False
            and not (k == "e_all_cells_exposed" and not big)]
    return checks, bool(core)
core is the list of FAILING keys, so the returned bool is True iff something failed. World's own self-test pins this polarity (world.py:1118-1120):
    chk("invariants_clean_passes", cf is False and [k for k, v in cc.items() if not v.get("pass", True)] == ["e_all_cells_exposed"])
    chk("invariants_flag_redeem_block", bf is True and bc["i_redeem_checkout_never_blocked"]["pass"] is False)

CONSUMER (loop.py:839):
    ok = bool(core) and all(bool(v.get("pass", True)) for v in extras.values())
Treats True as "everything fine" -- directly inverted.

RUNTIME REPRODUCTION (both commands run by me, verbatim):
1) python -m flowmirror.engine.loop runs/demo_two_arm.json --agents 20 --days 1 --out runs/out/_probe_verify1
   -> "[world] reports written to ...; status=ok (invariants: 11 pass, 0 fail, 0 skipped, 11 total)"
   -> console "invariants=FAIL()" and 'invariant FAIL core: {"core": false, "note": "core=False but no individual check entry is failing"}'
   -> "engine exit code 4: invariant failures: "  ; EXITCODE=4 on a fully clean run.
2) Same command with --replay-check -> EXITCODE=3 with NO "replay-check sha1=" line printed. Confirmed by loop.py:1500-1502:
       if run_simulation(cfg, rt) != 0:
           return 3
       sha1 = event_log_sha(logp)
   so 3 (the byte-identical-replay mismatch code the paper's audit reads) is returned before any hash is computed.
3) python -m flowmirror.engine.loop runs/mock_10x3.json --mock --agents 40 --days 5
   -> "status=invariant_failure (invariants: 10 pass, 1 fail, 0 skipped, 11 total)", "invariants=FAIL(a_lagged_signals_only)" ; EXITCODE=0.

REPORT vs EXIT CONTRADICTION: world.write_reports computes status independently and correctly; on run (1) report says ok while exit is 4, on run (3) report says invariant_failure while exit is 0.

TEST BLINDNESS (tests/unit/test_invariant_wiring.py:149-154):
    def fake_check_invariants(state, events_path, cfg_):
        checks = {k: {"pass": True, "n": 1} for k in REGISTRY}
        checks.update(failing)
        return checks, False
The stub returns False for a run it declares FAILING -- the opposite of what real world.check_invariants returns for a failing run. It encodes loop.py's convention an


---

## 2. CRITICAL  -  The TV arm never attaches pixels: `images_root`/`image_pick` are schema-only, so every image impression silently degrades to text

**Where.** `flowmirror/engine/loop.py` 248-250 (with flowmirror/agents/prompt.py:403-418)

**What.**

`_feed_card` resolves the picture as `image_path = note.get("image_path") or note.get("image") or note.get("cover")` (loop.py:249-250). The shipped pool `data/creatives/cn/content_pool_v1_masked.jsonl` carries none of those three keys — its image fields are `image_ids`, `image_sha256`, `n_images` — so `card["image_path"]` is None for all 201 notes. `build_decision_messages` then takes the else branch at prompt.py:416-418, appends an `image_missing` note, and leaves `image_shas` empty. `grep -rn 'images_root\|image_pick\|img_idx' flowmirror/` returns nothing: the keys exist only in `config/schemas/run.schema.json:5,256` and `config/schemas/event.schema.json:132-133`. This confirms the DEV_HANDOVER §10 claim, but the consequence is worse than a missing feature: nothing fails. The degradation note lives only in `rec["notes"]["prompt_notes"]`, which loop.py never logs; `image_shas` never reaches the event log either; and there is no invariant on image attachment. So a TV run that showed zero pixels produces a clean, byte-identically replayable event log that is indistinguishable from one that showed all of them.

**How it fails.**

Reproduced with the config whose own `_what` string asserts the opposite. `runs/demo_three_arm_images.json:20-21` sets `images_root: "D:/Desktop/ABM paper/fundmarket-sim/sim/content_pool_v1/images"` (that store exists and holds all 801 files) and `image_pick: "first"`, and its `_what` claims "The TV arm resolves <images_root>/<image_id> and attaches the file only after its sha256 matches the pool's image_sha256... TV impressions log img_idx on the imp row" and that it proves "the three arms genuinely differ". Running it at 20 agents x 2 days with `build_decision_messages` instrumented: 84 TV cards rendered, 84 `image_missing` notes emitted, image-attachment histogram `{0 images: 40 prompts}` — not one pixel attached, no `img_idx` on any imp row, run returns 0. The T/TV contrast reduces to the presence or absence of the single line "配图不展示。" (prompt.py:325-326). Every E1 $\dInput$ number computed from such a run measures that one line, not a modality.

**Paper claim at risk.** 3_environment.tex:94 (cache "keyed by the SHA-256 of the prompt text and of the attached image hashes"), 4_interventions.tex:63 ("the chosen index and the image hash are logged on the impression event"), and the entire $\dInput^{\mathrm{E1}}$ image-vs-text contrast in 6_experiments.tex.

**Fix.**

Either wire `images_root`/`image_pick` in `_feed_card` (resolve `<images_root>/<image_id>`, verify against the note's `image_sha256`, set `image_path`, log `img_idx` and the verified hash on the `imp` row) as the schema and the config's `_what` already describe, or — until it lands — make the degradation loud: have `run_simulation` count `image_missing`/`image_unsupported` prompt notes and fail an invariant when a TV/exposure run attaches zero images. A run configured with `modality_arms` containing "TV" that attaches no pixels must not exit 0.

**Verification (confirmed).**

I tried to refute this and could not; every load-bearing element reproduces from the code.

1) The resolution line is exactly as claimed — D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\loop.py:248-250:
    image_path = None
    if post.get("img"):
        image_path = note.get("image_path") or note.get("image") or note.get("cover")
The docstring above it (loop.py:234-236) even admits the design: "T and TV cards gain no pixel-side keys beyond image_path/sha, so only n_comments_prev (and masked text) changes them vs the baseline."

2) The shipped pool has none of those three keys. Parsing all 201 rows of data/creatives/cn/content_pool_v1_masked.jsonl: keys include image_ids, image_sha256, n_images, ocr_masked...; rows carrying image_path/image/cover = 0.

3) Live reproduction (real pool row, real _feed_card, real build_decision_messages):
   card image_path = None | image_sha = None
   image_shas = []
   prompt_notes = ['image_missing']
   has image_url part = False
i.e. a TV card built from a note whose image_ids = ['68be3ddd000000001b01e4af_0.jpg'] attaches nothing and falls into the else branch at flowmirror\agents\prompt.py:416-418.

4) The keys are engine-dead. `grep -rn 'images_root|image_pick|img_idx'` over flowmirror/ returns only data_pipeline/cn/caption_frozen.py (an offline captioning script with its own --images-root); nothing under flowmirror/engine or flowmirror/agents. world.py's only image logic is _has_image (world.py:536) collapsing n_images to a boolean `img` flag (world.py:581).

5) The three "it would be caught" guards do not exist:
   - No m_tv_arm_carries_images invariant: the string appears only in config/engine_defaults.yaml:40 and config/schemas/run.schema.json:257 prose; no chk(...) in loop.py's invariant list mentions images.
   - No startup warning: "no images_root configured" exists only in RUNBOOK.md:115/222 and the schema description; no .py emits it.
   - No img_idx on imp rows: loop.py:948 is `logd("imp", t=t, d=dstr, i=inv.id, p=pid, arm=arm, slot=s, source=source)` — no image field. The dec row (loop.py:996-1000) carries prompt_sha/raw_sha/status/arm/mood/reason/violations and no image_shas.
   - No run_meta images block.

6) The config contradiction is real. runs/demo_three_arm_images.json sets images_root/image_pick and its _what asserts "The TV arm resolves <images_root>/<image_id> and attaches the file only after its sha256 matches the pool's image_sha256 ... TV impressions log img_idx on the imp row", "proving 


---

## 3. CRITICAL  -  Invariant (a), the no-look-ahead check, compares an ISO week key against a calendar date and therefore fails on every run of two or more days

**Where.** `D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\world.py` 751 (audit rows produced at flowmirror/engine/loop.py:908-910)

**What.**

loop.py:908-910 appends one audit row per trading day: `signal_audit.append({"t": t, "used": wk_prev, "live_end": dstr, "prev_live_end": (dt_cur - ONE_DAY).isoformat(), "day_keys": guba_day_keys})`. `wk_prev` is `_week_key(dt_cur - ONE_DAY)`, an ISO WEEK label such as "2025-W41"; `prev_live_end` is a DATE such as "2025-10-08". world.check_invariants then asserts they are equal: `for e in audit[1:]: if e.get("used") != e.get("prev_live_end"): a_ok = False` (world.py:750-752). The two fields can never be equal for any run, any calendar, any config, so `checks["a_lagged_signals_only"]["pass"]` is False whenever len(audit) >= 2. I dumped the live audit rows from the reference mock run: t=0 {used: "2025-W41", prev_live_end: "2025-10-08"}, t=1 {used: "2025-W41", prev_live_end: "2025-10-09"}, and so on for all five days. The invariant has zero discriminating power: it cannot separate a leaky run from a lagged one, it only ever reports failure. It is also the reason the whole test suite's `assert run_simulation(cfg) == 0` lines (tests/unit/test_invariant_wiring.py:114, tests/unit/test_dump_prompt.py:58) pass -- they are riding on this permanent failure cancelling the inverted exit code of finding 1. Fix either bug alone and those tests break.

**How it fails.**

Any run of >= 2 trading days -- including every configuration in the paper's run budget -- writes invariants_report.json with `a_lagged_signals_only: {"pass": false}` and summary `all_passed: false`. Under the paper's own failure rule (5_validation.tex:20) every one of those runs must be named as failed and withdrawn from Sections 6 and 7, leaving no estimand reportable. Equally, if a real same-day leak were introduced into the feed ranking tomorrow, invariant (a) would report exactly the same False and nobody could tell the two situations apart.

**Paper claim at risk.** paper/v7/sections/appendix.tex:145 invariant (a) -- "every displayed signal is computed from state at or before $t-1$"; paper/v7/sections/5_validation.tex:16 -- "invariant~(a) the rest; a channel found not to be lagged is named in the audit rather than absorbed into a pass"; paper/v7/sections/3_environment.tex:96 -- "no displayed signal postdates $t-1$". The audit currently names (a) as failed on every run, so no lagging claim in the paper is supported by its own instrument.

**Fix.**

Make the two fields comparable: either have loop.py:909 emit `prev_live_end` as `_week_key(dt_cur - ONE_DAY)` so it is the same week-key domain as `used`, or have world.py:751 compare `used` against `_week_key(date.fromisoformat(e["prev_live_end"]))`. Then add a self-test row in world.py's self_test that feeds a deliberately non-lagged audit row and asserts (a) goes False, and a lagged one that asserts True, so the check is proven to discriminate rather than merely to fail.

**Verification (confirmed).**

I tried to refute this and could not. The type mismatch is real, it is reproducible on a live run, and it has the exact knock-on effect described.

1) The producer writes a WEEK key into `used` and DATES into `live_end`/`prev_live_end`.
D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\loop.py:153-155
```
def _week_key(d):
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"
```
loop.py:873 `wk_prev = _week_key(dt_cur - ONE_DAY)   # lagged signal week (t=0 -> pre-window day)`
loop.py:908-910
```
signal_audit.append({"t": t, "used": wk_prev, "live_end": dstr,
                     "prev_live_end": (dt_cur - ONE_DAY).isoformat(),
                     "day_keys": guba_day_keys})
```
The engine's own self-test pins the format: loop.py:1313 `chk("week_key_format", _week_key(date(2024, 1, 1)) == "2024-W01")`.

2) The consumer compares that week key against a date.
D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\world.py:748-756
```
audit = state.get("signal_audit") or []
a_ok = len(audit) > 0
for e in audit[1:]:
    if e.get("used") != e.get("prev_live_end"):
        a_ok = False
for e in audit:
    if e.get("live_end") == e.get("used") and int(e.get("day_keys", 0)) > 0:
        a_ok = False
```
Both clauses compare "YYYY-Www" to "YYYY-MM-DD". The first can never be equal (so it always fires from the 2nd row on); the second can never be equal either, so the leak-detecting clause is dead in the other direction too. The invariant genuinely has zero discriminating power in both directions, not just one.

3) Live reproduction (runs/mock_10x3.json, 3 trading days, mock LLM, n_agents=10), instrumenting check_invariants:
```
{'t': 0, 'used': '2025-W41', 'live_end': '2025-10-09', 'prev_live_end': '2025-10-08', ...}
{'t': 1, 'used': '2025-W41', 'live_end': '2025-10-10', 'prev_live_end': '2025-10-09', ...}
{'t': 2, 'used': '2025-W41', 'live_end': '2025-10-13', 'prev_live_end': '2025-10-12', ...}
a check: {'pass': False, 'days_audited': 3, ...}
core (True == something failed): True
[world] ... status=invariant_failure (invariants: 10 pass, 1 fail, 0 skipped, 11 total)
invariants=FAIL(a_lagged_signals_only)
EXIT CODE: 0
```
This matches the reporter's dump of the reference mock run.

4) It is universal across every committed multi-day run. Scanning all 39 `runs/**/invariants_report.json`: every report with `days_audited >= 2` has `a_lagged_signals_only.pass == false` and `summary.all_passed == false` (demo_null, demo_two-arm, demo_three-arm, mock_10x3, mock_3arm,


---

## 4. CRITICAL  -  The TV arm never attaches a pixel: _feed_card looks for note keys the shipped pool does not have

**Where.** `flowmirror/engine/loop.py` 248-251

**What.**

`_feed_card` builds the image slot as:

```python
image_path = None
if post.get("img"):
    image_path = note.get("image_path") or note.get("image") or note.get("cover")
```

The only content pool in the repo, `data/creatives/cn/content_pool_v1_masked.jsonl`, carries `image_ids`, `image_sha256` and `n_images`. A key census over all 200 note rows: 200 have `n_images > 0` and `image_ids` non-empty; 0 have `image_path`, `image` or `cover`. `_has_image` (`flowmirror/engine/world.py:535-542`) keys off `n_images`, so `post["img"]` is True for every post and the card presents as image-bearing, but `image_path` is always `None`. `flowmirror/agents/prompt.py:403-416` then evaluates `ipath = ""`, `exists = False`, appends no `image_url` part and records `"image_missing"`. No warning, no counter, no non-zero exit. The delivered TV card is `render_card` output that simply omits the T arm's "配图不展示。" line (`flowmirror/agents/prompt.py:326-330`), i.e. text-only.

This is observable in a committed run made with a valid store: `runs/out/try_images/run_meta.json` has `images_root` set to the 801-file image directory, `runs/out/try_images/event_log.jsonl` has 144 `imp` rows with `arm="TV"`, and `runs/out/try_images/prompts/inv_00012_d0.json` reads `"arm": "TV", "image_shas": [], "prompt_notes": ["image_missing", "image_missing", "image_missing", "image_missing", "image_missing", "image_missing", ...]` — 6 of 6 cards degraded.

**How it fails.**

Run `python -m flowmirror.engine.loop runs/demo_three_arm_images.json --mock --days 5 --agents 60 --out runs/out/imgs`, with `images_root` pointing at the verified 801-file store. Every TV impression is delivered with zero image parts and `image_shas == []`. The modality analysis (`flowmirror/analysis/modality.py`) then computes the TV-vs-T and TV-vs-TC contrasts over agents whose TV cards carried no pixels at all, and reports a difference, an effect size and a bootstrap CI as if a modality manipulation had occurred. The number that fills `GAP_E1_PRIMARY` — the paper's single confirmatory endpoint — would be a comparison of text against text-minus-one-line.

**Paper claim at risk.** fundmarket-sim/paper/v7/sections/0_abstract.tex:16-17 ("text plus its cover image (\armTV)" ... "\armTV{} attaches one"); 4_interventions.tex:47 ("Under \armTV{} the slot carries the note's cover image"); 4_interventions.tex:65 ("Today the feed builder attaches one fixed image per card"); 6_experiments.tex:99 ("The feed renderer attaches a note's first image and never its others"); 8_limits.tex:21 ("image selection currently attaches one fixed picture per note"); 3_environment.tex:68 ("The feed as configured attaches one fixed image per note, setting \imgchoicefixed"). The confirmatory contrast $\dInput^{\mathrm{E1}}$ (4_interventions.tex:79) is defined as an attached cover image against text derived from the note's images; with the released engine its image side carries no image.

**Fix.**

In `_feed_card` (flowmirror/engine/loop.py:248-251) resolve the picture from the pool's actual schema: take `note["image_ids"][0]`, join it under `cfg["images_root"]`, and set `image_path` only when the file exists. Until that lands, make the silent degradation loud: count `image_missing` notes per arm in `run_meta.json` and refuse to start a TV-bearing run when `images_root` is set but zero paths resolve.

**Verification (confirmed).**

CONFIRMED on every link; could not refute.

(1) Pool key census, all 200 non-meta rows of data/creatives/cn/content_pool_v1_masked.jsonl: 200 rows have image_ids/image_sha256/n_images; ZERO have image_path, image, or cover. Exactly as claimed.

(2) Direct reproduction — instantiated _feed_card (flowmirror/engine/loop.py:248-251) against a real pool row with arm="TV":
    post[img]= True
    image_path= None image_sha= None
    T-only-extra-lines: ['配图不展示。']
    TV-only-extra-lines: []
The TV card is identical to the T card minus the single line "配图不展示。". _has_image (world.py:535-542) keys on n_images, so post["img"] is True for all 200 notes, guaranteeing the branch runs and always yields None.

(3) The engine never reads the image store at all. `grep -rn "images_root|image_pick|img_idx|image_sha_mismatch" --include=*.py flowmirror/` returns ZERO hits. Yet runs/demo_three_arm_images.json asserts as fact: "The TV arm resolves <images_root>/<image_id> and attaches the file only after its sha256 matches the pool's image_sha256 ... TV impressions log img_idx on the imp row." No such code exists; images_root and image_pick are inert config keys.

(4) The "store was missing" excuse is eliminated: the configured store D:/Desktop/ABM paper/fundmarket-sim/sim/content_pool_v1/images exists with 801 files, and a full resolve+hash pass gives `resolvable+sha_match 801 missing 0 mismatch 0`. Every pool image_id resolves with an exact sha256 match. The pixels are present; nothing looks for them.

(5) Committed run confirms end-to-end. runs/out/try_images/run_meta.json cfg.images_root points at that verified store; event_log.jsonl imp rows = Counter({'TV':144,'T':144,'TC':144}); the one dumped TV prompt (prompts/inv_00012_d0.json) has image_shas == [] and prompt_notes containing ('image_missing', 6) — 6 of 6 cards degraded, 0 prompts with non-empty image_shas.

(6) Nothing detects it. invariants_report.json holds 11 checks, none image-related (the single failure, a_lagged_signals_only, is unrelated). run_meta.counters has no image counter. No image warning string in event_log.jsonl. prompt.py:403-416 silently appends "image_missing" and continues.

(7) Tests confirm the blind spot rather than catching it. The only TV-attach test, tests/unit/test_modality.py:428, hand-builds `self._card(arm="TV", image_path=self.jpg)`, bypassing _feed_card. The loop.py:1267 self-test feeds a synthetic note with "image_path": "imgs/n1.jpg" — a key no shipped pool row carries. Both validate 


---

## 5. HIGH  -  Invariant (a) compares an ISO-week key against an ISO date, so it can never pass on a run longer than one day

**Where.** `flowmirror/engine/world.py` 750-752 (with flowmirror/engine/loop.py:908-910)

**What.**

loop.py:908-910 appends `{"t": t, "used": wk_prev, "live_end": dstr, "prev_live_end": (dt_cur - ONE_DAY).isoformat(), "day_keys": guba_day_keys}`. `wk_prev` is `_week_key(dt_cur - ONE_DAY)` (loop.py:153-155), which formats as `"2025-W40"`, while `prev_live_end` is `"2025-10-02"`. world.py:750-752 then asserts `e.get("used") == e.get("prev_live_end")` for every audit entry after the first — comparing a week key to a date, which is never equal — so `a_ok` is forced False on any run with two or more trading days. The second guard at world.py:753-755 is dead for the same reason, and it hides a latent `TypeError`: `int(e.get("day_keys", 0))` would be applied to `guba_day_keys`, which is a list, if the short-circuited left operand were ever true. So invariant (a), the first item in the paper's twelve-invariant list and the one that certifies no same-day signal leakage, is structurally unpassable and is currently the only reason the acceptance run exits 0 (see the polarity finding).

**How it fails.**

`python -m flowmirror.engine.loop runs/mock_10x3.json --mock --agents 40 --days 5` -> `invariants_report.json` records `a_lagged_signals_only: {"pass": false, "days_audited": 5}` while all ten other invariants pass. The same run at `--days 1` passes it, because `audit[1:]` is empty. There is no actual same-day leakage in the engine (`heat_prev`/`clim_prev`/`top_prev` are frozen copies at loop.py:911 and the `heat != heat_prev` guard at loop.py:1036 never fires); the check itself is comparing the wrong two fields. Reporting GAP_PLATFORM_AUDIT from any multi-day run today would print invariant (a) as FAILED and, per §5, require withdrawing every run in the paper.

**Paper claim at risk.** appendix.tex:145 invariant (a) "every displayed signal is computed from state at or before t-1"; 3_environment.tex:96; and GAP_PLATFORM_AUDIT's per-invariant pass/skip table in 5_validation.tex:17-18.

**Fix.**

Make the two sides the same type. Either log the lagged-signal week key on both sides (`"prev_live_end": _week_key(dt_cur - ONE_DAY)` at loop.py:909, so `used == prev_live_end` is a real assertion about the week actually consumed) or compare the date the ranking consumed against `prev_live_end` and check `used == _week_key(date.fromisoformat(e["prev_live_end"]))` in world.py:751. Also fix `int(e.get("day_keys", 0))` at world.py:754 to `len(e.get("day_keys") or ())` before the short circuit stops hiding it.

**Verification (confirmed).**

I tried to refute this and could not; the code and the repo's own committed run artifacts both confirm it.

1) Type mismatch is real. D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\loop.py:153-155:
    def _week_key(d):
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"
loop.py:873: `wk_prev = _week_key(dt_cur - ONE_DAY)   # lagged signal week (t=0 -> pre-window day)`, and loop.py:908-910 writes it into `used` beside an ISO date in `prev_live_end`:
    signal_audit.append({"t": t, "used": wk_prev, "live_end": dstr,
                         "prev_live_end": (dt_cur - ONE_DAY).isoformat(),
                         "day_keys": guba_day_keys})
So for the same day `used` is e.g. "2025-W40" while `prev_live_end` is "2025-10-02"; they can never be equal.

2) The check compares exactly those two fields. flowmirror/engine/world.py:748-755:
    audit = state.get("signal_audit") or []
    a_ok = len(audit) > 0
    for e in audit[1:]:
        if e.get("used") != e.get("prev_live_end"):
            a_ok = False
    for e in audit:
        if e.get("live_end") == e.get("used") and int(e.get("day_keys", 0)) > 0:
            a_ok = False
The first loop forces a_ok False whenever len(audit) >= 2. The second compares `live_end` (ISO date) to `used` (week key), so it is dead for the same reason, and its right operand `int(e.get("day_keys", 0))` would raise TypeError because loop.py:775 builds `guba_day_keys` as `sorted({k for e in (W.guba or {}).values() ...})`, i.e. a list. Confirmed latent (short-circuited), exactly as claimed.

3) Reproduced from committed outputs without a new run. Parsing every runs/out/*/invariants_report.json:
    mock_40x5      pass=False days_audited=5  all_passed=False
    mock_10x3      pass=False days_audited=5  all_passed=False
    demo_three-arm pass=False days_audited=5  all_passed=False
    demo_null / demo_two-arm / mock_3arm / mock_null  all pass=False, days_audited=5
    try_images     pass=False days_audited=3
    _probe_verify1 pass=True  days_audited=1  all_passed=True
    _probe_verify3 pass=True  days_audited=1  all_passed=True
Exactly the claimed pattern: every run with >=2 audited days fails (a) and only (a) (summary "passed": 10, "failed": 1); every 1-day run passes it because audit[1:] is empty.

4) No test catches it. tests/unit/test_invariant_wiring.py never exercises (a) against real audit data (it only asserts the key is present with a bool). world.py's self-test at world.py:1094-1095 feeds hand-matched str


---

## 6. HIGH  -  Invariant (l), the byte-identical-replay invariant the paper cites as its replay evidence, is not in the registry and is never checked

**Where.** `flowmirror/engine/world.py` 666-678

**What.**

`INVARIANTS` registers exactly eleven keys, a through k; there is no `l_` entry. `write_reports` writes one report entry per key in `set(INVARIANTS) | set(checks)` (world.py:854-857), and `check_invariants` never produces a replay entry, so `invariants_report.json` can never contain an (l) row — I confirmed the report summary reads `{"total": 11, ...}`. `loop.py:993` even names the invariant in a comment explaining why `cache_hit`/`attempts` are kept out of the `dec` row ("would break invariant (l) byte-identical logs"), so the engine is written as if (l) exists. The only replay comparison anywhere is the `--replay-check` CLI path (loop.py:1498-1512), which is an out-of-band double run, not an end-of-run invariant, and which does not write anything into the invariants report.

**How it fails.**

The appendix states "Twelve invariants are checked at the end of every run" and defines (l) as "a full-cache replay reproduces the event log byte for byte"; §8 says "whether that held is invariant (l) of GAP_PLATFORM_AUDIT". An author filling GAP_PLATFORM_AUDIT from `invariants_report.json` finds eleven entries and no (l), so either the table ships eleven rows against a text that promises twelve, or an (l) row is hand-written from a `--replay-check` console line that the report never recorded and that (per the polarity finding) exits 3 on a clean run. Either way the paper's central reproducibility claim has no machine-produced evidence behind it.

**Paper claim at risk.** appendix.tex:145 ("Twelve invariants are checked at the end of every run... (l) a full-cache replay reproduces the event log byte for byte") and 8_limits.tex:21 ("whether that held is invariant (l) of GAP_PLATFORM_AUDIT").

**Fix.**

Register `l_replay_byte_identical` in `world.INVARIANTS` and have `run_simulation` populate it: record `event_log_sha` plus the warm-run provider-call count into `run_meta.json`, and add an engine-side extra check in `_finish` that reports `{"pass": sha1 == sha2, "sha": ..., "warm_calls": ...}` when the run was launched under `--replay-check`, or `{"skipped": True, "reason": "single pass; run with --replay-check"}` otherwise. A skip with a reason is what the report format already expects for a non-applicable check.

**Verification (confirmed).**

I tried to refute this and could not — it reproduces exactly, both by reading and by running the engine.

1) Registry stops at (k). D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\world.py:666-678 defines `INVARIANTS = {...}` with exactly eleven keys, the last being `"k_dec_matches_active": "exactly one decision row exists per active agent per day"`. There is no `l_*` key anywhere in the file (`grep -n "l_"` on world.py returns no invariant key).

2) Nothing can inject an (l) row. `check_invariants` assigns exactly eleven keys (world.py lines 756, 758, 760, 773, 776, 778, 793, 794, 807/809/824/827, 835, 837 — a,b,c,d,e,f,g,j,h,i,k); no replay key. `_invariant_report` (world.py:846-877) iterates `for key in sorted(set(INVARIANTS) | set(checks)):`, so the union is bounded by those eleven. `write_reports` (world.py:886-888) dumps `{"summary": inv_summary, "checks": inv_entries, "event_log_sha256": ...}`.

3) Live confirmation. I ran `python -m flowmirror.engine.loop runs/mock_10x3.json --mock --days 3 --agents 10 --out <tmp> --replay-check`. Console: `[world] reports written to ...; status=invariant_failure (invariants: 10 pass, 1 fail, 0 skipped, 11 total)`. The written `invariants_report.json` reads `{'total': 11, 'passed': 10, 'failed': 1, 'skipped': 0, 'all_passed': False}` with keys `a_lagged_signals_only ... k_dec_matches_active` — no (l). Across the 42 committed reports under `runs/out/**`, 12 have `total: 11` and 30 (pre-R2F) have `total: 12` where the twelfth key is the bogus `"core"`, not an `l_` entry; none contains a replay invariant.

4) The engine and its own spec are written as if (l) exists. loop.py:993 comment "...which would break invariant (l) byte-identical logs", loop.py:539-540 "cache_hit/attempts stay OUT of dec by design (invariant l, byte-identical replays)", and docs/SANDBOX_INTERNAL_SPEC_v1.md:179 / docs/SANDBOX_STATUS_2026-09-05.md:93 both say "不变量 (a)–(l)". Meanwhile tests/unit/test_invariant_wiring.py:37 states plainly: "# The 11 registered invariants (flowmirror.engine.world.INVARIANTS)" and its REGISTRY tuple lists a–k only. No test anywhere asserts an (l) entry in the report.

5) The paper does promise twelve. D:\Desktop\ABM paper\fundmarket-sim\paper\v7\sections\appendix.tex:145: "Twelve invariants are checked at the end of every run: ... (l)~a full-cache replay reproduces the event log byte for byte." 8_limits.tex:21: "whether that held is invariant (l) of \gap{GAP_PLATFORM_AUDIT}". GAP_REPORT.md:136 names the source fi


---

## 7. HIGH  -  The event log records only the text half of the cache key: `image_shas` and the prompt degradation notes never reach any row

**Where.** `flowmirror/engine/loop.py` 996-1000 (and 948)

**What.**

The cache key is `sha256(model|temp|schema|prompt_sha|image_shas)` (runtime.py:216-218), and `decide` returns both halves in the record (`rec["prompt_sha"]`, `rec["image_shas"]`, `rec["notes"]["prompt_notes"]`). The `dec` row at loop.py:996-1000 logs `prompt_sha`, `raw_sha`, `status`, `arm`, `mood`, `reason`, `violations` and the six tallies — `image_shas` and `notes` are dropped. The `imp` row at loop.py:948 carries `t/d/i/p/arm/slot/source` and no image identity at all. Because `render_card` (prompt.py:322-331) adds no text for the TV arm, the prompt text — and therefore `prompt_sha` — is byte-identical whether a TV card attached its pixels or degraded to `image_missing`. Note also that `_record_from_row` (runtime.py:409-413) replaces `notes` with `{"replay": True}` on a cache hit, so even if the notes were logged, cold and warm runs would disagree; only dropping the field keeps the log identical.

**How it fails.**

Two runs of the same config, one with `images_root` populated and one without (or one before and one after the image store is re-encoded), produce `dec` rows with identical `prompt_sha` for every TV impression. The event logs differ only through the model's raw text, which under a temp-0.3 provider is noise. A reader auditing the released event log cannot determine which impressions carried pixels, and the invariant list contains nothing that would. Given the previous finding — the shipped pool attaches zero images — this is the mechanism by which an all-text TV run passes as an image run.

**Paper claim at risk.** 3_environment.tex:94 (cache keyed by prompt text and attached image hashes) and 4_interventions.tex:63 ("the chosen index and the image hash are logged on the impression event, so a replay reproduces the assignment").

**Fix.**

Add `img=list(rec.get("image_shas") or ())` and `deg=[n for n in prompt_notes if not n.startswith("channel_sha:")]` to the `dec` row at loop.py:996-1000, sourcing the notes from `build_decision_messages` on the frozen job (which is pure, per loop.py:33-35) rather than from `rec["notes"]`, so the value is identical on the cold and warm paths. Add the per-impression image hash to the `imp` row at loop.py:948. Extend `config/schemas/event.schema.json` accordingly — it already reserves `img_idx` on `imp` rows at line 132.

**Verification (confirmed).**

CONFIRMED — I tried to refute it on four fronts (other log rows, invariants, reports, tests) and every route failed.

1) The cache key does split text/image, and `decide` does return both halves.
`D:\Desktop\ABM paper\flowmirror_v7\flowmirror\agents\runtime.py:216-218`:
    def key_for(model, temp, prompt_sha, image_shas):
        return sha256_text(f"{model}|{temp}|{SCHEMA_VERSION}|{prompt_sha}|"
                           + ",".join(image_shas or []))
`runtime.py:457-459` (cold path): `"prompt_sha": prompt_sha, ... "image_shas": img_shas, "cache_hit": False, "attempts": total_attempts, "notes": notes` with `notes = {"mode":..., "prompt_notes": list(prompt_notes or [])}` (`runtime.py:432`).

2) The `dec` row drops exactly the image half. `flowmirror\engine\loop.py:996-1000`:
    logd("dec", t=t, d=dstr, i=inv.id, prompt_sha=rec.get("prompt_sha"),
         raw_sha=rec.get("raw_sha256"), status=rec.get("parser_status"),
         arm=inv.arm, mood=(row or {}).get("mood"), reason=(row or {}).get("reason"),
         violations=list(rec.get("violations") or ()),
         **_dec_counts(adapted, len(job["cards"]), bool(cfg.get("social"))))
No `image_shas`, no `notes`. `loop.py:948`: `logd("imp", t=t, d=dstr, i=inv.id, p=pid, arm=arm, slot=s, source=source)` — carries the arm LABEL but no image identity, even though the card itself holds `image_path`/`image_sha` (`loop.py:256-257`).

3) The TV prompt text is byte-identical with or without pixels. `prompt.py:322-331` has branches only for `arm == "T"` and `arm == "TC"`; TV falls through with no line, and only `sha_blocks` (text) is hashed (`prompt.py:426`). I ran it: a TV card with a real JPEG and the same card with a missing path produce
    render equal: True
    prompt_sha equal: True  f91fd21ab240 / f91fd21ab240
    image_shas: ['cb0501d6...'] vs []
    notes: [] vs ['image_missing', ...]
So the two runs differ in the two fields that are never logged, and agree in the one that is.

4) Nothing else recovers it. `world.py:666-677` INVARIANTS registers a..k; `h_arm_balance` checks only the balance of the arm LABEL from `imp` rows (`world.py:718-719`, `imp_arms[r.get("i")][r.get("arm") or "T"] += 1`) — precisely the label an all-text run also emits. `write_reports` records `modality.arms`/`run_arm` (`world.py:914-916`), not attachments. No test asserts image identity in the log: `tests/unit/test_dump_prompt.py:82` checks `"image_shas" in car` for the one-agent `--dump-prompt` SIDECAR only, then asserts `decs[0].get


---

## 8. HIGH  -  The invariant exit code is polarity-inverted: a failing run exits 0, a clean run exits 4

**Where.** `D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\loop.py` 839 (with flowmirror/engine/world.py:839-842)

**What.**

world.check_invariants builds `core` as the LIST of fatally failing invariant keys and returns `bool(core)` (world.py:839-842), i.e. core==True means AT LEAST ONE INVARIANT FAILED. world.py's own self-test pins that reading (`chk("invariants_clean_passes", cf is False ...)`, `chk("invariants_flag_redeem_block", bf is True ...)` at world.py:1115-1123). loop._finish consumes it with the opposite meaning: `ok = bool(core) and all(bool(v.get("pass", True)) for v in extras.values())` (loop.py:839), then `if ok: return 0` / else `return 4`. So `ok` is True exactly when the world found fatal failures. The (checks, core) tuple is unpacked correctly and the checks half does drive the report file and the console line, but the core half drives the exit code backwards, which is the half b6f93e9 claims to have fixed. I ran the shipped reference config (runs/mock_10x3.json, 40 agents x 5 days, mock_llm) and the engine printed `invariants=FAIL(a_lagged_signals_only)`, wrote run_meta.json `status=invariant_failure` and invariants_report.json `"all_passed": false` -- and run_simulation returned 0. Monkeypatching check_invariants to return all-pass checks with core=False gives the mirror image: `invariants=FAIL()`, `engine exit code 4: invariant failures: ` with an EMPTY key list, rc=4. The `if not fails:` branch in _print_summary (loop.py:1141-1143) that prints "core=False but no individual check entry is failing" is that inverted state being papered over as a printing case. main() returns this rc straight to SystemExit (loop.py:1513), and --replay-check gates on it (`if run_simulation(cfg, rt) != 0: return 3`, loop.py:1500), so any harness or reviewer keying on the process exit code -- the contract stated verbatim in run_simulation's own docstring at loop.py:717-720 -- accepts a run whose invariants failed.

**How it fails.**

Run `python -m flowmirror.engine.loop runs/mock_10x3.json --mock --agents 40 --days 5`. Invariant a_lagged_signals_only fails, invariants_report.json records failed=1 / all_passed=false, run_meta.json records status=invariant_failure, and the process exits 0. A batch script that runs the E1/E2 grid and keeps every run whose exit status is 0 keeps every invariant-violating run. Conversely, once a_lagged_signals_only is repaired the same command on a fully clean run exits 4 and prints `engine exit code 4: invariant failures: ` with no key named, and --replay-check aborts with code 3 on every clean run before it ever compares the two event-log hashes.

**Paper claim at risk.** paper/v7/sections/5_validation.tex:18 -- "before that repair the engine discarded the value the invariant checker returned and printed a pass whatever the checks found, so no invariant claim in this paper rests on a run that predates it." GAP_REPORT.md:136 (GAP_PLATFORM_AUDIT) -- "No invariant claim may rest on a run predating the R2F fix ... which discarded every invariant result". The R2F fix is presented as the boundary that makes post-fix runs trustworthy; post-fix runs still return success while failing.

**Fix.**

loop.py:839 -- `ok = (not core) and all(bool(v.get("pass", True)) for v in extras.values())`. Add a regression test that monkeypatches check_invariants to return (all-passing checks, False) and asserts rc == 0, and one returning (checks with a False entry, True) asserting rc == 4; the current tests only cover the (failing checks, False) combination and therefore pass under both polarities.

**Verification (confirmed).**

Every attempt to refute failed; the polarity inversion is real and I reproduced both directions.

1) World-side semantics (D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\world.py:839-842) — `core` is the LIST of fatally failing keys, so True == FAILURE:
```
    big = len(agents) >= 300                      # (e) fatal only at n_agents >= 300
    core = [k for k, v in checks.items() if v.get("pass") is False
            and not (k == "e_all_cells_exposed" and not big)]
    return checks, bool(core)
```
`grep -rn "def check_invariants"` returns exactly one definition (world.py:700), imported by loop.py:106 — there is no wrapper that could re-invert it. world.py's own self-test (world.py:1115-1123) pins that reading: `chk("invariants_clean_passes", cf is False ...)` and `chk("invariants_flag_redeem_block", bf is True ...)`.

2) Loop-side consumption (loop.py:839, inside `_finish`) uses the opposite meaning:
```
        ok = bool(core) and all(bool(v.get("pass", True)) for v in extras.values())
```
followed by `if ok: return 0` and otherwise `print("engine exit code 4: invariant failure...")` / `return 4`. So `ok` is True exactly when the world found fatal failures. The tuple unpack at loop.py:826 (`checks, core = raw`) and the TypeError guard are correct — only the `core` half is consumed backwards.

3) Empirical, shipped reference config:
```
$ python -m flowmirror.engine.loop runs/mock_10x3.json --mock --agents 40 --days 5
[world] reports written to runs\out\mock_10x3; status=invariant_failure (invariants: 10 pass, 1 fail, 0 skipped, 11 total)
invariants=FAIL(a_lagged_signals_only)
invariant FAIL a_lagged_signals_only: {"days_audited": 5, ... "pass": false}
REAL_RC=0
```
invariants_report.json summary: `{'total': 11, 'passed': 10, 'failed': 1, 'skipped': 0, 'all_passed': False}`, run_meta.json `status=invariant_failure` — process exit 0.

4) Mirror image, monkeypatching `check_invariants` to `({'a_lagged_signals_only':{'pass':True}}, False)` (a fully clean run):
```
invariants=FAIL()
invariant FAIL core: {"core": false, "note": "core=False but no individual check entry is failing"}
engine exit code 4: invariant failures: 
RC= 4
```
The `if not fails:` branch at loop.py:1141-1143 is indeed the inverted state being printed as a "case" rather than caught.

5) The covering test does NOT refute it — it encodes the wrong convention and passes with the bug in place. D:\Desktop\ABM paper\flowmirror_v7\tests\unit\test_invariant_wiring.py: `test_failing_invaria


---

## 9. HIGH  -  The same-day-leakage clause of invariant (a) is unreachable, and would raise TypeError if it were ever reached

**Where.** `D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\world.py` 754

**What.**

world.py:753-754 is the clause that is supposed to catch actual same-day leakage: `for e in audit: if e.get("live_end") == e.get("used") and int(e.get("day_keys", 0)) > 0: a_ok = False`. `live_end` is a date string (loop.py:908, `dstr`) and `used` is a week key (loop.py:908, `wk_prev`), so the left operand of the `and` is always False and Python short-circuits before evaluating the right. That short-circuit is the only thing keeping the run alive, because `day_keys` is a LIST -- loop.py:775-776 sets `guba_day_keys = sorted({k for e in (W.guba or {}).values() if isinstance(e, dict) for k in e})`, which in the reference run is a 13-element list of week labels -- and `int([...])` raises TypeError. So the clause is simultaneously dead and, on the day the first condition is fixed, a crash.

**How it fails.**

Fix finding 2 by making `used` and `prev_live_end` comparable in the week-key domain. On the next run `live_end` (still a date) is compared to `used` (a week key) and the clause stays dead; if instead someone normalises `live_end` to a week key too, the `and` short-circuit falls away, `int(['2025-W41', ...])` executes, and check_invariants raises TypeError inside _finish, aborting every run after the last trading day with the event log already closed and no invariants_report.json written.

**Paper claim at risk.** paper/v7/sections/appendix.tex:145 invariant (a) and paper/v7/sections/3_environment.tex:96 -- the audit is claimed to detect a signal that postdates $t-1$. The clause that would detect it never executes.

**Fix.**

world.py:754 -- compare like-for-like and count the list instead of coercing it: `if e.get("live_end") == e.get("used") and len(e.get("day_keys") or []) > 0:`, with `live_end` and `used` normalised to the same key domain as in finding 2. Add a self-test row that constructs an audit entry with live_end == used and a non-empty day_keys and asserts (a) goes False.

**Verification (confirmed).**

Tried hard to refute; every step reproduces.

1) The operands are in different domains, so the left conjunct is a constant False. D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\loop.py:908-910 is the only writer of signal_audit:
    signal_audit.append({"t": t, "used": wk_prev, "live_end": dstr,
                         "prev_live_end": (dt_cur - ONE_DAY).isoformat(),
                         "day_keys": guba_day_keys})
`wk_prev = _week_key(dt_cur - ONE_DAY)` (loop.py:873) and `_week_key` (loop.py:153-155) returns `f"{y}-W{w:02d}"`, while `dstr` is an ISO date. "2025-10-09" can never equal "2025-W41", so `e.get("live_end") == e.get("used")` at world.py:754 is always False and Python short-circuits. The clause is dead.

2) day_keys is a list, so the right conjunct would raise. loop.py:775-776:
    guba_day_keys = sorted({k for e in (W.guba or {}).values()
                            if isinstance(e, dict) for k in e})
Against the shipped data/attention/guba_signal_v1.json this is exactly a 13-element list of week labels ['2025-W41' ... '2026-W01'], and int(that_list) raises TypeError: int() argument must be a string, a bytes-like object or a real number, not 'list'.

3) Executed both paths through the real check_invariants:
 - audit row as the engine actually builds it (live_end='2025-10-09', used='2025-W41', day_keys=[13 week labels]) -> returns {'pass': True, 'days_audited': 1, ...}, no crash: the clause never ran.
 - same row with live_end normalised to the week-key domain ('2025-W41') -> TypeError: int() argument must be a string, a bytes-like object or a real number, not 'list' propagates out of check_invariants.

4) The failure scenario's blast radius is accurate. In loop.py _finish (803-841): `elog.close()` is line 806, `raw = check_invariants(state, os.path.join(out_dir, "event_log.jsonl"), cfg)` is line 819, and `write_reports(...)` is line 841. A TypeError at 819 aborts after the event log is closed and before invariants_report.json is written.

5) Nothing covers it. `grep -rn day_keys tests/` returns nothing; the in-module selftest (world.py:1094-1095) feeds "day_keys": 2 / 1 (ints) with used/live_end values ("aa"/"bb", "bb"/"cc") that are never equal, so it exercises neither the dead branch nor the int(list) path.

Net effect today: the registry description at world.py:667 promises "no same-day leakage", but the only clause that would detect it can never execute, and it is a crash the moment the domain mismatch on the first conjunct is rep


---

## 10. HIGH  -  Invariant (c) reports a vacuous PASS on a branch the cohort can never enter, where the paper promises a SKIP

**Where.** `D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\world.py` 760

**What.**

world.py:760-761 writes `checks["c_hard_block_never_subscribes"] = {"pass": c_viol == 0, "violations": c_viol, "hard_block_checkouts": co_oc["hard_block"]}`. There is no skip branch: when `hard_block_checkouts == 0` -- no hard-block checkout ever occurred, so no settle-despite-block could occur -- the entry is `pass: true`. I confirmed this on the reference mock run: the entry is `{"pass": true, "hard_block_checkouts": 0, "violations": 0}` while checkout_oc contained only confirm_declined/confirm_signed/match. This is exactly the pass/skip conflation the lens asks about, and it is the one case the paper singles out. The engine also does not carry the other three counts the appendix says are printed beside (c): confirmation prompts raised, declined, and signed appear in run_meta.json's checkout_oc but never in the (c) entry.

**How it fails.**

The paper states at 3_environment.tex:89 that "$\Cclass{1}$ investors are absent from the cohort, so the hard-block branch is implemented but never observed." Every live run therefore produces `c_hard_block_never_subscribes: pass=true, hard_block_checkouts=0`. GAP_PLATFORM_AUDIT is filled from `runs/*/invariants_report.json` (GAP_REPORT.md:136), so the audit table in Section 5 will print invariant (c) as PASSED, asserting that the confirmation mechanic was verified when zero confirmation blocks were ever raised.

**Paper claim at risk.** paper/v7/sections/appendix.tex:147 -- "An invariant that no event ever reaches is satisfied vacuously, so the audit prints, beside invariant (c), the number of confirmation prompts raised, the number declined, the number signed and the number of blocked settlements attempted, and marks the invariant a skip rather than a pass where the branch is never entered." The engine marks it a pass.

**Fix.**

world.py:760 -- when `co_oc["hard_block"] == 0`, emit `{"skipped": True, "reason": "no hard-block checkout was raised in this run; the settle-despite-block branch was never entered", ...}` instead of `pass`. Carry `confirm_signed`, `confirm_declined` and the blocked-settlement attempt count from `co_oc` into the entry so the four counts the appendix promises actually reach invariants_report.json. Apply the same treatment to b_nonholder_never_redeems (world.py:758), which is vacuous whenever the run logs no redemption.

**Verification (confirmed).**

CONFIRMED as stated. Four independent checks, none of which refuted it.

1) The code is exactly as claimed. D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\world.py:760-761:

    checks["c_hard_block_never_subscribes"] = {"pass": c_viol == 0, "violations": c_viol,
                                               "hard_block_checkouts": co_oc["hard_block"]}

There is no `if co_oc["hard_block"] == 0: skipped` branch. Compare (h), the only invariant that does emit a skip (world.py:809 and 824): `checks["h_arm_balance"] = {"skipped": True, "level": "run", ... "reason": ...}` — so the engine has the skip idiom and simply does not apply it to (c). c_viol itself can only be non-zero via hard_p, which is populated only by `if oc == "hard_block": hard_p.add((r.get("i"), r.get("p")))` (world.py:~727), so with zero hard_block checkouts the second pass over `rows` (world.py:743-745) cannot increment c_viol: `pass: true` is the only reachable value.

2) The branch is unreachable by construction, not by accident. flowmirror/regulator/cn_cxr.py:17-18: "ONLY C1 x R>1 yields ``hard_block``. The v7 cohort contains no C1 clients, so ``hard_block`` is never observed in the experiment". flowmirror/regulator/base.py:15 repeats it: "hard_block C1 x R>1 (unreachable in the v7 cohort; see cn_cxr)". So every live run pins (c) to a vacuous pass.

3) The reference run reproduces it. runs/out/mock_40x5/invariants_report.json: `"c_hard_block_never_subscribes": {..., "pass": true, "hard_block_checkouts": 0, "violations": 0}`, while runs/out/mock_40x5/run_meta.json:1369-1373 shows `"checkout_oc": {"confirm_declined": 4, "confirm_signed": 4, "match": 12}` — eight confirmation prompts were actually raised and none of them is reflected in the (c) entry. The report summary counts it under `"passed"` (skipped entries are counted separately at world.py:876-877), and nothing downstream converts it: loop.py:1130-1134 only distinguishes pass from fail on entries the report already carries, and no script rewrites the entry.

4) The paper does promise a skip, verbatim, and the four counts. paper/v7/sections/appendix.tex:147: "An invariant that no event ever reaches is satisfied vacuously, so the audit prints, beside invariant (c), the number of confirmation prompts raised, the number declined, the number signed and the number of blocked settlements attempted, and marks the invariant a skip rather than a pass where the branch is never entered." The engine prints one of those four (hard_block_chec


---

## 11. HIGH  -  Registry key (h) checks modality-arm balance; the paper's invariant (h) claims persona-cell cohort composition, which no invariant checks

**Where.** `D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\world.py` 674

**What.**

world.INVARIANTS["h_arm_balance"] (world.py:674) is "modality arms are balanced: overall share within tolerance of 1/k and each cell as balanced as its size permits", and the implementation (world.py:800-830 -> channels/feed.py:425 check_arm_balance) verifies, for each population cell, that each MODALITY ARM's within-cell share is within `_cell_tolerance(n_cell, k)` of 1/k, where n_cell is the number of agents in that cell (feed.py:409-422). The paper's invariant (h) is a different claim about a different quantity: cohort composition against a target share, with tolerance $1/(2\ncells)$ where ncells is the NUMBER OF CELLS, not the size of one. Nothing in world.INVARIANTS checks that any persona cell's realised share matches its target population share; sampler.py computes post-stratification weights but writes no invariant entry into invariants_report.json. On the reference 40-agent run the (h) report came back `worst_cell_dev: 0.5, worst_cell_tolerance: 0.500000001, ok_cells: true` -- a size-1 cell whose tolerance is by construction 0.5, i.e. unfalsifiable at that cell size, which is not what a cohort-composition bound would look like.

**How it fails.**

A reader checks Appendix G invariant (h) against `runs/*/invariants_report.json` and finds an entry named h_arm_balance whose report block contains n_tv / tv_share / worst_cell_dev for the modality arms and no persona-cell target share anywhere. The paper's stated cohort-composition guarantee has no evidence behind it, and the pass it does show is a pass of a different check.

**Paper claim at risk.** paper/v7/sections/appendix.tex:145 invariant (h) -- "cohort composition matches the analysis plan fixed on 2026-09-06, each persona cell deviating from its target share by at most $1/(2\ncells)$ under within-cell stratified block randomisation across runs"; paper/v7/sections/3_environment.tex:96 -- "cohort balance is stated there as a maximum admissible per-cell deviation".

**Fix.**

Either add a distinct registry key that checks realised persona-cell shares against the sampler's target shares with the $1/(2\ncells)$ tolerance the appendix states, or rewrite appendix.tex:145 item (h) to describe what h_arm_balance actually verifies (within-cell modality-arm balance) and say where cohort composition is checked instead.

**Verification (confirmed).**

CONFIRMED — I tried to refute it three ways (maybe the paper sentence is loose wording for the same arm check; maybe a cohort-composition invariant lives elsewhere; maybe the reference-run numbers were misread) and all three fail.

1) What the code checks. D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\world.py:674 registers: "h_arm_balance": "modality arms are balanced: overall share within tolerance of 1/k and each cell as balanced as its size permits (per-impression shares at exposure level)". world.py:797-804 dispatches to feed.check_arm_balance(state.get("agent_arms") or {}, id2cell, arms=arms). D:\Desktop\ABM paper\flowmirror_v7\flowmirror\channels\feed.py:423-449 docstring: "Verify arm invariant (h) for stratified block randomisation ... WITHIN EACH CELL, every arm's share is within _cell_tolerance(n_cell, k) of 1/k", and _cell_tolerance (feed.py:409-421) returns "max(1.0 / (2.0 * n_cell), reach / (k * n_cell)) + 1e-9" where n_cell is the NUMBER OF AGENTS IN THAT CELL. tests/unit/test_arm_balance.py's docstring confirms the object under test is "agent-level arm randomisation against PREREG B10" — arms, not cohort shares. docs/PREREG_v1.5_DRAFT.md:19 likewise defines 不变量 (h) as 三臂份额 |·−1/3| ≤ 0.03.

2) What the paper claims. D:\Desktop\ABM paper\fundmarket-sim\paper\v7\sections\appendix.tex:145 item (h): "cohort composition matches the analysis plan fixed on 2026-09-06, each persona cell deviating from its target share by at most $1/(2\ncells)$ under within-cell stratified block randomisation across runs". macros.tex:33 sets \ncells = 36, so the stated bound is 1/72 = 0.0139, keyed to the NUMBER of cells, not to any cell's size. sections/4_interventions.tex:51 makes the divergence deliberate: "the arm-share and per-arm cohort constants no longer apply, and cohort balance is stated instead as a maximum admissible per-cell deviation of $1/(2\cdot\ncells)$", echoed at sections/3_environment.tex:96 ("The \preregver{} between-agent arm-share and per-arm cohort constants do not apply to this design and are retired"). So the paper declares the arm constants RETIRED while the only shipped invariant (h) is exactly that retired arm check — which closes off the charitable "it's the same check, loosely worded" reading.

3) No cohort-composition invariant exists. world.INVARIANTS holds 11 keys (a–k; no l) and none concerns persona-cell realised vs target share. flowmirror/population/sampler.py:386-410 has self-checks ("all 36 cells present, n_sample >= decl


---

## 12. HIGH  -  The appendix claims twelve invariants checked at the end of every run; the registry has eleven and invariant (l) never appears in any report

**Where.** `D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\world.py` 666-678

**What.**

world.INVARIANTS (world.py:666-678) contains exactly 11 keys, a_ through k_. _invariant_report emits one entry per registry key (world.py:855-856), so invariants_report.json always has 11 entries -- I confirmed `"total": 11` on the reference run. The paper's Appendix G enumerates twelve, (a) through (l), with (l) being "a full-cache replay reproduces the event log byte for byte". That check exists only as the CLI `--replay-check` path in main() (loop.py:1498-1512), which compares two event-log SHA-256s and returns 0 or 3; it never writes an entry into invariants_report.json and has no registry key. GAP_REPORT.md:136 sources GAP_PLATFORM_AUDIT from `runs/*/invariants_report.json` and `run_meta.json` and asks for "per-invariant pass or skip", which for (l) cannot be produced from those files. Separately, appendix (j) is stated as "every displayed comment has a logged source", while the registry's j_displayed_comment_matches_prev_day (world.py:676, evaluated at world.py:794) checks that every displayed comment appears verbatim in the post's day-(t-1) comment set -- a different property; nothing checks that a displayed comment carries a logged source.

**How it fails.**

Filling GAP_PLATFORM_AUDIT from the run outputs produces an eleven-row table. Either the paper prints eleven rows against a text that says twelve, or a twelfth row for (l) is filled from the --replay-check console output while the surrounding sentence says the record comes from the per-invariant audit. A reader who greps the released runs for an (l) entry finds none. The (j) row will be printed against a check that verifies a different property than the appendix describes.

**Paper claim at risk.** paper/v7/sections/appendix.tex:145 -- "Twelve invariants are checked at the end of every run: ... (l)~a full-cache replay reproduces the event log byte for byte" and appendix.tex:147 -- "The per-invariant pass or skip record ... are \gap{GAP_PLATFORM_AUDIT}"; paper/v7/sections/8_limits.tex:21 -- "whether that held is invariant (l) of \gap{GAP_PLATFORM_AUDIT}".

**Fix.**

Register an `l_replay_byte_identical` key in world.INVARIANTS and have main()'s --replay-check path write its outcome (pass / skip-with-reason when replay was not requested) into the second run's invariants_report.json, so all twelve letters are present with an explicit pass or skip. Correct the appendix (j) wording to match j_displayed_comment_matches_prev_day, or add the logged-source check the appendix describes.

**Verification (confirmed).**

I tried to refute this three ways (a hidden 12th registry key, an (l) entry injected at report time, and a 12-entry report on disk) and all three attempts failed.

1) Registry is 11, not 12. `D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\world.py:666-678` — `INVARIANTS = {` ... keys `a_lagged_signals_only` through `k_dec_matches_active`, ending at `"k_dec_matches_active": "exactly one decision row exists per active agent per day",`. Exactly eleven keys, no `l_*`. `grep -rn "l_replay\|\"l_\|'l_" flowmirror/` over the whole package returns nothing.

2) The report is registry-driven, so 11 is the ceiling. `world.py:855-856` in `_invariant_report`: `for key in sorted(set(INVARIANTS) | set(checks)):` — the only way a 12th row appears is if `check_invariants` returns an unregistered key, and it returns none.

3) On-disk confirmation. Across all 42 `invariants_report.json` under `flowmirror_v7/runs/`, the key sets are exactly two: the 11 registry keys, or those 11 plus a stray `core` key (a stale artifact of the pre-R2F tuple/dict bug, written with the fallback description "unregistered invariant"). No file anywhere contains an (l)/replay entry. `runs/out/mock_40x5/invariants_report.json` summary is `{'total': 11, 'passed': 10, 'failed': 1, ...}`.

4) The repo's own test agrees. `flowmirror_v7\tests\unit\test_invariant_wiring.py:36` — "# The 11 registered invariants (flowmirror.engine.world.INVARIANTS); mirrored here so this test fails loudly if the registry and the wiring drift apart." followed by an 11-element REGISTRY tuple ending at `"k_dec_matches_active"`.

5) (l) exists only as an exit code. `flowmirror\engine\loop.py:1498-1512` runs the sim twice, compares `event_log_sha`, prints `replay-check identical=...` and `return 0 if same else 3`. It writes nothing into `invariants_report.json` and has no registry key.

6) The paper does assert twelve, checked every run. `D:\Desktop\ABM paper\fundmarket-sim\paper\v7\sections\appendix.tex:145` — "Twelve invariants are checked at the end of every run: (a)...(l)~a full-cache replay reproduces the event log byte for byte." And `8_limits.tex:21` routes (l) through the audit slot: "whether that held is invariant (l) of \gap{GAP_PLATFORM_AUDIT}, whose replay hashes are reported there". `GAP_REPORT.md:136` sources that slot from `runs/*/invariants_report.json`, `run_meta.json` and demands "Per-invariant pass or skip with the reason" — which for (l) those files cannot supply.

Partial mitigation found (does not refu


---

## 13. HIGH  -  The TV arm never attaches an image with the shipped content pool: TV is byte-identical to T

**Where.** `flowmirror/engine/loop.py` 250 (_feed_card); consumed at flowmirror/agents/prompt.py:403-414

**What.**

_feed_card resolves a card's picture as `image_path = note.get("image_path") or note.get("image") or note.get("cover")` (loop.py:250). The production pool rows in data/creatives/cn/content_pool_v1_masked.jsonl carry none of those keys — they carry `image_ids` (e.g. ['68be3ddd000000001b01e4af_0.jpg']) and `image_sha256`. There is no resolver from image_id to a file anywhere in the package: `grep -rn 'images_root|image_pick|img_idx' flowmirror/` returns nothing (the only hits are in data_pipeline/cn/caption_frozen.py, the offline captioner). So card['image_path'] is None for every note; prompt.build_decision_messages then takes the `else` branch at prompt.py:413-414 and appends the note 'image_missing' instead of an image part. I verified this directly by feeding a real pool row through _feed_card: image_path=None, image_sha=None. The DEV_HANDOVER §10 claim that the engine side of the image surface is NOT landed is therefore correct, notwithstanding the HEAD commit message b6f93e9 'land image config surface'. config/engine_defaults.yaml:33-56 additionally documents behaviour that does not exist: a run-start WARNING, an `m_tv_arm_carries_images` invariant (absent from world.INVARIANTS, world.py:666-678) and an `img_idx` field on the imp row (never emitted).

**How it fails.**

Run runs/demo_three_arm.json (arms T/TC/TV) to completion. Every TV impression renders exactly the same text parts as a T impression and carries zero image parts, so prompt_sha for a TV card equals the T card's and image_shas is empty. The estimate reported as dInput^{E1} (armTV minus armTCf, the paper's headline contrast in 0_abstract.tex:14-18 and 6_experiments.tex:21) is then a contrast between text-plus-OCR and text, with the picture never delivered — a structural zero that the paper would read as 'the image does not change what the agent does'. Nothing in the run output contradicts it: no invariant covers image delivery and the 'image_missing' note is discarded (see separate finding).

**Paper claim at risk.** 0_abstract.tex:14-18, 3_environment.tex:50 (property ii/iii), 6_experiments.tex:21 — dInput^{E1}/dInput^{E2} as a text-vs-pixels contrast; GAP_ARM_DELIVERY.

**Fix.**

Resolve card images from the pool's image_ids against cfg['images_root'] (verifying each file against image_sha256) inside _feed_card, honour cfg['image_pick'] and log the chosen index as img_idx on the imp row; add the m_tv_arm_carries_images invariant that world.py and engine_defaults.yaml already reference so a run with zero attached TV images fails loudly instead of silently producing a null.

**Verification (partly).**

CORE DEFECT CONFIRMED AND REPRODUCED; one stated consequence ("byte-identical to T", "prompt_sha equal") is REFUTED.

1) The resolver is exactly as described. flowmirror/engine/loop.py:248-250:
    image_path = None
    if post.get("img"):
        image_path = note.get("image_path") or note.get("image") or note.get("cover")

2) The shipped pool carries none of those keys. Enumerating the union of keys over all 201 lines of data/creatives/cn/content_pool_v1_masked.jsonl yields: ['_meta','caption','caption_len','caption_masked','collects','comments','common_support_codes','concept_words','desc_source','engagement_source','fund_codes_malformed','fund_codes_valid','generated_at','has_link','image_ids','image_sha256','intent','intent_group','likes','mask_rule_version','n_images','n_masked_caption','n_masked_ocr','note_id','ocr_masked','ocr_text','org','per_org','pool_sha256','pre_window','primary_label','resized','rule','script','shares','source','source_sha256','submit_time','title','totals','version'] — image_ids/image_sha256 present, image_path/image/cover absent.

3) Reproduced directly: feeding the first real pool row (note_id 68be3ddd000000001b01e4af, n_images=1, image_ids=['68be3ddd000000001b01e4af_0.jpg']) through _feed_card with post={'img':True} and arm='TV' returns image_path=None image_sha=None.

4) No resolver anywhere in the package: `grep -rn "images_root\|image_pick\|img_idx\|image_ids\|image_sha256" flowmirror/` exits 1 with zero hits. All hits in the repo are in config/schemas, config/engine_defaults.yaml, docs/, and data_pipeline/cn/caption_frozen.py (the offline captioner) / import_from_research.py.

5) The three documented-but-absent behaviours check out:
   - imp row (loop.py:948): `logd("imp", t=t, d=dstr, i=inv.id, p=pid, arm=arm, slot=s, source=source)` — no img_idx, ever, despite config/schemas/event.schema.json:132 defining it.
   - world.INVARIANTS (flowmirror/engine/world.py:666-678) contains exactly 11 keys a_lagged_signals_only .. k_dec_matches_active; m_tv_arm_carries_images is absent (it appears only in engine_defaults.yaml, run.schema.json, docs/RUNBOOK.md).
   - The only "WARNING" print in the engine is flowmirror/engine/world.py:326 (synthetic demo NAVs). There is no run-start images_root warning.
   - images_root/image_pick are valid run-schema keys (run.schema.json:256-266) and runs/demo_three_arm_images.json sets images_root to a real local store — but the engine never reads either key, so even a correctly-configured run a


---

## 14. HIGH  -  Invariant (a) compares an ISO week key to an ISO date, so it is False on every run and can never detect a real leak

**Where.** `flowmirror/engine/world.py` 748-757

**What.**

check_invariants builds a_ok from the signal_audit rows: `for e in audit[1:]: if e.get("used") != e.get("prev_live_end"): a_ok = False` (world.py:750-752) and `for e in audit: if e.get("live_end") == e.get("used") ...` (753-755). The producer, loop.py:908-910, writes `used = wk_prev = _week_key(dt_cur - ONE_DAY)` — an ISO WEEK key such as '2025-W41' (loop.py:150-152) — while prev_live_end and live_end are ISO DATE strings such as '2025-10-09'. The two can never be equal, so the first loop forces a_ok False on any run of two or more days and the second condition can never fire. Confirmed on disk: runs/out/mock_3arm, demo_three-arm and mock_40x5 all report a_lagged_signals_only pass:false with summary all_passed:false. The check also inspects only the guba week key; it never touches NAV, heat, climate or comment state, so even a corrected comparison would not test what the paper says invariant (a) tests.

**How it fails.**

Any multi-day run reaches _finish, ok is False, and the engine prints 'engine exit code 4: invariant failure: a_lagged_signals_only' and exits 4 — every run in the paper's budget is flagged as an invariant failure for a reason that has nothing to do with lag. Conversely, if a genuine same-day leak were introduced into the ranking inputs, a_lagged_signals_only would report exactly the same False, so the check cannot distinguish a leak from its own type error. GAP_PLATFORM_AUDIT ('per-invariant pass or skip with the reason') would have to record the paper's flagship timing invariant as failing on every accepted run.

**Paper claim at risk.** appendix.tex:145 invariant (a); 3_environment.tex:96; 5_validation.tex:16; GAP_PLATFORM_AUDIT.

**Fix.**

Compare like with like: either record `used_week` alongside `prev_live_end_week = _week_key(dt_cur - ONE_DAY)` and compare week to week, or have the loop emit prev_live_end already reduced to its week key. Then extend the check beyond the guba key to the channels invariant (a) is claimed to cover — the NAV/return fields and the heat map — so a real same-day read fails it.

**Verification (partly).**

CORE DEFECT: CONFIRMED — the type mismatch is real and invariant (a) is False on every multi-day run.

Producer (D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\loop.py):
  873:  wk_prev = _week_key(dt_cur - ONE_DAY)   # lagged signal week
  908:  signal_audit.append({"t": t, "used": wk_prev, "live_end": dstr,
  909:                       "prev_live_end": (dt_cur - ONE_DAY).isoformat(),
  910:                       "day_keys": guba_day_keys})
with loop.py:153-155 `def _week_key(d): y,w,_ = d.isocalendar(); return f"{y}-W{w:02d}"`.

Consumer (flowmirror\engine\world.py):
  750:  for e in audit[1:]:
  751:      if e.get("used") != e.get("prev_live_end"):
  752:          a_ok = False
  753:  for e in audit:
  754:      if e.get("live_end") == e.get("used") and int(e.get("day_keys", 0)) > 0:

`used` is '2025-W41'; `prev_live_end`/`live_end` are '2025-10-08'/'2025-10-09'. Reproduced by calling check_invariants with rows built exactly as loop.py builds them: a -> {'pass': False, 'days_audited': 3}. The second condition is indeed dead; note it also short-circuits away a latent TypeError, since `guba_day_keys` is a list (loop.py:775 `sorted({...})`) and `int(list)` would raise. On-disk confirmation matches: runs/out/mock_3arm, demo_three-arm, mock_40x5 all show `a_lagged_signals_only pass:false` with `all_passed:false`.

FAILURE SCENARIO: REFUTED. The claim that "every run ... exits 4" is wrong. I ran mock_10x3 at 40 agents x 5 days end to end:
  invariants=FAIL(a_lagged_signals_only)
  invariant FAIL a_lagged_signals_only: {"days_audited": 5, ... "pass": false}
  EXIT CODE = 0
Reason: world.py:840-842 sets `core = [k for k, v in checks.items() if v.get("pass") is False ...]` then `return checks, bool(core)` — so `core` is True when failures EXIST — while loop.py:839 does `ok = bool(core) and all(bool(v.get("pass", True)) for v in extras.values())` and `if ok: return 0`. The polarity is inverted, so the always-failing (a) makes `core` True, `ok` True, and the run exits 0. (test_invariant_wiring.py assumes the opposite convention — its fake returns `checks, False` for a failure — and test_mock_run_reports_real_invariant_detail only passes its `assert run_simulation(cfg) == 0` because of this inversion.) So the engine does not gate on the broken check; it prints "status=invariant_failure" and returns success.

SUB-CLAIM "would not test what invariant (a) claims": mostly right but overstated. The audit row carries only the guba week key plus two dates, so a corr


---

## 15. HIGH  -  Per-impression delivery notes (image_missing, image_unsupported, tc_no_caption) are computed and then discarded

**Where.** `flowmirror/engine/loop.py` 996-1000 (dec row) and 419-421 (only surfacing site)

**What.**

build_decision_messages returns prompt_notes carrying 'image_missing'/'image_unsupported' per undelivered TV card and 'tc_no_caption' per degraded TC card (prompt.py:344-352, 397-414); runtime.decide stores them at record['notes']['prompt_notes'] (runtime.py:432, 459). The loop's dec row logs only prompt_sha, raw_sha, status, arm, mood, reason and violations (loop.py:996-1000) — prompt_notes are never read. The single place they reach disk is the --dump-prompt sidecar for ONE agent-day (loop.py:421). They are not counted into S, not in run_meta counters (world.write_reports, world.py:893-920), not on the imp row, and no invariant references them.

**How it fails.**

GAP_ARM_DELIVERY names runs/*/run_meta.json and the impression events as its evidence sources and asks for 'the per-condition share of impressions delivered as specified, with the tc_no_caption, image_missing and image_unsupported counts'. Open any completed run: run_meta.json has no such field and the event log has no such field, so the slot cannot be filled from an executed run. In the current build every single TV impression emits 'image_missing' (see the TV-arm finding) and the run still reports status ok with 11 invariants and no delivery warning.

**Paper claim at risk.** appendix.tex:70 and GAP_ARM_DELIVERY (§3.3, App. C).

**Fix.**

Accumulate prompt_notes into the run counters (e.g. S['image_missing'], S['image_unsupported'], S['tc_no_caption'], each with the arm) and emit the per-card note on the imp row, so run_meta.counters carries a per-condition delivered-as-specified share; make a TV run with zero attached images fail an invariant rather than pass silently.

**Verification (confirmed).**

I tried to refute this three ways (a second surfacing site, persistence via the LLM cache, an aggregation script) and all three failed. The defect is real as stated.

1) Notes are produced per card. D:\Desktop\ABM paper\flowmirror_v7\flowmirror\agents\prompt.py (the claim's "prompt.py" is agents/, not engine/ — line numbers are within ~2 of those quoted):
  line 400: `notes.append("tc_no_caption")`
  line 416: `notes.append("image_unsupported" if exists else "image_missing")`
  docstring 349-350: `notes records image degradations ("image_missing"/"image_unsupported"), the TC-arm degradation "tc_no_caption"`

2) They are stored on the record and go nowhere. D:\Desktop\ABM paper\flowmirror_v7\flowmirror\agents\runtime.py:
  line 419: `messages, prompt_sha, img_shas, prompt_notes = build_decision_messages(...)`
  line 432: `notes = {"mode": "decision", "model": model, "retried": False, "prompt_notes": list(prompt_notes or [])}`
  line 460 (`cache.put`): `cache.put(key, {"key": key, "provenance": prov, "parsed": parsed, "raw": prov.get("raw"), "ts": ...})` — `notes` is NOT among the persisted keys, so the cache does not carry them either. I confirmed this against a real cache file: `runs/out/try_images/llm_cache.jsonl` rows have exactly `['key','parsed','provenance','raw','ts']`.
  line 413 (cache-hit path): `"notes": {"replay": True}` — on a warm replay the prompt_notes are not even recomputed, so any cached/replayed run loses them entirely. This is stronger than the claim.

3) The dec row drops them. D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\loop.py:996-1000 logs `prompt_sha, raw_sha, status, arm, mood, reason, violations` plus `_dec_counts(...)`; `rec["notes"]` is never read. A repo-wide grep for `["notes"]` / `get("notes")` inside flowmirror/ returns exactly one unrelated hit (world.py:215, a JSON-shape fallback). The only disk surfacing is loop.py:421 inside `_write_prompt_dump`, which is guarded by `if dump_target is not None and not dump_done` (loop.py:401-405) — one agent-day per run.

4) Verified against executed runs, not just source. Field inventory of `runs/out/try_images/event_log.jsonl`: `imp` rows carry only `['arm','d','ev','i','p','slot','source','t']`; `dec` rows carry only `['aff_sum','arm','d','ev','i','mood','n_comment','n_follow','n_like','n_read','n_save','prompt_sha','raw_sha','reason','status','t','violations']`. `run_meta.json` top-level keys are `['arms','cfg','checkout_oc','checkout_oc_cf','counters','elapsed_s','engine',


---

## 16. HIGH  -  The engine's invariant registry does not match the twelve invariants the paper's appendix prints

**Where.** `flowmirror/engine/world.py` 666-678, 736, 795-810

**What.**

appendix.tex:145 lists twelve invariants (a)-(l) 'checked at the end of every run'. world.INVARIANTS (world.py:666-678) holds eleven keys and three of them do not mean what the paper says: (f) the paper says 'every post's primary intent group is one of the three logged values, brand, product push or investor education', but the code checks membership in the TWO-way group `_IG_GROUPS = ("I2", "nonI2")` (world.py:483, 736) and never validates the raw three-way `intent` label — a post row with intent='I9' passes, since ig becomes 'nonI2' and (ig=='I2')==(intent=='I2') holds. (h) the paper says (h) is 'cohort composition matches the analysis plan ... each persona cell deviating from its target share by at most 1/(2 ncells)', but the code's h_arm_balance checks MODALITY ARM balance and no cohort-composition check exists anywhere in check_invariants. (l) 'a full-cache replay reproduces the event log byte for byte' has no registry entry at all; it is a main()-level --replay-check, not an end-of-run invariant, which is why the shipped reports show total:11. Additionally, 3_environment.tex:96 claims the engine checks 'that the E2 policy assignment is balanced across runs' and 'that every E1 decision state is evaluated under all conditions'; at run level h_arm_balance is explicitly SKIPPED (world.py:808-810), check_invariants is per-run so it can see no cross-run balance, and no E1 pairing check exists.

**How it fails.**

A reviewer reads appendix.tex:145, opens runs/*/invariants_report.json for an accepted run under GAP_PLATFORM_AUDIT, and finds eleven entries whose letters carry different meanings: (f) validates a two-way tag against a three-way claim, (h) reports arm balance where cohort composition was promised (and is 'skipped' for exactly the E2 run-level configuration whose balance §3 says is checked), and (l) is missing. Any statement that 'all twelve invariants passed' is unsupportable from the artefact.

**Paper claim at risk.** appendix.tex:145 invariants (f), (h), (l); 3_environment.tex:96; GAP_PLATFORM_AUDIT.

**Fix.**

Reconcile the registry with the appendix: make (f) assert intent in {I1,I2,I3} as well as the derived group; add the cohort-composition check the paper attributes to (h) and give arm balance its own letter; register (l) explicitly (even as a skipped entry pointing at --replay-check) so the report enumerates twelve; and either implement the cross-run E2 balance and E1 pairing checks or delete those two clauses from 3_environment.tex:96.

**Verification (confirmed).**

All four sub-claims reproduce from the code; I could not refute any of them.

1) COUNT. `world.INVARIANTS` (D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\world.py:666-678) holds exactly eleven keys, a_ through k_. Executed: `len(world.INVARIANTS) == 11`, keys a..k, no l. The repo's own test agrees with me and not with the paper — tests/unit/test_invariant_wiring.py:36: "# The 11 registered invariants (flowmirror.engine.world.INVARIANTS)". Eight of the shipped runs/out/*/invariants_report.json summaries read `{"total": 11, ...}`; the five reading `total: 12` get there only because a stray `core` key rides along in `checks` (keys are a..k plus literal "core"), not because (l) exists. appendix.tex:145: "Twelve invariants are checked at the end of every run", listing (a)-(l).

2) (f) IS A TWO-WAY CHECK BEHIND A THREE-WAY CLAIM. world.py:483 `_IG_GROUPS = ("I2", "nonI2")`; world.py:735-737: `elif ev == "post":` / `if r.get("ig") not in _IG_GROUPS or (r.get("ig") == "I2") != (r.get("intent") == "I2"): f_viol += 1`. The raw label is never tested against `_INTENTS`. Running check_invariants over one fabricated row `{'ev':'post','ig':'nonI2','intent':'I9'}` returned `f = {'pass': True, 'violations': 0, 'ig_domain': ['I2','nonI2']}`. The paper's (f) is "every post's primary intent group is one of the three logged values, brand, product push or investor education". The I1/I2/I3 enum does exist at config/schemas/event.schema.json:60-68, but nothing in the run path validates the event log against that schema — the only reference, flowmirror/cli.py:266, is a print statement naming the filename.

3) (h) IS ARM BALANCE, NOT COHORT COMPOSITION, AND IS SKIPPED AT E2's LEVEL. world.py:674 describes (h) as "modality arms are balanced: overall share within tolerance of 1/k..."; feed.py:426 docstring: "Verify arm invariant (h) for stratified block randomisation" over `agent_arms`. My run returned `h = {'pass': False, 'level': 'agent', 'arms': ['T','TV'], 'report': {... 'n_tv': 0, 'tv_share': 0.0, 'n_t': 0, 't_share': 0.0}}` — arm shares, not persona-cell shares. No cohort-composition check exists in check_invariants or anywhere reachable from it (grep for target share / per-cell deviation / 1/(2·ncells) hits only feed.py arm code and sampler.py sampling-time code). It is the retired constant by the paper's own admission — 4_interventions.tex:51: "the arm-share and per-arm cohort constants no longer apply, and cohort balance is stated instead as a maximum admissible per-c


---

## 17. HIGH  -  The m_tv_arm_carries_images invariant documented in three places does not exist in the registry

**Where.** `flowmirror/engine/world.py` 666-678

**What.**

`INVARIANTS` is the complete registry (`flowmirror/engine/world.py:666-678`) and contains exactly eleven keys: `a_lagged_signals_only`, `b_nonholder_never_redeems`, `c_hard_block_never_subscribes`, `d_wealth_conservation`, `e_all_cells_exposed`, `f_post_ig_is_intent_group`, `g_comments_lagged_only`, `h_arm_balance`, `i_redeem_checkout_never_blocked`, `j_displayed_comment_matches_prev_day`, `k_dec_matches_active`. `m_tv_arm_carries_images` appears nowhere in any `.py` file, yet it is described as an operating check in `config/engine_defaults.yaml:40`, `config/schemas/run.schema.json:257`, and `docs/RUNBOOK.md:115`, `:127`, `:152`, `:224` — the last of which even gives a troubleshooting entry for its failure mode: "invariant `m_tv_arm_carries_images` FAILED | TV ran with `images_root` configured but zero attachments". Two companion signals documented alongside it are equally absent: the run-start line `[world] WARNING: no images_root configured -- TV arm degrades to text-only` (the only WARNING in the engine is `flowmirror/engine/world.py:326`, about synthetic NAVs), and the `images: {root, attached, missing, sha_mismatch, policy}` block in `run_meta.json` (`runs/out/try_images/run_meta.json` has keys engine/synthetic_nav/cfg/inputs_sha256/universe/funds/investors/arms/modality/fees/counters/checkout_oc/checkout_oc_cf/elapsed_s/status — no `images`).

**How it fails.**

An operator follows docs/RUNBOOK.md §4, sets `images_root`, runs the three-arm demo, sees `invariants: 10 pass, 1 fail` where the one failure is the unrelated `a_lagged_signals_only`, and concludes from the absence of an `m_tv_arm_carries_images` failure that the TV arm carried images. It carried none. The invariant that the RUNBOOK says "exists to catch" exactly this silent no-op is the one thing that would have caught it, and it was never written.

**Paper claim at risk.** fundmarket-sim/paper/v7/sections/5_validation.tex GAP_PLATFORM_AUDIT and 3_environment.tex:60/65 (GAP_ARM_DELIVERY: "per-condition share of cards delivered as specified, with the tc_no_caption, image_missing and image_unsupported counts"). The engine emits no per-condition delivery counters at all, so that slot has no source today.

**Fix.**

Either register and implement `m_tv_arm_carries_images` in `flowmirror/engine/world.py` INVARIANTS plus `check_invariants` (fail when the run has TV impressions, `images_root` is non-null, and zero impressions carried an image), or delete the four RUNBOOK references, the engine_defaults comment and the run.schema description that assert it already runs.

**Verification (confirmed).**

Tried hard to refute; every element of the claim holds, and the repo itself admits it.

1) Registry is complete and has exactly 11 keys, none `m_*`. flowmirror/engine/world.py:662-679 comment: "Registry of every invariant this engine defines" ... "write_reports() writes ONE invariants_report.json entry per key in this registry (union with anything check_invariants returned), so a check that did not run shows up as `skipped`". Keys are a_..k_ only. `grep -rn "m_tv_arm_carries_images" .` returns six hits, none in a .py file: config/engine_defaults.yaml:40, config/schemas/run.schema.json:257, docs/RUNBOOK.md:115,127,152,224.

2) The entire image pipeline is unimplemented in the engine, not merely the invariant. `grep -rn "images_root|image_pick|img_idx" --include=*.py flowmirror/ tests/` returns ZERO hits. The only place image_path is set is flowmirror/engine/loop.py:248-250:
    image_path = None
    if post.get("img"):
        image_path = note.get("image_path") or note.get("image") or note.get("cover")
and the pool "deliberately ships no image paths" (schema:257), so image_path is always None and flowmirror/agents/prompt.py:403-416 never attaches pixels on TV. images_root is read only by data_pipeline/cn/caption_frozen.py, an offline captioner outside the engine.

3) The repo's own handover doc confirms the gap. docs/DEV_HANDOVER.md:81 lists card IMG-A as 生成中 ("TV 臂真正附图... imp.img_idx 记录、run_meta.images 统计"); :179 states outright "**引擎侧解析仍未落地**（IMG-A 卡待重跑）" — engine-side resolution has not landed; only the config surface (IMG-B) shipped. RUNBOOK section 4 carries no such caveat and describes the mechanics in the present tense.

4) The failure scenario already exists as a completed run in the repo. runs/out/try_images/run_meta.json cfg carries images_root: "D:/Desktop/ABM paper/fundmarket-sim/sim/content_pool_v1/images", image_pick: "first", arms [T,TC,TV]. Its invariants_report.json summary is verbatim {"total": 11, "passed": 10, "failed": 1, "skipped": 0, "all_passed": false} with the single failure being a_lagged_signals_only — exactly the "10 pass, 1 fail, unrelated" reading the claim predicts. The event log has 432 imp rows; a TV imp row's keys are ['arm','d','ev','i','p','slot','source','t'] — no img_idx key at all, `grep -c img_idx` = 0, zero image_missing notes. run_meta.json keys are engine/synthetic_nav/cfg/inputs_sha256/universe/funds/investors/arms/modality/fees/counters/checkout_oc/checkout_oc_cf/elapsed_s/status — no images block.

5) The comp


---

## 18. HIGH  -  imp.img_idx is declared in the event schema and never emitted; image_pick=random is a byte-identical no-op

**Where.** `flowmirror/engine/loop.py` 948

**What.**

The `imp` row is written as `logd("imp", t=t, d=dstr, i=inv.id, p=pid, arm=arm, slot=s, source=source)` — no `img_idx`. `config/schemas/event.schema.json:132` declares `img_idx` as an optional integer on `imp` rows, and `config/schemas/run.schema.json:265-266` plus `config/engine_defaults.yaml:46-53` both state that "the chosen index is logged as img_idx on the imp row". Because `img_idx` is declared optional (`"type": ["integer", "null"]`), a log in which it never appears validates cleanly, so schema validation cannot detect the omission.

I verified the no-op end to end: two runs of `runs/demo_three_arm_images.json` (12 agents x 3 days, mock), identical except `image_pick: "first"` vs `"random"`, produced event logs with the same sha256 `03a859bd998618c909807003173625ca3e29d4a1ff2136abc56911bba9531eea`. Both exited 0. `runs/out/try_images/event_log.jsonl` likewise has 432 `imp` rows and 0 carrying `img_idx`.

**How it fails.**

Someone sets `image_pick: "random"` to execute E3, runs the sandbox, sees a normal successful run with a valid config and a schema-valid event log, and writes an analysis that groups impressions by `imp.img_idx`. Every row is missing the key, so the grouping collapses to a single bucket — or, worse, the analysis silently treats absent-as-null and reports a within-note re-pairing contrast computed over impressions that all showed the same (in fact, no) image. That number would fill GAP_E3_IMAGE and GAP_E3_VARSHARE with an artefact.

**Paper claim at risk.** fundmarket-sim/GAP_REPORT.md:128-130 (GAP_E3_IMAGE / GAP_E3_VARSHARE / GAP_E3_ATTR, source `runs/e3_*/repair_summary.json`) and 4_interventions.tex:63 ("the chosen index and the image hash are logged on the impression event, so a replay reproduces the assignment"). The paper's guard sentences (4_interventions.tex:65, 6_experiments.tex:99, 103) are correct that the change has not landed — the risk is that the schema and defaults advertise the key as live, so a later run appears to satisfy the guard without doing anything.

**Fix.**

Make the dead key fail loudly rather than silently: have the config layer reject `image_pick: "random"` (and warn on a non-null `images_root`) with an explicit "not implemented in this engine build" error until `_feed_card` actually selects an index and `logd("imp", ...)` carries `img_idx`.

**Verification (confirmed).**

CONFIRMED on all three points, and the underlying scope is broader than claimed.

(1) img_idx never emitted. flowmirror/engine/loop.py:948 is the ONLY logd("imp", ...) site in the engine:
    logd("imp", t=t, d=dstr, i=inv.id, p=pid, arm=arm, slot=s, source=source)
`grep -rn 'logd("imp"' flowmirror/` returns exactly this one line. No img_idx anywhere.

(2) The entire image config surface is unwired, not just the log key. `grep -rn "image_pick" --include=*.py .` returns NOTHING repo-wide (exit 1). `grep -rn "images_root" flowmirror/` returns NOTHING (exit 1). Both keys live only in config/, docs/, and run JSON. The only image code in the engine is _feed_card (loop.py:248-250):
    image_path = None
    if post.get("img"):
        image_path = note.get("image_path") or note.get("image") or note.get("cover")
The pool it reads (data/creatives/cn/content_pool_v1_masked.jsonl) carries only image_ids / image_sha256 / n_images -- data_pipeline/cn/import_from_research.py:191 converts image_paths_resized -> image_ids and drops the path keys. So image_path is always None and the TV arm never attaches a pixel, with or without images_root. TV is byte-identical to T by construction.

(3) Reproduced the byte-identical no-op with the reporter's exact digest. Two runs of runs/demo_three_arm_images.json (--mock --days 3 --agents 12) differing only in image_pick "first" vs "random" both exited 0 with event_log.jsonl sha256 03a859bd998618c909807003173625ca3e29d4a1ff2136abc56911bba9531eea. runs/out/try_images/event_log.jsonl: 432 imp rows (TV 144 / T 144 / TC 144), 0 carrying img_idx.

(4) The promised safety nets do not exist. docs/RUNBOOK.md:150-152 asserts a successful run "prints `[world] images: <n> resolvable under <images_root>`, reports a non-zero `images.attached` in `run_meta.json`, passes the `m_tv_arm_carries_images` invariant, and shows `img_idx` on TV impressions." In fact: `m_tv_arm_carries_images` appears in ZERO Python files (only config/engine_defaults.yaml:40, config/schemas/run.schema.json:257, docs/RUNBOOK.md) and is absent from the ~40 chk(...) invariants at loop.py:1188-1438; runs/out/try_images/run_meta.json has no "images" key; my runs printed no "[world] images:" line and no missing-images_root WARNING. Schema validation is likewise blind: event.schema.json:132-139 declares "img_idx" with "type": ["integer","null"] as an optional property, so an all-absent log validates cleanly.

Only mitigating fact, which does not refute: docs/DEV_HANDOVER.md:81,179


---

## 19. HIGH  -  Invariant pass/fail polarity is inverted: a run with failing invariants exits 0, a clean run exits 4

**Where.** `flowmirror/engine/world.py` 840-842

**What.**

`check_invariants` ends with:

```python
core = [k for k, v in checks.items() if v.get("pass") is False
        and not (k == "e_all_cells_exposed" and not big)]
return checks, bool(core)
```

`core` is the list of FAILING keys, so `bool(core)` is True precisely when the run has failures. The caller in `flowmirror/engine/loop.py:839` reads it as the success flag: `ok = bool(core) and all(bool(v.get("pass", True)) for v in extras.values())`, then `if ok: return 0` and otherwise prints `engine exit code 4: invariant failure`. The polarity is reversed at one of the two ends.

Verified empirically: `python -m flowmirror.engine.loop runs/mock_10x3.json --mock --days 3 --agents 40 --out <tmp>` prints `status=invariant_failure (invariants: 10 pass, 1 fail, 0 skipped, 11 total)` and `invariant FAIL a_lagged_signals_only`, writes `"all_passed": false` into `invariants_report.json` and `"status": "invariant_failure"` into `run_meta.json` — and returns process exit code 0, with the "engine exit code 4" line never printed. Symmetrically, a run in which all eleven invariants pass would take the else branch and exit 4 with an empty failure list.

**How it fails.**

Any CI gate, batch driver or `flowmirror demo` wrapper that keys off the process exit code accepts every invariant-violating run and rejects every clean one. Concretely: the eight committed runs under `runs/out/` show `status=invariant_failure`, all of which exited 0. This also means the fix that commit b6f93e9 and docs/DEV_HANDOVER.md §10 claim landed ("失败时以独立退出码 4 终止") did not land, and it neutralises the enforcement the image documentation depends on — an `m_tv_arm_carries_images` failure would make the run exit 0 rather than 4.

**Paper claim at risk.** fundmarket-sim/paper/v7/sections/8_limits.tex:21 and 5_validation.tex GAP_PLATFORM_AUDIT, which report the platform invariant verdicts as the evidence that released runs are sound. Any statement of the form "all released runs passed the invariant suite" that was established by exit code rather than by reading invariants_report.json is unsupported.

**Fix.**

Return `not core` from `flowmirror/engine/world.py:842` (or change `flowmirror/engine/loop.py:839` to `ok = (not core) and all(...)`) — one end only — and add a regression test that asserts exit code 4 on a run with a deliberately broken invariant and exit code 0 on a clean run.

**Verification (confirmed).**

CONFIRMED as stated — both polarity ends reproduced empirically.

1) The producer treats the second return value as a FAILURE flag. D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\world.py:840-842:

    big = len(agents) >= 300                      # (e) fatal only at n_agents >= 300
    core = [k for k, v in checks.items() if v.get("pass") is False
            and not (k == "e_all_cells_exposed" and not big)]
    return checks, bool(core)

`core` is a comprehension over `v.get("pass") is False`, i.e. the list of FAILING keys. world.py's own embedded self-test pins this polarity (world.py:1115-1120):

    cc, cf = check_invariants(st, cp, {"arm_level": "exposure"})
    bc, bf = check_invariants(st, bp, {"arm_level": "exposure"})
    chk("invariants_clean_passes",
        cf is False and [k for k, v in cc.items() if not v.get("pass", True)] == ["e_all_cells_exposed"])
    chk("invariants_flag_redeem_block", bf is True and bc["i_redeem_checkout_never_blocked"]["pass"] is False)

clean -> False, dirty -> True.

2) The consumer reads the identical value as a SUCCESS flag. loop.py:839-861:

    ok = bool(core) and all(bool(v.get("pass", True)) for v in extras.values())
    ...
    if ok:
        return 0
    print("engine exit code 4: invariant failure" ...)
    return 4

3) Empirical, this checkout (HEAD = b6f93e9):
- Failing run: `python -m flowmirror.engine.loop runs/mock_10x3.json --mock --days 3 --agents 40 --out <tmp>` printed `status=invariant_failure (invariants: 10 pass, 1 fail, ...)`, `invariants=FAIL(a_lagged_signals_only)`, wrote `"all_passed": false` / `"status": "invariant_failure"`, and the shell reported EXITCODE=0. The "engine exit code 4" line never printed.
- Clean run: I monkeypatched `loop.check_invariants` to return an all-pass `checks` with `core` computed by world.py's own formula (`bool([])` -> False). Result: `invariants=FAIL()`, `invariant FAIL core: {"core": false, "note": "core=False but no individual check entry is failing"}`, `engine exit code 4: invariant failures: ` (empty key list), rc = 4 — while run_meta.json on disk said `"status": "ok"`. The symmetric half of the claim holds.

4) Why the suite does not catch it. tests/unit/test_invariant_wiring.py:149-161 hardcodes the consumer's polarity rather than the producer's — `return checks, False` alongside a failing check, asserting exit 4 — so it never exercises world.py's real formula. And test (a) at line 114 (`assert run_simulation(cfg) == 0`) currently passes only *becau


---

## 20. HIGH  -  I2 detection never matches engine logs, so click_rate and subscribe_conversion are structurally dead on every real run

**Where.** `flowmirror/analysis/modality.py` 99-104, 214-218, 264-267, 296-298

**What.**

`_is_i2(pid, source)` (line 99-104) recognises I2 content only when the imp row's `source` field equals the literal string "I2", or when the post id starts with "I2"/"i2". Neither is ever true for engine output. The engine writes `source` from `_rank_item` (flowmirror/engine/loop.py:223-226, 943-948), whose values are the ranking-channel labels "follow"/"fit"/"trending"/"spill". Post ids are built as `pid = f"{t:03d}{oi}{j}"` (flowmirror/engine/world.py:578), i.e. purely numeric strings such as "00311". The I2 membership actually lives in a different field entirely, `intent_group` on the post object, emitted as `ig` on the `post` event (world.py:580-584) — a field the analysis layer never reads. Consequently `i2_posts` (line 216) is always empty, `i2_shown` and `i2_clicks` (lines 226-233) stay 0 for every agent, and the guards at lines 264-267 (`_agent_metrics`) and 296-298 (`_pooled_metrics`) never fire. Two of the four rate metrics named in the module docstring are never computed. Worse, the failure is disguised: the empty metric flows into `_cross_agent`, `vals` is empty, and the emitted warning reads "n_runs=0: descriptive only" — indistinguishable from an ordinary shortage of seeds rather than a metric that can never be computed. `docs/DECISIONS_2026-09-05_SANDBOX.md:15` names 点击率 (click rate) and 申购转化 (subscribe conversion) as the secondary endpoints sourced from `analysis/modality.py`, and paper/v7/sections/8_limits.tex:24 lists "click rate, purchase conversion" among the endpoints declared defined and transferable across regimes.

**How it fails.**

Ran `python -m flowmirror.analysis.modality runs/out/demo_three-arm` on the repo's own committed run. The log contains 20 posts with ig="I2", 19 click rows and 14 subscribe act rows. Every one of the nine `click_rate` and `subscribe_conversion` cells prints `--` for diff, CI, effect and effect CI, with verdict `insufficient_runs` and warning "click_rate n_runs=0: descriptive only", while engagement_rate and comment_rate print real numbers off the same log. With the planned 5 seeds the engagement rows would carry a real t-interval and the two I2 endpoints would still say "n_runs=0: descriptive only", so a reader fills the slot as under-powered when in fact the numerator and denominator were never counted.

**Paper claim at risk.** GAP_E2_POLICY / §6.2 secondary endpoints; paper/v7/sections/8_limits.tex:24 ("click rate, purchase conversion ... are defined in both regimes"); docs/DECISIONS_2026-09-05_SANDBOX.md:15 (次: 点击率、申购转化)

**Fix.**

Read the I2 flag from where the engine writes it: build `i2_posts` from the `post` events' `ig == "I2"` field (or add `ig` to the imp/click rows at loop.py:948 and to the click emit) rather than from `source`/post-id prefixes, and add a hard assertion that `i2_posts` is non-empty whenever `post` rows exist, so the metric fails loudly instead of degrading into a seed-count warning.

**Verification (confirmed).**

I attempted to refute and failed on every axis.

1. The predicate is exactly as described. flowmirror/analysis/modality.py:99-104:
```python
def _is_i2(pid, source=None):
    """I2 content: source == 'I2' or a post id carrying the I2 prefix."""
    if source == "I2":
        return True
    s = str(pid)
    return s.startswith("I2") or s.startswith("i2")
```
Those are the only two ways in.

2. Neither branch can fire on engine output. flowmirror/engine/world.py:578-584 builds the id numerically and puts I2 membership in a different field:
```python
pid = f"{t:03d}{oi}{j}"
ig = intent_group(intent)   # post.ig is the intent GROUP in {I2, nonI2}, not the raw label
...
log.emit("post", t=t, d=dstr, org=org, p=pid, intent=intent, ig=ig, fund=code, ...)
```
and imp.source is the ranking-channel label from _rank_item (engine/loop.py:223-226, emitted loop.py:947). channels/feed.py:585 documents that domain as "follow", "fit", "trending", "spill" — "I2" is not in it.

3. The analysis layer never reads the field that does carry it: grep -rn '"ig"|get("ig")|intent_group' flowmirror/analysis/ returns ZERO hits.

4. Reproduced on the committed run runs/out/demo_three-arm/event_log.jsonl:
- imp sources {'fit': 852, 'trending': 600, 'follow': 348} — no "I2"
- 40 posts, 20 with ig=="I2"; sample pids ['00000','00001','00010','00011','00020']
- click rows carry no source key at all: {'ev':'click','t':0,...,'p':'00031','oc':'to_checkout'}
So i2_posts (line 216) is empty, i2_shown/i2_clicks stay 0, and the guards at 264-267 and 296-298 never fire.

5. `python -m flowmirror.analysis.modality runs/out/demo_three-arm` prints exactly the reported failure: all nine click_rate/subscribe_conversion cells show `-- [--] -- [--] 0 insufficient_runs` with `warning: agent-level: TV-TC click_rate n_runs=0: descriptive only`, while engagement_rate and comment_rate print real numbers (-0.008, -0.003) off the same log with n_runs=1.

6. Why no test catches it — the fixture uses a schema the engine never emits. modality.py:661-664:
```python
rows.append({"ev": "imp", ..., "p": "I2-%03d" % j, "arm": arm,
             "slot": "feed", "source": "I2"})
```
Both branches of _is_i2 are satisfied by construction, so tests/unit/test_analysis_modality.py:106 (pooled["click_rate"] == 0.2) passes green against a shape no real run produces.

7. The endpoints really are declared: docs/DECISIONS_2026-09-05_SANDBOX.md:15 lists them as the secondary endpoints sourced from analysis/modality.py — "次：点击率、申购转化"


---

## 21. HIGH  -  The bounded_null branch has no lower bound, so a large effect in the opposite direction is reported as practical equivalence

**Where.** `flowmirror/analysis/modality.py` 107-114 (specifically 112)

**What.**

`_verdict` tests `if hi < sesoi: return "bounded_null"` at line 112. SESOI is a magnitude (h = 0.10, d = 0.20, line 58) but the test is applied only to the upper end of a two-sided interval. Equivalence requires the whole interval inside the margin, i.e. `-sesoi < lo and hi < sesoi`. As written, any interval lying wholly below +0.10 is declared bounded_null, however far below zero it sits. This interacts with the fixed contrast direction: `_arm_pairs` (lines 193-199) always orders pairs as (hi, lo) in canonical T < TC < TV, so the contrast is always TV-T, TV-TC, TC-T. An effect where the text arm beats the visual arm therefore produces a negative interval and can only ever land in bounded_null — the code has no branch that can report a supported negative direction. The docstring at lines 15-17 and the printed rule at line 613 both present this as the three-way verdict against the SESOI.

**How it fails.**

`_verdict(-0.85, -0.55, SESOI["h"])` returns "bounded_null". Executed against the real machinery: two run-level seeds whose per-seed Cohen h are -0.55 and -0.60 give `seed_t_interval` mean -0.575, df 1, CI [-0.893, -0.257] — an effect roughly six times the SESOI, consistently signed across both seeds, entirely excluding zero — and `_verdict` labels it "bounded_null". The report line then reads TV-T ... bounded_null, and the printed rule at line 613 tells the reader that means the effect is bounded below the smallest effect of interest. The paper would state "no difference beyond the stated bound" for the largest effect in the study, with its sign inverted.

**Paper claim at risk.** GAP_E1_PRIMARY decision rule in fundmarket-sim/GAP_REPORT.md:91 ("A reversed sign is reported as contradicting the expected direction, with no substitute narrative"); paper/v7/sections/1_intro.tex:32 ("if the paired runs disagree in sign ... no direction")

**Fix.**

Require both ends inside the margin: `if lo > -sesoi and hi < sesoi: return "bounded_null"`, and add an explicit negative-direction branch (`if hi < 0: return "supported_negative"` or equivalent) so a reversed sign is reported as a reversed sign rather than folded into equivalence.

**Verification (confirmed).**

CODE (flowmirror/analysis/modality.py:107-114): `def _verdict(lo, hi, sesoi): / if lo is None or hi is None or not _finite(lo) or not _finite(hi): return "indeterminate" / if lo > 0: return "supported" / if hi < sesoi: return "bounded_null" / return "indeterminate"`. SESOI is a magnitude (line 58: `SESOI = {"h": 0.10, "d": 0.20}`) but only the upper bound is tested; there is no `-sesoi < lo` conjunct, and the only "supported" branch is `lo > 0`.

DIRECTION IS FIXED (lines 193-199): `_arm_pairs` docstring "Ordered pairs (hi, lo) in canonical order T < TC < TV; hi - lo contrast" over `CANONICAL_ARM_ORDER = ("T", "TC", "TV")`. Executed: `_arm_pairs({'T','TC','TV'})` -> `[('TV','TC'), ('TV','T'), ('TC','T')]`. A text-beats-visual effect is therefore always negative and can only ever land in bounded_null.

DIRECT REPRO: `_verdict(-0.85, -0.55, SESOI['h'])` -> `bounded_null`. `seed_t_interval([-0.55,-0.60])` -> `(-0.575, 0.0354, -0.8926, -0.2574, 1)`, and `_verdict(-0.8926, -0.2574, 0.10)` -> `bounded_null`.

END-TO-END REPRO through the real `analyze()` using the repo's own fixture generator `_synthetic_run(rd, s, like_tv=0/1/0)`, level="agent", 3 seeds:
`TV-T     engagement_rate         -0.183 [-0.255,-0.112]         -0.777 [-1.424,-0.130]          2 bounded_null`
followed by line 613: `rule: eff CI lo > 0 -> supported; hi < SESOI -> bounded_null; else indeterminate`. A Cohen h of -0.777 with CI [-1.424,-0.130], ~8x the SESOI and wholly excluding zero, is labelled as bounded below the smallest effect of interest.

REFUTATION ATTEMPTS THAT FAILED:
(1) "It matches its spec." It does - docstring lines 16-17 and docs/PREREG_v1.5_DRAFT.md:32 ("三分判定沿用 v1.2 SectionE（区间下界 > 0 / 上界 < SESOI / 不定）") both state the same one-sided rule. But bounded_null is an affirmative equivalence claim and equivalence requires the whole interval inside +/-SESOI; the spec carries the same flaw, so this is a faithful implementation of a wrong rule, not a non-defect.
(2) "The prereg is directional (TV > TC > T), so negative just means not supported." Even under a one-sided alternative, a negative interval should fall to indeterminate, never to the equivalence label.
(3) "Tests cover it." They do not. tests/unit/test_analysis_modality.py and `_self_test` only assert "supported" on positive fixtures and "bounded_null" on zero-effect fixtures (line 708: `assert v["aff_sum"] == "bounded_null"`, where both arms have aff_sum=5.0). No test exercises a negative effect on any metric.
(4) "The reade


---

## 22. HIGH  -  Run-level pairing silently discards runs that share a (seed, arm) key, contradicting the function's own no-silent-drop guarantee

**Where.** `flowmirror/analysis/modality.py` 471-476 (specifically 475)

**What.**

`_cross_runlevel` builds its pairing table with `by[(str(rs.get("seed")), rs["arm"])] = rs.get("pooled") or {}` at line 475. The assignment is unconditional, so when two run directories carry the same seed and the same arm — a re-run, a resumed run, a config sweep whose extra factor is not part of the seed, or any run whose `run_meta` lacks a seed — the later one overwrites the earlier with no warning and no record. The docstring of this very function (lines 459-464) promises the opposite: "Seeds that have one arm of a pair but not the other, and runs with no detectable arm, are excluded and reported via warnings instead of being silently dropped." The unpaired-seed case is indeed warned about (lines 477-485), and the no-arm case is warned about (lines 467-469), but the duplicate-key case is neither detected nor warned. The `_run_seed` fallback chain (lines 167-171) makes this easy to trigger: with no `seed` and no `run_tag` in run_meta it returns None, and every run collapses onto the single key "None".

**How it fails.**

Fed `analyze` eight run directories at level="run": seeds 1, 2, 3 in both arms plus a replicate pair at seed 1 with a different planted rate. Result: `n_common_seeds` 3, `df` 2, `per_seed` {'1': 0.400, '2': 0.150, '3': 0.100}, mean 0.2167, and `res["warnings"]` is the empty list. The original seed-1 pair (true diff +0.100) vanished without trace and the point estimate moved from 0.1167 to 0.2167 — an 86% shift — while the reported df still asserts three independent seeds. Second scenario: stripping `seed` and `run_tag` from the six run_meta files collapses all six runs onto key "None", yielding n_common_seeds 1, df 0, per_seed {'None': 0.100}, and warnings that say only "n_runs=1: descriptive only" — five of the six runs are gone and nothing says so.

**Paper claim at risk.** GAP_E2_POLICY in fundmarket-sim/GAP_REPORT.md:97 ("Policy contrasts with the run as the unit, and the number of independent runs behind each interval") — the reported run count would be wrong

**Fix.**

Detect the collision before assigning: if `(seed, arm)` is already in `by`, append a warning naming both run directories and either refuse the run set or keep a deterministic choice that is stated in the warning. Separately, make `_run_seed` returning None a hard warning rather than a silent key, since "None" is a legal dict key that swallows every run.

**Verification (confirmed).**

Line 475 of D:\Desktop\ABM paper\flowmirror_v7\flowmirror\analysis\modality.py is exactly as claimed and has no duplicate guard:

472:    by = {}
473:    seed_arms = defaultdict(set)
474:    for rs in armed:
475:        by[(str(rs.get("seed")), rs["arm"])] = rs.get("pooled") or {}
476:        seed_arms[str(rs.get("seed"))].add(rs["arm"])

The only warning paths in _cross_runlevel are the no-arm case (467-469) and the unpaired-seed case (477-485). Nothing detects or reports a repeated (seed, arm) key. The docstring at 459-461 reads: "Seeds that have one arm of a pair but not the other, and runs with no detectable arm, are excluded and reported via warnings instead of being silently dropped."

_run_seed (167-171) ends in `return meta.get("run_tag")`, so a run_meta with neither `seed` (top level, cfg, or config) nor `run_tag` yields None and every such run keys to "None".

Reproduced both scenarios by driving the module's own _synthetic_run fixture through analyze(..., level="run"):
- Baseline six runs (seeds 1,2,3 x arms T,TV): mean=0.1167 df=2 n_common_seeds=3 per_seed={'1':0.1,'2':0.15,'3':0.1}, warnings=[].
- Adding a replicate T/TV pair at seed 1 with like_tv=12: mean=0.2167 df=2 n_common_seeds=3 per_seed={'1':0.4,'2':0.15,'3':0.1}, warnings=[] (empty), while res["runs"] still lists all 8 rows ['T1','TV1','T2','TV2','T3','TV3','dup_T1','dup_TV1']. Point estimate moved 86% with no warning and df still asserting 3 independent seeds.
- Stripping `seed` and `run_tag` from the six run_meta.json files: n_common_seeds=1, df=0, per_seed={'None': 0.0999999...}, and warnings contain only the five "run-level: TV-T <metric> n_runs=1: descriptive only" lines. Five of six runs vanish with no record.

Two findings that strengthen rather than weaken the claim:
1. analyze (539-547) deliberately de-duplicates run *names* with a `#N` suffix (`if name in seen: seen[name] += 1; name = "%s#%d" % (name, seen[name])`), so duplicate run directories are explicitly designed to survive as distinct rows in `runs` — they collapse only in `by`. The result object therefore self-contradicts: 8 run rows, n_common_seeds=3.
2. This is not hypothetical in this repo. All 18 real run directories under runs/out/ resolve to the same seed via _run_seed: cfg.seed = 2027 for every one. analyze() over eight of them at level="run" lists eight run rows, every one seed=2027 arm=TV, all keyed ("2027","TV") — seven silently discarded from `by`.

Reachable from the user-facing CLI: --level run (788-799) 


---

## 23. MEDIUM  -  `--replay-check` prints the warm-run provider-call count but never asserts it is zero

**Where.** `flowmirror/engine/loop.py` 1507-1512

**What.**

The verdict is computed as `same = sha1 is not None and sha1 == sha2` and the command returns `0 if same else 3`. `warm_cache_calls` is read from `run_meta.json` at loop.py:1506 and only printed on the console line at loop.py:1509-1510; it never enters the pass/fail decision. The paper makes a two-part claim — byte-identical event log AND zero provider calls — and only the first part is mechanized. The gap is reachable because the cache key (runtime.py:216-218) is not the whole run configuration: it omits `max_tokens`, and it omits which policy produced the row, so `mock_llm`, `agent_policy: "null"` and a live provider all key under the same `cfg["llm"]["model"]` string. It also omits nothing that would protect a warm run whose image files moved: `image_shas` come from `sha256_file` (prompt.py:414), so any change to the bytes on disk is a fresh key and therefore a live call during what the operator believes is a replay.

**How it fails.**

Bump `llm.max_tokens_start` between the cold and warm run, or point `--out` at a directory whose `llm_cache.jsonl` was produced under a different `agent_policy`: the warm run silently makes provider calls. If those calls happen to reproduce parseable responses whose `raw_sha256` matches (or on a reflection whose parse fails both times, leaving `inv.reflection` and hence `summary_sha` at loop.py:1089-1090 unchanged), `sha1 == sha2` still holds and `--replay-check` returns 0 while `warm_cache_calls` is nonzero on a console line nobody parses. GAP_COST's "cache hit rate" and appendix.tex:150's "Replay makes no provider calls" would then be asserted from a check that did not test them.

**Paper claim at risk.** appendix.tex:150 ("Replay makes no provider calls") and 3_environment.tex:94 ("reproduces the event log byte for byte with zero provider calls").

**Fix.**

Include the warm-run call count in the verdict at loop.py:1507: `same = sha1 is not None and sha1 == sha2 and (m2.get("counters") or {}).get("calls") == 0`, and print which of the two conditions failed. Separately, fold the effective policy identity into `LLMCache.key_for` (runtime.py:216-218) — e.g. prefix the model string with `null:`/`mock:`/`live:` — so a cache written by one substrate can never be replayed as another.

**Verification (confirmed).**

CONFIRMED as stated, verbatim from the code.

1. The verdict ignores the counter. loop.py:1506-1512:
    m2 = _last_meta(cfg["out_dir"])
    same = sha1 is not None and sha1 == sha2
    print(f"replay-check sha1={sha1}")
    print(f"replay-check sha2={sha2} "
          f"warm_cache_calls={(m2.get('counters') or {}).get('calls')}")
    print(f"replay-check identical={bool(same)}")
    return 0 if same else 3
`m2` is loaded only to be interpolated into a print string; `same` is byte-identity alone. Exactly as claimed.

2. Cache key omits max_tokens and policy. runtime.py:215-218:
    @staticmethod
    def key_for(model, temp, prompt_sha, image_shas):
        return sha256_text(f"{model}|{temp}|{SCHEMA_VERSION}|{prompt_sha}|"
                           + ",".join(image_shas or []))
`max_tokens` comes from `_llm_cfg` (runtime.py:402, `int(llm.get("max_tokens_start") or 6144)`) and is passed to the provider call but never keyed. `_make_llm` (loop.py:310-313) swaps in NullPolicyLLM/MockLLM while decide()/reflect() still key on `cfg["llm"]["model"]`, so mock, agent_policy:"null" and live rows collide on one key. Both sub-claims hold.

3. Reflection bypass is real. loop.py:1087-1090:
    if rp.get("summary"):
        inv.reflection = str(rp["summary"])
    logd("refl", t=t, d=dstr, i=inv.id,
         summary_sha=sha256_text(str(inv.reflection or "")))
The refl event row carries no raw_sha and no prompt_sha, and `inv.reflection` is only reassigned when the parse yields a summary. A live reflection call during "replay" that fails to parse leaves summary_sha byte-identical.

4. The paper asserts the untested half. fundmarket-sim/paper/v7/sections/appendix.tex:150 ends "Replay makes no provider calls."; 8_limits.tex:21 says the cache "replays the event log with no provider calls; whether that held is invariant (l)". The named-invariant layer (world.py:756-837) runs a_ through k_ only — there is no l_ check. Invariant (l) IS this byte-identity comparison, so the paper attributes to the check a property the check does not mechanize.

Refutation attempts, all failed: no test under tests/unit/ exercises --replay-check (grep for replay/sha1/identical returns only unrelated dump-prompt and modality tests); no zero-calls assertion exists anywhere under flowmirror/ (the sibling data_pipeline/cn/caption_frozen.py:982 does assert "dry-run: must make zero calls and write nothing", so the convention exists in this codebase and was omitted here); the Governor resets per run so no 


---

## 24. MEDIUM  -  The engine-side extra checks are injected only when they FAIL, so a passing midday-mutation or cap-stop check is silently absent from the report

**Where.** `D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\loop.py` 1094

**What.**

loop.py:1094 builds the extras dict as `extra = {} if inv_a_ok else {"a_midday_signal_mutation": {"pass": False}}`, and the CapStop handler at loop.py:1097 similarly passes `{"cap_stop": {"pass": False}}` only on the failure path. _finish merges extras into checks (loop.py:834-836) and world._invariant_report unions the registry with whatever keys arrive (world.py:855). Neither key is in world.INVARIANTS (world.py:666-678), so on the normal path -- the heat map did not mutate mid-day, the budget cap was not hit -- neither key exists anywhere in invariants_report.json. There is no `skipped` entry and no `pass: true` entry: the check ran, passed, and left no trace. That is the same silently-absent failure mode the R2F card set out to eliminate for the registry keys, still present for the engine-side ones. (Because they are unregistered, when they do appear they also carry the placeholder description "unregistered invariant (add a description to world.INVARIANTS)" from world.py:857-858.)

**How it fails.**

A reviewer opens any accepted run's invariants_report.json to fill GAP_PLATFORM_AUDIT and finds 11 entries. There is no way to tell whether the mid-day signal-mutation check ran and passed, or was never wired in for that run -- the two states produce byte-identical reports. If a future edit accidentally leaves `inv_a_ok` permanently True, no report and no test would show the loss.

**Paper claim at risk.** paper/v7/sections/appendix.tex:147 -- "The per-invariant pass or skip record ... are \gap{GAP_PLATFORM_AUDIT}"; paper/v7/sections/5_validation.tex:18 -- "The audit reports per-invariant pass or skip". A check that ran and passed is neither.

**Fix.**

Register `a_midday_signal_mutation` and `cap_stop` in world.INVARIANTS (world.py:666) with descriptions, and have loop.py:1094 always emit them with their real outcome (`{"pass": inv_a_ok}`, `{"pass": True}` on the non-CapStop path) rather than only on failure.

**Verification (confirmed).**

I tried to refute this three ways (a redundant registry entry, a covering test, a `skipped` fallback that fires) and all three failed. The code says exactly what the claim says.

1) Failure-only injection — D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\loop.py:1094-1097:
```
        extra = {} if inv_a_ok else {"a_midday_signal_mutation": {"pass": False}}
        return _finish(extra, 0, n_days)
    except CapStop:
        return _finish({"cap_stop": {"pass": False}}, 2, max(t + 1, 0))
```
`inv_a_ok` is initialized True at loop.py:779 and only ever cleared at loop.py:1036-1037 (`if heat != heat_prev: inv_a_ok = False`). So on the pass path `extra` is literally `{}`. Same for the third engine-side key, `decision_failure_halt` (loop.py:1009-1013), which is likewise only ever emitted from its failure branch.

2) The merge is a plain dict.update of whatever arrived, so an empty `extras` contributes nothing — loop.py:829-831:
```
        extras = {str(k): (v if isinstance(v, dict) else {"pass": bool(v)})
                  for k, v in (extra_checks or {}).items()}
        checks.update(extras)
```

3) The report builder iterates `set(INVARIANTS) | set(checks)` (world.py:854), so a key absent from both the registry and `checks` produces no entry at all — there is no third branch that could emit `skipped`. The `src is None` / malformed branches at world.py:860-866 are reachable only for keys that ARE in `INVARIANTS`. And the registry (flowmirror/engine/world.py:666-678) holds exactly the 11 keys a…k; `a_midday_signal_mutation`, `cap_stop` and `decision_failure_halt` are not among them. Note `a_lagged_signals_only` is a *different*, event-log-derived check ("day t ranking input sha == end-of-(t-1) live sha"), not the in-process `heat != heat_prev` guard, so it does not stand in for it.

This directly contradicts the module's own stated contract, world.py:10-14: "write_reports() writes ONE invariants_report.json entry per registered key … or skipped+reason when the check does not apply, never silence."

4) No test covers it. `grep -rn "a_midday_signal_mutation\|cap_stop" --include=*.py .` returns only loop.py lines (1094, 1097) plus comments — zero test hits. tests/unit/test_invariant_wiring.py hardcodes `REGISTRY` as the same 11 keys and asserts only over those; its fake `check_invariants` never exercises `inv_a_ok=False`. The in-module self-test is actually the opposite of a guard here — world.py:1216-1217 asserts `set(ent) == set(INVARIANTS)`, i.e. it woul


---

## 25. MEDIUM  -  oc_cf is identical to oc in every suitability-on run, so the within-run gate counterfactual carries no information

**Where.** `flowmirror/engine/loop.py` 639-640

**What.**

apply_decision computes `oc_cf = cxr_outcome(inv.rc, fund.r, smc)` then `oc = oc_cf if cfg.get("suitability") else "match"` (loop.py:639-640). The counterfactual is therefore the GATE-ON verdict, which is informative only in a gate-OFF run. With suitability true — the setting of every shipped run config and, per 3_environment.tex:89, of every run in the paper's budget — oc_cf is a literal copy of oc, differing only where an engine-side override (purchase_blocked, below_min, no_holdings) later rewrites oc. Confirmed on disk: runs/out/mock_3arm/run_meta.json has checkout_oc == checkout_oc_cf == {'confirm_declined': 9, 'confirm_signed': 2, 'match': 17}; same for mock_40x5 and demo_null. flowmirror/regulator/none.py implements exactly the needed direction (NoGate.checkout -> 'match', NoGate.counterfactual -> the CXR verdict) but loop.py never imports it; it imports only cxr_outcome (loop.py:113).

**How it fails.**

§7 asks for GAP_RQ3_GATE: 'the direct settlement of an above-cap product-push purchase differed by X, per run' computed from 'the logged oc_cf counterfactual checkout outcome inside suitability-on runs' (7_application.tex:37-38). Compute that from any suitability-on event log: for every co row with act=subscribe, oc == oc_cf, so the difference is identically 0 for every run and every seed. Either the slot is filled with a mechanical zero that reads as 'the gate changed nothing', or it cannot be filled at all — and the paper's sentence that 'the direction of that difference is fixed by the program rule' is unsupported by any logged quantity.

**Paper claim at risk.** 3_environment.tex:89 ('the off state enters §7 as a within-run counterfactual, logged per checkout as oc_cf'); 7_application.tex:38 and GAP_RQ3_GATE.

**Fix.**

Log both directions on every checkout: keep the applied outcome in oc, and record the OTHER regime's verdict in oc_cf — i.e. in a suitability-on run oc_cf must be what NoGate.checkout would return ('match', modulo QDII), and in a suitability-off run what CNCXR.checkout would return. Route both through the regulator plugins (CNCXR / NoGate) rather than calling cxr_outcome inline, so the counterfactual can never collapse onto the observed outcome.

**Verification (partly).**

CORE CONFIRMED — `oc_cf` is the GATE-ON verdict in both arms, so it duplicates `oc` in every shipped run.

D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\loop.py:639-640
    oc_cf = cxr_outcome(inv.rc, fund.r, smc)
    oc = oc_cf if cfg.get("suitability") else "match"

The engine pins the redundancy itself, loop.py:1214:
    chk("gated_subscribe_oc_equals_cf", co["oc"] == co["oc_cf"] and co["oc"] in _VALID_OC)

Every shipped config is gate-on: config/engine_defaults.yaml:23 `suitability: true`; all of runs/*.json set `"suitability": true`; and `grep -o '"suitability": *[a-z]*' runs/out/*/run_meta.json | sort | uniq -c` returns `18 "suitability": true` — zero gate-off runs exist on disk. In all 18 run_meta.json, checkout_oc == checkout_oc_cf exactly (mock_3arm: both {'confirm_declined': 9, 'confirm_signed': 2, 'match': 17}; mock_40x5, mock_10x3, _polarity, _probe_verify2 all {'confirm_declined': 4, 'confirm_signed': 4, 'match': 12}; demo_null both {'confirm_declined': 2, 'confirm_signed': 3, 'match': 30}). loop.py:113 imports only `from flowmirror.regulator.cn_cxr import cxr_outcome`; `NoGate` appears nowhere outside tests/unit/test_cn_cxr.py:25.

So the paper's named vehicle does not carry the information it is asked to carry. 3_environment.tex:89 states "An off-plugin exists that lets every subscription execute while still logging what the rule would have decided; ... the off state enters Section~\ref{sec:application} as a within-run counterfactual, logged per checkout as \texttt{oc\_cf}." Against the engine that sentence is false for gate-on runs: what is logged is the ON verdict, and the off state is logged nowhere.

THREE OVERSTATEMENTS that keep this from "confirmed":

(1) "it cannot be filled at all" is wrong — GAP_RQ3_GATE is derivable, just not from oc_cf. NoGate.checkout is a constant, pinned exhaustively over the full C1..C5 x R1..R5 x {smc} x {qdii} table (tests/unit/test_cn_cxr.py:99-107, `assert ng.checkout(reported_c, fund_r, smc, is_qdii) == "match"`). The intercepted set E is exactly the {confirm_signed, confirm_declined} rows of `oc`, and under gate-off all of them settle directly, subject to the same QDII / 100-CNY overrides whose inputs (code, amt) are logged per row. 7_application.tex:38 in fact already names the derivable quantity: "what is measured is the incidence of above-cap product-push attempts per run" — a pure count over `oc`.

(2) "for every co row with act=subscribe, oc == oc_cf, so the difference is identically 0" is n


---

## 26. MEDIUM  -  The null policy's 'preregistered' parameters are ordinary run-config keys with no pin or equality check

**Where.** `flowmirror/engine/loop.py` 312 (with config/schemas/run.schema.json:28 and config/engine_defaults.yaml:7-24)

**What.**

_make_llm constructs `NullPolicyLLM(cfg.get("null_params") or {}, cfg["run_tag"])` (loop.py:312). null_params is a first-class, schema-valid run-config key (run.schema.json:28-...) whose thirteen values (p_redeem_gain, p_redeem_loss, redeem_pct, p_sub_base, chase_slope, sub_pct, p_sign_mismatch, p_like, p_save, p_follow, p_comment, stance_probs) are merged over config/engine_defaults.yaml:7-24 by deep_merge and accepted anywhere in [0,1] (or [0,100] for the pct keys). NullPolicyLLM itself only clamps them (null_policy.py:173-190). Nothing compares the supplied values with the preregistered defaults: `grep -n null_params flowmirror/config/validate.py flowmirror/config/loader.py flowmirror/engine/world.py` returns nothing, and run_meta records the values used without flagging a deviation. The module's own docstring (null_policy.py:38-41) and the schema description both assert these parameters 'MUST be fixed BEFORE any main-grid result is seen' — an assertion the code does not enforce.

**How it fails.**

After seeing the platform's Spearman score, an author edits runs/mock_10x3_null.json to set p_sub_base 0.05 -> 0.02 and chase_slope 2.0 -> 0.5. The run validates, executes, and produces a rule-based comparator that buys far less; the platform's advantage in GAP_VAL_BASELINES grows, and nothing in invariants_report.json, run_meta.json or the console output records that the baseline was retuned post hoc.

**Paper claim at risk.** 5_validation.tex:63-65 and GAP_VAL_BASELINES (the rule-based-agent comparator); the repo's own PREREG v1.5 §F item 12.

**Fix.**

Freeze the preregistered vector in code (a module-level constant in null_policy.py) and have validate_config reject or loudly flag any null_params that deviate from it, writing a null_params_sha and a 'preregistered: true/false' flag into run_meta.json so an analysis run can be excluded automatically.

**Verification (confirmed).**

I tried to refute this by hunting for any pin, invariant, or test that ties a run's `null_params` to the preregistered values. There is none. Every element of the claim reproduces.

1. The construction site is exactly as stated — D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\loop.py:311-312:
```
    if cfg.get("agent_policy") == "null":   # Card C: null policy wins over mock/live
        return NullPolicyLLM(cfg.get("null_params") or {}, cfg["run_tag"])
```
No comparison, no warning, no hash of the supplied dict.

2. The parameters really are ordinary schema-valid run-config keys. config/schemas/run.schema.json:28-... declares `null_params` with per-key `minimum`/`maximum` bounds only ("all scalar keys are numbers... Defaults live in config/engine_defaults.yaml"). I ran the failure scenario:
```
python -c "cfg=json.load(open('runs/mock_10x3_null.json')); cfg['null_params']['p_sub_base']=0.02; cfg['null_params']['chase_slope']=0.5; validate(cfg,'run')"
-> VALIDATES OK
NullPolicyLLM(cfg['null_params'],'x') -> applied p_sub_base 0.02 chase 0.5
```
So a retuned baseline passes validation and takes effect verbatim.

3. NullPolicyLLM only clamps (null_policy.py:76-84): `_num` casts to float with a fallback, `_prob` is `min(1.0, max(0.0, _num(...)))`. No equality check against defaults.

4. The documented MUST is real. null_policy.py:39-41: "every parameter in `null_params` MUST be fixed BEFORE any main-grid result is seen. The defaults below are the preregistered values and must not be tuned after the fact." config/engine_defaults.yaml:3-5 repeats it, as does run.schema.json:20 ("fix them before seeing main-grid results").

5. `grep -rn null_params` over the whole package hits only loop.py:22 (a docstring), loop.py:312, loop.py:1190 (a self-test fixture with `"null_params": {}`), null_policy.py:40 (the docstring) — nothing in flowmirror/config/validate.py, flowmirror/config/loader.py or flowmirror/engine/world.py, confirming the claim's grep.

6. No run-time invariant covers it. world.py:664-676 lists the full registry `INVARIANTS = {a_lagged_signals_only ... k_dec_matches_active}` — eleven keys, none about null-policy parameters — and `write_reports` emits one entry per registry key, so invariants_report.json can never mention a deviation.

7. The one check that *sounds* like the missing guard does not close the gap. null_policy.py:440-446:
```
    d = NullPolicyLLM()
    chk("null_preregistered_defaults",
        (d.p_redeem_gain, ... d.stance_probs) ==


---

## 27. MEDIUM  -  The sha256 verification of image files against the pool's image_sha256 does not exist in the engine

**Where.** `config/schemas/run.schema.json` 257

**What.**

The schema states "The engine joins <images_root>/<image_id> and verifies the file's sha256 against the pool's image_sha256 before attaching pixels on the TV arm; a missing file or hash mismatch attaches nothing and records an image_missing / image_sha_mismatch prompt note", repeated at `docs/RUNBOOK.md:118-124` and `config/engine_defaults.yaml:41-42`. No such comparison exists. `flowmirror/engine/loop.py:257` sets `"image_sha": sha256_text(str(image_path)) if image_path else None` — that is the sha256 of the path STRING, not of the file, and no code ever reads the card's `image_sha` back. `flowmirror/agents/prompt.py:408-414` base64-encodes the file and only then computes `sha256_file(ipath)`, appending it to `image_shas` for the cache key; it is compared against nothing. The string `image_sha_mismatch` appears in no `.py` file — it is a prompt note the engine cannot emit. (The real sha256 verification against `image_sha256` lives in the offline captioner, `data_pipeline/cn/caption_frozen.py:401-458`, which never runs in the simulation loop.)

**How it fails.**

An `images_root` is repointed at a stale or partially re-resized store whose files no longer match the pool's `image_sha256`. Once image resolution is implemented as documented, the engine attaches the wrong pixels for those `image_id`s, records no `image_sha_mismatch` note, and the run's provenance chain claims the pool's hashes while the prompts carried different bytes. The released `llm_cache.jsonl` keys would be consistent with themselves and inconsistent with the frozen pool.

**Paper claim at risk.** fundmarket-sim/paper/v7/sections/0_abstract.tex:74 ("per-item hashes") and 8_limits.tex:21 ("the recorded input hashes let a reader check that"), plus the appendix artifact claim that a reader can verify the exact rendered input. A per-item image hash that is the hash of a filename does not support that.

**Fix.**

When image resolution is implemented, compare `sha256_file(path)` against `note["image_sha256"][idx]` before attaching, emit `image_sha_mismatch` on mismatch, and store the file hash (not the path hash) in the card's `image_sha` at flowmirror/engine/loop.py:257.

**Verification (confirmed).**

Every specific assertion in the claim checks out, and the gap is wider than stated.

(1) `flowmirror/engine/loop.py:257`: `"image_sha": sha256_text(str(image_path)) if image_path else None` — `sha256_text` (imported at loop.py:111) hashes the path STRING, not file bytes. Grep for `get("image_sha")` and `["image_sha"]` across all .py files returns zero readers; the card key is write-only (all other occurrences are literal assignments in test/self-check fixtures at prompt.py:627,698,704,712,715, runtime.py:695, test_live_path.py:93, test_modality.py:381).

(2) `flowmirror/agents/prompt.py:403-416` is the ONLY TV attach path and gates on existence + extension alone:
    ipath = str(card.get("image_path") or "")
    exists = bool(ipath) and os.path.isfile(ipath)
    if exists and os.path.splitext(ipath)[1].lower() in MIME:
        url = image_data_url(ipath); attached = True
    if attached: parts.append({"type": "image_url", ...}); image_shas.append(sha256_file(ipath))
    else: notes.append("image_unsupported" if exists else "image_missing")
`sha256_file` is computed AFTER the bytes are already base64-encoded into the prompt, and is compared against nothing. It flows only into the prompt side-car: loop.py:419 `"image_shas": list(img_shas or []),`.

(3) `image_sha_mismatch` occurs in no .py file. Repo-wide hits are only config/schemas/run.schema.json:257, config/schemas/event.schema.json:133, docs/RUNBOOK.md:121 and :223, runs/demo_three_arm_images.json, and a copied `_what` string in runs/out/try_images/run_meta.json. prompt.py:416 can emit only `image_unsupported` / `image_missing`.

(4) Stronger than claimed: `images_root` appears in NO engine file whatsoever. The only .py hits are data_pipeline/cn/caption_frozen.py (offline captioner, which does verify — line 362: "file is accepted ONLY when its sha256 equals the row's image_sha256 at that index"; helpers `_resolve_by_id(image_id, expected_sha, images_root)` at :401 and `resolve_row_images` at :447). `image_pick`, `img_idx`, the run_meta `images: {root, attached, missing, sha_mismatch, policy}` block, and the `m_tv_arm_carries_images` invariant (all documented in docs/RUNBOOK.md:115-128, config/engine_defaults.yaml:41-45) likewise exist in no .py file. The engine derives the path solely from the note record: loop.py:250 `image_path = note.get("image_path") or note.get("image") or note.get("cover")` — keys the masked pool deliberately never ships. Consistent with runs/out/try_images/prompts/inv_00012_d0.js


---

## 28. MEDIUM  -  No per-agent-day cap on image-bearing TV cards exists, contradicting the paper's \maximgday = 3

**Where.** `flowmirror/engine/loop.py` 248-258

**What.**

The paper states a cap of one image per card (`\maximgcard{1}`) and three image-bearing cards per agent-day (`\maximgday{3}`, defined at fundmarket-sim/paper/v7/macros.tex:79-80), and builds an accounting argument on it. In the engine, `_feed_card` attaches at most one picture per card (the `\maximgcard{1}` half is satisfied by construction), but there is no per-agent-day limit anywhere: grepping `flowmirror/` for `max_img`, `img_cap` or any image counter returns nothing, and the feed fills `K = 6` slots per agent-day (`runs/demo_three_arm_images.json` `feed.K = 6`). Since 200/200 pool notes have `n_images > 0`, `_has_image` (`flowmirror/engine/world.py:535-542`) makes `post["img"]` True for every post, so once image resolution is implemented all 6 of a TV agent's cards would carry pixels, not 3. The legacy `p_image: 0.5` in `config/engine_defaults.yaml:56` is read by no code in `flowmirror/` either, so it does not impose the cap.

**How it fails.**

The paper's §3.3 accounting says "at most \maximgday{} of an agent's \Kslots{} \armTV{} cards can carry pixels, and ... $\dInput$ is therefore measured over a set of impressions of which a stated share carries no pixels at all". Once images are attached, the realised share reported into GAP_ARM_DELIVERY would be 6/6 rather than the 3/6 ceiling the text asserts, and the cost/token accounting in GAP_COST would be built on half the true image payload per agent-day.

**Paper claim at risk.** fundmarket-sim/paper/v7/sections/3_environment.tex:46 ("at most \maximgday{} of an agent's \Kslots{} \armTV{} cards can carry pixels"), 4_interventions.tex:63 and 6_experiments.tex:91 ("The caps of \maximgcard{} image per card and \maximgday{} per agent-day are unchanged").

**Fix.**

Either implement the per-agent-day cap in the feed/card path (stop attaching after the third image-bearing TV card in a given agent-day) and report the realised distribution in run_meta, or change \maximgday in fundmarket-sim/paper/v7/macros.tex:80 to the value the engine actually enforces and drop the "unchanged cap" sentences.

**Verification (confirmed).**

I tried to refute this and could not. Every leg of the claim checks out.

1. NO CAP EXISTS ANYWHERE IN THE ENGINE.
`D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\loop.py:248-250` is the only place a picture is bound to a card, and it is unconditional per post:
```
    image_path = None
    if post.get("img"):
        image_path = note.get("image_path") or note.get("image") or note.get("cover")
```
There is no counter, no per-agent-day state, and no caller-side limit. Card assembly at `loop.py:959-961` builds one card per shown pid with no image bookkeeping:
```
                cards = [_feed_card(W, shown[pid], notes_by_id, arm_by_pid[pid],
                                    heat_prev, clim_prev, top_prev, dt_cur, n_prev)
                         for pid in shown]
```
and the attach site `D:\Desktop\ABM paper\flowmirror_v7\flowmirror\agents\prompt.py:396-413` loops over every card, attaching pixels for each TV card whose file resolves, with no budget:
```
    for card in feed_cards:
        ...
        if arm != "TV":
            continue
        ...
        if attached:
            parts.append({"type": "image_url", "image_url": {"url": url}})
```
`grep -rn "max_img|img_cap|maximgday|image_cap|img_per_day"` over the whole repo returns zero engine hits (only `image_caption_frozen` matches, an unrelated TC key).

2. EMPIRICAL CHECK: 6/6, NOT 3/6. I ran `build_decision_messages` with K=6 TV cards each carrying a valid JPEG:
```
K cards = 6  image parts attached = 6
image_shas logged = 6
```
`feed.K` is 6 in every tracked run config (`runs/demo_three_arm_images.json:53`, `runs/mock_10x3.json:41`, etc.), and `modality_level: "agent"` (`runs/demo_three_arm_images.json`) means all 6 of a TV agent's cards are TV, so the ceiling is 6, exactly as the claim says.

3. THE PAPER DOES ASSERT THE CAP AS A CURRENT SYSTEM PROPERTY, IN FIVE PLACES.
`fundmarket-sim/paper/v7/macros.tex:79-80` defines `\maximgcard{1}` / `\maximgday{3}`; `sections/3_environment.tex:44` ("at most \maximgcard{} per card and \maximgday{} per agent-day"), `:46` (the accounting the claim quotes: "at most \maximgday{} of an agent's \Kslots{} \armTV{} cards can carry pixels"), `:50` item (iii) ("At most \maximgday{} of \Kslots{} \armTV{} cards carry pixels ... which pushes $|\dInput^{L}|$ toward zero"), `sections/4_interventions.tex:63` and `sections/6_experiments.tex:91` ("The caps ... are unchanged"), and `sections/appendix.tex:68`. The repo spec repeats the false premise: `fundmarket-sim/s


---

## 29. MEDIUM  -  SESOI never gates the supported branch: a statistically nonzero effect a hundred times smaller than the SESOI is reported as supported

**Where.** `flowmirror/analysis/modality.py` 110-113

**What.**

`_verdict` evaluates `if lo > 0: return "supported"` (line 110) before it evaluates the SESOI at line 112. The two conditions overlap for any interval with `0 < lo` and `hi < sesoi`, and precedence silently resolves the overlap in favour of "supported". So the smallest effect of interest is applied only to the null side and never to the positive side; "supported" means nothing more than "the interval excludes zero", exactly the inference that an equivalence-bounded three-way verdict exists to prevent. The module docstring at lines 15-17 claims "the verdict uses the effect-scale interval vs SESOI (h = 0.10, d = 0.20)", which is only half true. The problem compounds at the paper's planned run counts: paper/v7/macros.tex:90 sets \nrunarmseeds to 2, so run-level contrasts run at df = 1, where two closely agreeing seeds produce an arbitrarily narrow interval regardless of magnitude.

**How it fails.**

`_verdict(0.001, 0.02, SESOI["h"])` returns "supported". Through the real path: two seeds with per-seed effects h = 0.0010 and 0.0011 give `seed_t_interval` mean 0.00105, df 1, CI [0.00041, 0.00169] — an effect one hundredth of the SESOI of 0.10 — and `_verdict` returns "supported". The tool prints TV-T engagement_rate ... supported at n = 2 seeds, and the printed rule at line 613 offers no hint that the magnitude is below the pre-registered smallest effect of interest.

**Paper claim at risk.** paper/v7/sections/4_interventions.tex:79-81 (confirmatory family, one primary contrast at alpha=0.05, with a practical equivalence bound on its own scale); GAP_E1_PRIMARY

**Fix.**

Gate the positive branch on the margin as well, e.g. `if lo > sesoi: return "supported"`, or return a distinct label such as "nonzero_but_trivial" for `0 < lo and hi < sesoi`, and make the printed rule at line 613 state the magnitude condition.

**Verification (partly).**

MECHANISM CONFIRMED, EXACTLY AS STATED. D:\Desktop\ABM paper\flowmirror_v7\flowmirror\analysis\modality.py:107-113:

  107: def _verdict(lo, hi, sesoi):
  108:     if lo is None or hi is None or not _finite(lo) or not _finite(hi):
  109:         return "indeterminate"
  110:     if lo > 0:
  111:         return "supported"
  112:     if hi < sesoi:
  113:         return "bounded_null"

`lo > 0` short-circuits before the SESOI test, so the region `0 < lo` AND `hi < sesoi` resolves to "supported". Reproduced both stated paths verbatim:
  _verdict(0.001, 0.02, SESOI["h"]) -> "supported"
  seed_t_interval([0.0010, 0.0011]) -> (mean 0.00105, se 7.07e-05, lo 0.00041470, hi 0.00168530, df 1) -> _verdict(lo, hi, 0.10) -> "supported"
The claim's CI [0.00041, 0.00169] and df 1 match to the digit. df=1 survives the guard at line 570-574, which fires only on `if e.get("df") == 0`. No test covers the overlap region: tests/unit/test_analysis_modality.py and the in-module _self_test only ever plant rate diffs of +0.10/+0.15 (h far above SESOI), and assert "supported" there (test file lines 80, 232, 273). So nothing in the suite refutes the claim.

Bonus, same one-sided design, not in the claim: _verdict(-0.9, -0.5, 0.10) -> "bounded_null". A large NEGATIVE effect is labelled bounded_null because `sesoi` is a bare positive upper bound with no absolute value and no symmetric lower bound. That is a stronger instance of the same defect than the one alleged.

THREE SUPPORTING ARGUMENTS REFUTED, WHICH IS WHY THIS IS "partly":

(1) The docstring is NOT "only half true". Lines 16-17 state the rule verbatim, including the very precedence complained of: "uses the effect-scale interval vs SESOI (h = 0.10, d = 0.20): lo > 0 -> supported, hi < SESOI -> bounded_null, else indeterminate." The printed legend at line 613 says the same: "rule: eff CI lo > 0 -> supported; hi < SESOI -> bounded_null; else indeterminate". The rule is fully disclosed at both the API and the console; nothing is silent or hidden.

(2) The code MATCHES its pre-registration, so this is a methodology choice, not a code slip. docs/PREREG_v1.5_DRAFT.md:32: "SESOI: h = 0.10...; d = 0.20. 三分判定沿用 v1.2 §E（区间下界 > 0 / 上界 < SESOI / 不定）" — i.e. the frozen three-way rule IS lower bound > 0 for the positive branch. Changing it now would be a deviation from prereg, not a bug fix.

(3) The compounding aggravator is contradicted by the document it cites. paper/v7/macros.tex does not exist in flowmirror_v7; it is in the sibling r


---

## 30. MEDIUM  -  Exposure-concentration Gini is computed only over posts that received at least one impression, understating concentration and compressing the very contrast it measures

**Where.** `flowmirror/analysis/modality.py` 306-324 (specifically 309 and 318-324)

**What.**

`_meso` first looks for a heat log at line 309 (`ev.get("heat") or ev.get("hl")`). No such event name exists anywhere in the engine — the event names actually emitted are act, click, clim, cmt, co, dec, imp, post, refl, st — so that branch is dead and the proxy at lines 318-324 always runs. The proxy builds `counts` as a defaultdict populated only from `imp` rows, so a published post that was never shown to anyone contributes nothing at all: it is absent from the vector rather than entering it as a zero. Since Gini measures concentration of exposure across the published catalogue, dropping the zero-exposure tail systematically understates it. The bias is not neutral across the policies being contrasted: a more strongly popularity-weighted ranking starves more posts, which removes more zeros from the denominator, so the metric compresses exactly the difference it exists to detect. The heat_source field is set to "imp_counts" but nothing records how many published posts were excluded.

**How it fails.**

On the repo's `runs/out/demo_three-arm`: 40 posts are published (`post` events) and 32 received at least one impression. `_gini` over the 32 shown posts returns 0.5033 — the value the tool prints as heat_gini and the value that would fill the slot. `_gini` over all 40 published posts, counting the 8 starved posts as zero exposure, returns 0.6026. The reported concentration is 17% below the true value on the repo's own fixture. Under a policy contrast where one arm starves 8 posts and the other starves 20, the reported gap between the arms shrinks toward zero while the true gap widens, so the reproduction target "popularity-weighted ranking concentrates exposure — rises" can be recorded as not reproduced when it in fact holds.

**Paper claim at risk.** GAP_E2_MESO in fundmarket-sim/GAP_REPORT.md:99 ("Exposure concentration ... by policy"); the reproduction row "Popularity-weighted ranking concentrates exposure ... rises ... E2, GAP_E2_MESO" at paper/v7/sections/5_validation.tex:115

**Fix.**

Seed `counts` from the `post` events (`counts = {r["p"]: 0 for r in ev.get("post", [])}`) before accumulating impressions, so zero-exposure posts enter the vector as zeros, and record the published-post count and the starved-post count alongside heat_source. Also either implement or delete the dead heat-log branch at line 309, since as written it silently guarantees the proxy path.

**Verification (partly).**

CODE MECHANISM: FULLY CONFIRMED — I could not refute any part of it.

1) The heat-log branch is dead. `D:\Desktop\ABM paper\flowmirror_v7\flowmirror\analysis\modality.py:309`:
    heat_rows = ev.get("heat") or ev.get("hl") or []
`load_events` (flowmirror/analysis/common.py:42) keys rows by `row["ev"]`. A repo-wide grep for a `heat`/`hl` event name returns only two hits, both inside `flowmirror/channels/feed.py` (645 `"heat": heat,` and 677), and both are fields of an in-memory ranking record — never a logged event. On the fixture: `ev.get("heat") -> None`, `ev.get("hl") -> None`; the actual event keys are `['act','click','clim','cmt','co','dec','imp','post','refl','st']`. So the `if out["heat_gini_posts"] is None:` proxy at 318-324 always runs, and the unit test bakes that in — tests/unit/test_analysis_modality.py:98 asserts `m1["heat_source"] == "imp_counts"`.

2) The proxy drops zero-exposure posts. Lines 318-324:
    counts = defaultdict(int)
    for r in ev.get("imp", []):
        counts[r.get("p")] += 1
    if counts:
        out["heat_gini_posts"] = _gini(list(counts.values()))
        out["heat_source"] = "imp_counts"
Only `imp` rows create keys; the `post` event stream is never consulted, so a published-but-never-shown post is absent from the vector rather than entering as 0. The out-dict (307-308) has no field recording how many published posts were excluded — confirmed.

3) Numbers reproduce exactly. On runs/out/demo_three-arm: 40 `post` events, 32 distinct posts with >=1 `imp`. `_gini` over the 32 shown = 0.5033 (equals the value `_meso` returns, 0.503263888…, heat_source="imp_counts", printed at line 621 as `heat_gini=`). `_gini` over all 40 with the 8 starved posts as 0 = 0.6026. Reported value is 16.5% low. Other fixtures are far worse: `_dump` 0.1687 vs 0.5844; `try_images` 0.1632 vs 0.7211 (24 posts, 8 shown).

REFUTATION ATTEMPTS THAT FAILED:
- "The 8 unshown posts were structurally ineligible (published on the last day)." They are all from 2025-10-07, but same-day posts ARE candidates: engine/loop.py:886 `cand = [p for ps in recent_list for p in ps] + today`, and the fixture shows 10-01 posts impressed on 10-01 and 10-06 posts impressed on 10-06 (113 imps). The day-7 posts were in the pool and lost the ranking — genuine starvation, exactly what a concentration metric should capture.
- "Maybe the metric is deliberately conditional on exposure." The field is named `heat_gini_posts`, printed as `gini(posts)` (line 594), and the module docstr


---

## 31. MEDIUM  -  The single-seed guard keys off the difference df instead of the effect df, so an effect that could not be computed at all is reported as indeterminate with no warning

**Where.** `flowmirror/analysis/modality.py` 566-577 (specifically 571)

**What.**

The verdict is derived entirely from the effect-scale interval (`eff.get("lo")`, `eff.get("hi")` at line 576), but the insufficiency guard at line 571 tests `e.get("df")` — the df of the raw difference, not `eff.get("df")`. The two diverge whenever the effect is finite for fewer runs than the difference is. That happens by construction for aff_sum: `_effect` (lines 93-96) calls `cohen_d` with `_pooled_sd`, and when the pooled sd is zero `cohen_d` (common.py:88-92) returns +/-inf, which `_n` (lines 71-76) converts to None. Those Nones are filtered out at line 439, so `evals` is empty, `edf` is 0 and elo/ehi are None — while the difference df is a healthy n-1. The guard does not fire, `_verdict(None, None, ...)` returns "indeterminate" (line 108-109), and no warning is emitted either, because warnings are appended only inside the difference branch at lines 436-437 and never in the effect branch at lines 440-441. The reader sees an ordinary inconclusive row.

**How it fails.**

Built three seeds where aff_sum is constant at 1.0 in arm T and 9.0 in arm TV (zero within-arm variance, maximal separation). `analyze(..., level="agent")` returns for TV-T aff_sum: diff mean 8.0, df 2, raw CI [8.0, 8.0]; effect {'per_run': [None, None, None], 'mean': None, 'lo': None, 'hi': None, 'df': 0}; verdict "indeterminate"; and `[w for w in res["warnings"] if "aff_sum" in w]` is empty. The most extreme, most perfectly replicated result the design can produce is reported as inconclusive, and nothing in the output tells the reader the effect-scale test never ran.

**Paper claim at risk.** GAP_E1_PRIMARY decision rule ("Indeterminate: report the interval half-width and call the evidence insufficient") — the row would be filed as indeterminate rather than as a computable effect

**Fix.**

Make the guard test the quantity the verdict actually uses: branch on `eff.get("df") == 0` (or on both dfs), and mirror the "n_runs=<n>: descriptive only" warning into the effect branch at lines 440-441 and 516-517 so a withheld effect interval is always announced.

**Verification (confirmed).**

Reproduced exactly as stated. modality.py:571 `if e.get("df") == 0:` gates on the DIFFERENCE df while line 576 `_verdict(eff.get("lo"), eff.get("hi"), ...)` derives the verdict from the EFFECT interval. The two diverge for aff_sum: `_pooled_sd` returns 0.0 on zero within-arm variance (and whenever `dof <= 0`, lines 86-87), common.py:88-92 `return float("inf") if m1 > m2 else float("-inf")`, `_n` (71-76) maps that to None, line 439's `evals = [x for x in effs if _finite(x)]` drops it -> edf=0, elo/ehi=None, while `vals` (diffs) still has n=3 -> df=2. The warning append at 436-437 lives only in the difference branch; the effect branch at 438-441 appends nothing. My repro (3 seeds, aff_sum constant 1.0 in T / 9.0 in TV, 10 agents per arm, level="agent") returned for TV-T aff_sum: diff mean 8.0, df 2, CI [8.0,8.0]; effect {'per_run': [None,None,None], 'mean': None, 'lo': None, 'hi': None, 'df': 0}; verdict "indeterminate"; and `[w for w in res["warnings"] if "aff_sum" in w]` == []. No test covers it: the only aff_sum assertion (tests/unit/test_analysis_modality.py:82 and the mirror at modality.py:708) uses a fixture with aff_sum 5.0 in BOTH arms, so m1 == m2 and cohen_d takes the `return 0.0` branch, never the inf branch. The same hole exists at run level (lines 500-517: for aff_sum, `effs` is only rebuilt when `sp > 0`, so sp == 0 leaves it empty with no warning). Two refutation attempts failed: (a) the printed row is not fully blank -- `_ci` renders the effect CI as "[--]" -- but line 608 prints `e.get("df")` = 2, advertising three usable runs, and the verdict prints "indeterminate", so nothing tells the reader the effect-scale test never ran; (b) the guard's docstring intent (n_runs < 2) does not save it, because the verdict it gates is computed from a different quantity.


---

## 32. MEDIUM  -  Impressions from decisions that failed to parse stay in the rate denominator, so a differential parse-failure rate across arms becomes a spurious contrast

**Where.** `flowmirror/analysis/modality.py` 226-229 and 238-246

**What.**

`_aggregate` increments `rr["n_shown"] += 1` for every imp row at line 227-228, unconditionally. The numerator is accumulated at lines 238-246 only when the micro count `isinstance(v, (int, float))`. On a decision that fails to parse the engine still emits both: the imp rows go out at flowmirror/engine/loop.py:948 before the model call, and the dec row goes out at loop.py:996-1000 with `_dec_counts(None, ...)` returning `{k: None for k in keys}` (loop.py:535-537). So a failed agent-day contributes its full impression count to the denominator and nothing to the numerator, deflating engagement_rate and comment_rate in exact proportion to that arm's failure rate. `_aggregate` never reads the `status` field that the dec row carries, and nothing in the result reports the per-arm parse-failure rate, so the deflation is invisible. The bias is aligned with the treatment: the TV/TC prompts are the longest and image-bearing, and paper/v7/sections/appendix.tex:145 tolerates a parse-failure rate up to two percent before halting a run.

**How it fails.**

A true engagement rate of 0.20 in both arms, with the TV arm parsing at 98.0% and the T arm at 99.5% (both well inside the two-percent halt threshold): measured engagement_rate is 0.196 for TV and 0.199 for T, a TV-T difference of -0.003 with Cohen h = -0.0075, manufactured entirely out of parser behaviour with no behavioural difference present. Because `_verdict` has no lower bound (see the bounded_null finding) this lands in bounded_null rather than being flagged, and because the deflation is proportional it does not cancel across seeds — every seed shifts the same way, so the seed-level t-interval tightens around the artefact rather than covering it.

**Paper claim at risk.** GAP_E1_PRIMARY / the primary engagement contrast at paper/v7/sections/4_interventions.tex:79; GAP_COST which is supposed to report the decision-failure rate (fundmarket-sim/GAP_REPORT.md:128)

**Fix.**

Exclude the impressions of a failed agent-day from n_shown: track the day key on both imp and dec rows and subtract the impressions of any dec row whose micro counts are None (or whose `status` marks a parse failure). At minimum, count parse failures per arm in `_aggregate` and surface the per-arm rate in the result and the printed report so the deflation is visible next to the contrast.

**Verification (confirmed).**

Mechanism confirmed in code and reproduced numerically.

DENOMINATOR unconditional — flowmirror/analysis/modality.py:226-228:
    for r in imp_rows:
        rr = R(r.get("i"))
        rr["n_shown"] += 1

NUMERATOR gated — modality.py:238-243:
        if modern:
            for k in ("n_read", "n_like", "n_save", "n_follow", "n_comment"):
                v = r.get(k)
                if isinstance(v, (int, float)):
                    rr[k] = (rr[k] or 0) + v

All micro counts are None on parse failure — flowmirror/engine/loop.py:535-537:
    if adapted is None:
        return {k: None for k in keys}

The imp rows for a failed agent-day are already written: logd("imp", ...) at loop.py:948 runs during feed assembly, before the LLM phase (loop.py:977). The dec row is still emitted carrying status=rec.get("parser_status") (loop.py:997), and `grep -n "status" flowmirror/analysis/modality.py` matches nothing outside the synthetic fixture — _aggregate never reads it.

REPRODUCED (scratchpad script; true rate 4/20 = 0.200 in both arms, TV 2.0% of agent-days unparsed, T 0.5%):
    TV engagement_rate = 0.196
    T  engagement_rate = 0.199
    diff = -0.003  cohen h = -0.0075
Exactly the claimed figures, from parser behaviour alone with no behavioural difference.

Two details beyond the claim strengthen it:
- _pooled_metrics adds n_shown unconditionally (modality.py:279) but gates the numerator on `if r["n_like"] is not None` (modality.py:285), so an agent whose decisions ALL failed contributes its full impression count to the run-level pooled denominator and nothing to the numerator. Agent-level partly self-protects (modality.py:259-261 skips such an agent), run-level `pooled` does not.
- The correct denominator is logged and discarded: _dec_counts writes "n_read": int(n_cards) per parsed day (loop.py:544); modality.py accumulates n_read at line 240 and never uses it in any rate (grep n_read: only 7, 207, 211, 222, 240, 669).

TWO CORRECTIONS to the claim's framing (basis for medium, not high):
1. paper/v7/sections/appendix.tex:145 does not exist — this repo has no paper/ directory. The 2% figure is real but lives in code, loop.py:1009-1012: `thr = halt if isinstance(halt, float) and 0.0 < halt < 1.0 else 0.02`, applied to the run-POOLED S["decision_failures"] / S["decisions"].
2. Magnitude is capped well below SESOI (modality.py:58, h = 0.10). Because the halt gate is arm-pooled, one arm of K can carry ~K*thr; even a 6% single-arm failure rate at p = 0.20 gives h ~ 


---

## 33. MEDIUM  -  The t table stops at df=10 and falls back to the normal quantile, producing anti-conservative intervals for larger seed counts

**Where.** `flowmirror/analysis/common.py` 23-25 and 74

**What.**

`T_975` covers df 1 through 10 (lines 23-24) and `half = T_975.get(df, Z_975) * sd / math.sqrt(n)` at line 74 substitutes the normal quantile 1.959964 for every larger df. The substitution is documented at lines 8-10 and 57-59, but it is not a rounding convenience: for df just past the table the true two-sided quantile is materially larger, and the interval is too narrow in the direction that inflates the false-positive rate. The paper's current plan (paper/v7/macros.tex:55, \nseeds = 5; macros.tex:90, \nrunarmseeds = 2) keeps df at 4 and 1, inside the table, so the defect is latent today; it activates silently the moment the seed count is raised, with no warning and no visible change in the output format.

**How it fails.**

`seed_t_interval([float(x) for x in range(12)])` returns df 11 with half-width 2.0400. The correct t_{0.975}(11) = 2.201 gives 2.2909 — the reported interval is 11% too narrow. An effect whose true 95% interval is [-0.005, +0.105] would be reported as [+0.001, +0.099], flipping `_verdict` from indeterminate to "supported" purely from the quantile substitution. The same substitution at df = 30 still costs about 4%.

**Paper claim at risk.** paper/v7/sections/5_validation.tex:65 ("seed-level t-intervals over \nseeds environment draws, df = \nseeds-1") — the stated df would no longer describe the quantile actually used

**Fix.**

Extend T_975 to cover the df range the study can actually reach (at least through df=30) and raise a ValueError, or emit an explicit warning into the result, rather than silently substituting Z_975 for any df not in the table.

**Verification (confirmed).**

Reproduced from the code itself. flowmirror/analysis/common.py:22-25 defines `T_975` for df 1..10 only plus `Z_975 = 1.959964`, and line 74 is exactly `half = T_975.get(df, Z_975) * sd / math.sqrt(n)`, so every df > 10 silently uses the normal quantile.

Ran the stated failure scenario: `seed_t_interval([float(x) for x in range(12)])` returns df 11 with half-width 2.0399952 (sd 3.6055). Correct t_{0.975}(11) = 2.2010 gives 2.290873 — the reported interval is 11.0% too narrow (ratio 1.1230). At df = 30 the shortfall is 4.2% (2.0423/1.959964). Both figures match the claim exactly. Direction is always anti-conservative because z < t for every finite df; true coverage of the nominal 95% interval at df = 11 is about 92.4%, i.e. a ~7.6% type-I rate.

Silent activation confirmed: `dict.get` with a default raises nothing and logs nothing; `analyze(run_dirs)` is fed `ap.add_argument("run_dirs", nargs="*")` (modality.py:786) so the seed count is unbounded, and `_verdict` (modality.py:107-114) keys only off `lo > 0` / `hi < sesoi`, so a narrowed interval flips indeterminate -> supported as described.

The covering test does not pin the quantile — tests/unit/test_analysis_modality.py:46-48 asserts only `self.assertEqual(df, 11)` and `self.assertTrue(lo < m < hi)` (same in common.py:102-103 self-test), so a correct t table would pass unchanged. This corroborates "no visible change in the output format".

One factual error in the claim, not load-bearing: paper/v7/macros.tex does not exist in this repo (there is no paper/ directory; `nseeds`/`nrunarmseeds` appear nowhere), so the cited mitigation that df stays at 4 and 1 is unverifiable here — that weakens the latency argument, not the defect.

Severity corrected down from the implied critical/high: the error is bounded (11% at the worst reachable df, ~4% at df 30, ~1% at df 100), requires >=12 seeds to trigger, is disclosed three times in source as a frozen prereg convention (common.py:7-8, 22, 59-60), and produces no wrong number in any artifact that exists today. Latent statistical-validity bug with a one-line fix (extend the table or compute the t quantile).


---

## 34. MEDIUM  -  The within-run agent-clustered bootstrap the paper declares retired is still computed, exported and printed as a headline parameter

**Where.** `flowmirror/analysis/modality.py` 1-22, 365-377, 398-401, 584-587

**What.**

paper/v7/sections/4_interventions.tex:77 states "The within-run agent-clustered bootstrap of \preregver{} is retired", and paper/v7/sections/7_application.tex:9 repeats "the within-run agent-clustered bootstrap retired in Section~\ref{sec:interventions} is not used". The code still runs it: `_boot_diff_ci` at lines 365-377 draws 1000 agent resamples per contrast per metric per run, `_run_contrasts_agent` writes the result into every contrast record as `"boot_ci": [blo, bhi]` at line 400, the module docstring at lines 11-14 presents it as part of the reported statement, and `_print_report` announces "bootstrap=%d reps seed=%d" in the run header at lines 584-587 — the second line of every report, before any interval. Nothing in the output marks the field as retired or as non-inferential, so the `boot_ci` values sit in the result JSON as a second, narrower interval available to be read into a slot alongside the seed-level t-interval that the paper actually declares.

**How it fails.**

Running the tool on any multi-seed set prints "level=agent runs=5 bootstrap=1000 reps seed=2027 sesoi h=0.10 d=0.20" as the header, and the saved --out JSON carries a boot_ci pair on every per-run contrast. Anyone filling GAP_E1_PRIMARY or GAP_E2_POLICY from that JSON can take boot_ci as the interval — it is present, populated, and narrower than the seed-level interval because it resamples agents within a run rather than seeds across runs — producing a published interval that the methods section explicitly disowns and whose variance model covers the wrong source.

**Paper claim at risk.** paper/v7/sections/4_interventions.tex:77 and 7_application.tex:9 (the bootstrap is retired and not used); the variance-source labelling required by GAP_E1_PRIMARY ("which variance sources the interval covers")

**Fix.**

Either delete `_boot_diff_ci` and its `boot_ci` output along with the bootstrap line in the report header, or rename the key to something self-marking such as `boot_ci_retired_not_for_inference` and drop it from the header, so the retired estimator cannot be mistaken for the declared one.

**Verification (partly).**

MECHANICAL CORE: CONFIRMED. Every code fact in the claim reproduces exactly.

Paper side (note: the .tex files are NOT in flowmirror_v7 — that repo has no paper/ directory at all; they live in D:\Desktop\ABM paper\fundmarket-sim\paper\v7\sections\, verified verbatim):
- 4_interventions.tex:77 — "The within-run agent-clustered bootstrap of \preregver{} is retired."
- 7_application.tex:9 — "the within-run agent-clustered bootstrap retired in Section~\ref{sec:interventions} is not used, and no interval here is a band over society runs."

Code side, D:\Desktop\ABM paper\flowmirror_v7\flowmirror\analysis\modality.py:
- 56-57: `BOOT_REPS = 1000` / `BOOT_SEED = 2027`
- 365-366: `def _boot_diff_ci(v1, v2, reps=BOOT_REPS, seed=BOOT_SEED):` / `"""Agent-cluster bootstrap CI of mean(v1) - mean(v2), inside one run."""`
- 398-401 (claim said 400; it is 401): `blo, bhi = _boot_diff_ci(v1, v2)` ... `"boot_ci": [blo, bhi],` — inside the `for metric in ALL_METRICS` loop under `for hi, lo in _arm_pairs(...)`, i.e. per contrast per metric per run.
- 555: `rs["contrasts"] = _run_contrasts_agent(am, _agent_metrics(rec))`; 578 returns `"runs": runs`; 803 `json.dump(res, fh, ...)` — so boot_ci reaches the --out JSON.
- 585-587: `print("level=%s runs=%d bootstrap=%d reps seed=%d sesoi h=%.2f d=%.2f" % (res["level"], len(res["runs"]), BOOT_REPS, BOOT_SEED, ...))` — second line of the report.
- `boot_ci` occurs exactly ONCE in the entire repo outside runs/ (line 401). Nothing consumes it, nothing labels it, no test touches it (`grep -n "boot" tests/unit/test_analysis_modality.py` → no hits), and `grep -rni retired flowmirror/ tests/` → no hits.

The "narrower interval" assertion is empirically TRUE, and the artifacts already exist. runs/out/_gap_section7/mod.json (a staging file literally named for a paper GAP section) carries, for TV-TC engagement_rate:
  per-run: "boot_ci": [-0.0733, 0.0550]        (width 0.128)
  seed-level: "lo": -0.2522, "hi": 0.2772, "df": 1  (width 0.529)
A 4.1x narrower unlabelled interval sitting in the same file as the inferential one. Also present in runs/out/_gap_section5/mod_b.json and _gap_section7/mod_demo3.json.

WHERE THE CLAIM OVERREACHES (three points):

1. "the module docstring at lines 11-14 presents it as part of the reported statement" — refuted by reading four more words. Lines 13-15 read: "an agent-cluster bootstrap CI (1,000 reps, seed 2027) inside the run. Across runs (seeds) the seed-level Student-t interval (df = n_runs - 1) on each cont


---

## 35. LOW  -  `card["image_sha"]` hashes the path string, not the file, while the schema documents it as verified against the pool's `image_sha256`

**Where.** `flowmirror/engine/loop.py` 257

**What.**

`_feed_card` sets `"image_sha": sha256_text(str(image_path)) if image_path else None`. `sha256_text` on the path is not content addressing; the content hash used for the cache key is computed separately and correctly by `sha256_file(ipath)` at prompt.py:414. `config/schemas/run.schema.json:5` describes the image store as "verified against the pool's image_sha256 before any pixel is attached" and `config/schemas/event.schema.json:133` documents an `image_sha_mismatch` degradation, but `grep -rn 'image_sha_mismatch' flowmirror/` returns nothing and `build_decision_messages` only ever emits `image_missing`/`image_unsupported`/`tc_no_caption` (prompt.py:409-418). The card field is currently read by nothing, which is why this is low rather than high — but it is a trap for whoever wires `images_root`, since the field that looks like the verification hash is not one.

**How it fails.**

An engineer implements the `images_root` card and reuses `card["image_sha"]` as the value to compare against the pool's `image_sha256`. Every image passes verification regardless of its bytes, because the field is the SHA of the filename; a re-encoded or swapped file at the same path is attached silently and the promised `image_sha_mismatch` degradation never fires.

**Paper claim at risk.** 8_limits.tex:18 and appendix.tex:147 ("per-item hashes", inputs "identified by SHA-256 in the released manifest") — a reader checking that the images an agent saw match the released manifest hashes cannot do so from this field.

**Fix.**

Either set `"image_sha": sha256_file(image_path)` at loop.py:257 (guarding for a missing file) so the name matches the value, or drop the field from the card and from prompt.py's documented card contract (prompt.py:13) until the verification path exists, so nothing can mistake it for a content hash.

**Verification (partly).**

CODE FACT — CONFIRMED. flowmirror/engine/loop.py:257 is verbatim as claimed: `"image_sha": sha256_text(str(image_path)) if image_path else None,` and flowmirror/io/hashing.py:8-10 confirms sha256_text hashes the UTF-8 string, not file bytes. The genuine content hash lives elsewhere: prompt.py:414 `image_shas.append(sha256_file(ipath))`, and the cache-key path runtime.py:342-343 `return [sha256_text(u) for kind, u in _iter_message_parts(messages) if kind == "image"]` hashes the base64 data-URI (content-derived). So neither prompt_sha nor the cache key is contaminated. grep for reads of the field returns only writes (loop.py:257 plus literal `"image_sha": None` in prompt.py/runtime.py self-tests, tests/unit/test_live_path.py:93, tests/unit/test_modality.py:382); no test asserts its semantics — the _feed_card self-test at loop.py:1271-1283 checks ocr/caption/n_comments_prev only.

FRAMING — REFUTED on two points. (1) "the schema documents it as verified against the pool's image_sha256" is not what the schema says. config/schemas/run.schema.json:257 documents the CONFIG KEY images_root, not the card field: "The engine joins <images_root>/<image_id> and verifies the file's sha256 against the pool's image_sha256 before attaching pixels on the TV arm". The card key image_sha appears only as a bare name in two module docstrings (prompt.py:13, loop.py:29) that assign it no semantics. Nothing in the repo claims card["image_sha"] is the verification hash. (2) The missing image_sha_mismatch is an openly tracked TODO, not a silent inconsistency: docs/DEV_HANDOVER.md:81 lists card IMG-A against loop.py ("最高优先：TV 臂真正附图。按 image_ids 在 images_root 下解析、校验 sha256 ... 生成中") and DEV_HANDOVER.md:179 states "引擎侧解析仍未落地（IMG-A 卡待重跑）". `grep -rn images_root --include=*.py flowmirror/` returns nothing — the engine has no images_root code at all.

EXPOSURE WEAKER THAN CLAIMED. The field is not merely unread, it is always None on shipped data. loop.py:249-250 sources it as `note.get("image_path") or note.get("image") or note.get("cover")`, but parsing data/creatives/cn/content_pool_v1_masked.jsonl shows row keys ['caption','caption_masked',...,'image_ids','image_sha256','n_images','ocr_masked',...] with zero of the 200 note rows carrying image_path/image/cover (Counter() for all three). docs/RUNBOOK.md:109 agrees: the pool ships "image_ids + image_sha256 and deliberately NO file paths". Only the loop.py self-test fixture (line 1267, "image_path": "imgs/n1.jpg") makes the expression non-


---

## 36. LOW  -  Displayed NAV, three-month/one-year returns and the trend channel are computed from day t, not day t-1

**Where.** `flowmirror/engine/loop.py` 246, 281, 871, 925

**What.**

navday is built from `f.nav_at(dt_cur)` where dt_cur is the CURRENT trading day (loop.py:871), and Fund.nav_at returns the last NAV on or before d (world.py:94-95), i.e. today's close. That value is printed to the agent as its holdings NAV and floating P&L (loop.py:281, `"nav": round(nav, 4)`, `pnl_pct = nav/cst - 1`). The fund card's landing block uses `_hist_ret(f, dt_cur, 63)` and `_hist_ret(f, dt_cur, 252)` (loop.py:246); _hist_ret indexes with `bisect_right(f.dates, dt) - 1` (loop.py:193-198), which selects dt_cur itself, so ret_3m and ret_1y both end at today's close. The trend channel is built over `hist = W.nav_days[max(0, t - 125): t + 1]` (loop.py:925), whose last element is dt_cur, so '近1周/近1月/近3月/3月最大回撤/处于近半年高位' (loop.py:186-210) all incorporate day-t information. The engine then settles the trade at that same navday price.

**How it fails.**

On a day where a fund's NAV jumps +4 %, the card an agent reads before deciding already shows the post-jump three-month return and the post-jump holdings P&L, and _trend_position can flip that fund to '处于近半年高位' on the strength of a close that, in reality, is published only after the cut-off. Return-chasing and disposition-effect estimates in §5 Table 2 (GAP_VAL_MOMENTS) are then measured against a signal the agent could not have had, and the same-day close both drives and prices the trade. Invariant (a) does not catch it: world.py:748-757 inspects only the guba week key.

**Paper claim at risk.** 5_validation.tex:16 ('All four, and the NAV printed beside them, are read from state at or before the previous close'); appendix.tex:145 invariant (a); 3_environment.tex:16 Fig. 2b caption.

**Fix.**

Price the displayed channels off dt_prev = W.nav_days[t-1]: build a separate `navview` from nav_at(dt_prev) for _agent_view holdings and pass dt_prev into _hist_ret and into the trend `hist` slice (nav_days[max(0,t-126):t]), while keeping navday(dt_cur) for settlement only. Then extend invariant (a) to assert that every NAV-derived field on a card and in the agent view was computed from dt_prev.

**Verification (partly).**

EVERY CODE FACT IN THE CLAIM IS CORRECT — I reproduced all of them:

1. loop.py:871 `navday = {c: f.nav_at(dt_cur) for c, f in sorted(FUNDS.items())` under `for t, dt_cur in enumerate(W.nav_days)` (loop.py:866). world.py:94-95 `def nav_at(self, d): return self.navs[max(bisect_right(self.dates, d) - 1, 0)]  # last NAV on/before d`. world.py:328-329 builds `day_strs = sorted({d for s in nav_series.values() for d in s if lo <= d <= hi}); nav_days = [date.fromisoformat(d) ...]` from the SAME date keys as `Fund.dates` (world.py:346/350), with no shift — so `navday[c]` is literally the day-t close.
2. loop.py:246 `"ret_3m": _hist_ret(f, dt_cur, 63), "ret_1y": _hist_ret(f, dt_cur, 252)`, and loop.py:180 `j = bisect_right(f.dates, dt) - 1` → j indexes dt_cur itself.
3. loop.py:925 `hist = W.nav_days[max(0, t - 125): t + 1]` → last element is dt_cur; loop.py:198 `_trend_line` maps it through `fund.nav_at(dt)`, so 近1周/近1月/近3月/3月最大回撤/_trend_position all end at day t.
4. loop.py:281-282 `"nav": round(nav, 4) if nav else None, "pnl_pct": round(nav / cst - 1.0, 4)` from that same navday; loop.py:876 also refreshes `inv.gain_loss[code] = nav / cst - 1.0` before the prompt is frozen.
5. Settlement uses the same dict: loop.py:654 and loop.py:679 both `nav = navday[code]`.
6. Invariant (a) does not cover it: world.py:748-757 only compares `e["used"]` (guba week key) to `e["prev_live_end"]`; its own label is `"mechanism": "day t ranking input sha == end-of-(t-1) live sha"`. No test under tests/ references nav_at, navday, _hist_ret or _trend_line.

WHY THE SEVERITY IS OVERSTATED — three things the claim gets wrong about the consequence:

(a) The pricing half is documented, deliberate design, not a slip. docs/SANDBOX_INTERNAL_SPEC_v1.md:121 — "D2. 申赎结算 …：申购按当日净值… `[待对齐]` T+1 确认（当前当日成交，简化）"; docs/DECISIONS_2026-09-05_SANDBOX.md:19 decision #12 — "成交仍当日（不做 T+1）". Same-day NAV pricing is also the correct real-world rule for a pre-15:00 CN open-end-fund order. So "the same-day close … prices the trade" is by design, not a defect.

(b) Given (a), showing the day-t NAV is INTERNALLY CONSISTENT rather than a leak: the agent observes exactly the price at which it transacts. The mismatched alternative (show t-1, fill at t) would be the stranger one. And there is no exploitable look-ahead: I grepped for any write to `Fund.navs` or any price-impact term inside flowmirror/ and found none — every `navs[...]` site is a read (loop.py:181/184/188/201-203, world.py:95/101/449). NAV is frozen e


---

## 37. LOW  -  Dollar-cost-averaging subscriptions pay no subscription fee

**Where.** `flowmirror/engine/loop.py` 1018-1035

**What.**

The monthly DCA block debits `inv.cash -= amt` (loop.py:1029), credits `units = amt / nav` at full amount (1027), adds amt to the family subscription flow (1032) and logs the act row with a hard-coded `fee=0.0` (1035). It never applies cfg['fees']['subscribe_rate'], never calls _bump_fees and never increments S['fees_cny'] — unlike the feed-driven subscribe path (loop.py:648-660), which charges amt*sub_rate. Wealth conservation still holds because the cost basis is set to the gross amount, so invariant (d) cannot surface it.

**How it fails.**

Run runs/demo_three_arm.json (subscribe_rate 0.0012 = the paper's \feesub 0.12 %). Over 63 trading days each DCA-enabled agent makes up to three monthly purchases at 2 % of cash, all fee-free, while every feed-driven subscription of the same size pays the fee. run_meta.fees.total and the per-agent inv.fees therefore understate cumulative fees, and family net flow in flows_family_quarter.json mixes gross fee-free tickets with net fee-bearing ones — the panel that GAP_VAL_MOMENTS regresses for flow-performance convexity.

**Paper claim at risk.** 3_environment.tex:87 ('Subscriptions carry a 100 CNY minimum and a \feesub percent fee'; DCA is exempted only from the checkout); 5_validation.tex:16 fee accounting.

**Fix.**

Apply the same fee arithmetic in the DCA branch: fee = amt * sub_rate, units = (amt - fee)/nav, cost basis on amt - fee, _bump_fees(inv, fee), S['fees_cny'] += fee, and log the real fee on the act row.

**Verification (partly).**

CORE MECHANISM: CONFIRMED. `D:\Desktop\ABM paper\flowmirror_v7\flowmirror\engine\loop.py` lines 1017-1035 are exactly as described (claim's line refs are off by one in places, substance exact):

```
1017  if dt_cur.day == 1:                    # DCA (spec §3): not feed-driven, no CxR gate
1020          amt = inv.cash * 0.02
1026          units = amt / nav
1030          inv.cash -= amt
1033          flows[FUNDS[code].family][quarter_of(dt_cur)]["sub"] += amt
1034          logd("act", ..., kind="dca", fund=code,
1035               amt=round(amt, 2), units=round(units, 6), nav=nav, fee=0.0)
```
No `sub_rate`, no `_bump_fees`, no `S["fees_cny"]`. The feed path at 660-671 does all three: `fee = amt * sub_rate` / `units = (amt - fee) / nav` / `_bump_fees(inv, fee)` / `S["fees_cny"] += fee`. `sub_rate` is bound only inside `apply_decision` (line 582), so the DCA block cannot see it. Spec does not exempt DCA: `docs/PREREG_v1.5_DRAFT.md:48` "费率：申购 0.12%、赎回 0.5%（可配置）" with no DCA carve-out; `docs/SANDBOX_INTERNAL_SPEC_v1.md:121` exempts DCA only from C×R, not from fees. No test covers it (`tests/unit/` has no DCA case; `loop.py` self-test lines 1244-1263 exercise only `apply_decision`).

INVARIANT BLINDNESS: CONFIRMED. `world.py:770` computes `res = (a.cash + holdv + a.other + _agent_fees(a)) - (a.w0 + a.realized + (holdv - costv))`. DCA sets cost basis to the gross amount, so cash-out and cost-in cancel exactly.

REPRODUCTION (my run, 66 trading days x 40 agents, mock LLM, demo NAVs):
- counters: `{'dca_cny': 64389.97, 'dca_n': 14, 'fees_cny': 3908.58, 'sub_cny': 3257146.52, 'sub_n': 101}`
- every DCA act row: `{'t': 43, 'd': '2025-12-01', 'kind': 'dca', 'amt': 342.47, 'fee': 0.0}` vs subscribe `{'kind': 'subscribe', 'amt': 7236.9, 'fee': 8.68}`
- unbilled fee = 64389.97 * 0.0012 = 77.27 CNY, i.e. run_meta fees understated by 1.94%
- `d_wealth_conservation` still `pass: True, max_abs_residual_cny: 9.3e-10`

WHAT IS REFUTED IN THE CLAIM:
1. "family net flow ... mixes gross fee-free tickets with net fee-bearing ones" is FALSE. The feed path also books the GROSS ticket: line 670 `flows[fund.family][quarter_of(dt_cur)]["sub"] += amt` where `amt = pct * inv.cash` before the fee (`inv.cash -= amt`, line 665). I verified numerically: flows_family_quarter sub total 3321536.49 == sub_cny + dca_cny 3321536.49. The flow panel is apples-to-apples gross on both paths and is NOT contaminated.
2. "Over 63 trading days each DCA-enabled agent makes up to three monthly purchases" is

