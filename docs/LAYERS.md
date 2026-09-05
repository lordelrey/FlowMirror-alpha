# FlowMirror v7.1 - five-layer map (PROJECT_ARCHITECTURE_v7.1 S2)

Additive reorganisation: no module is moved and no behaviour changes. The
layers are (a) shared vocabulary in `flowmirror.core.types`, (b) layer
contracts in `flowmirror.core.protocols`, and (c) facades that re-export
today's implementations under layer-stable import paths.

Rule: **new code targets the Protocol; facades are removed as modules are
refactored.** Callers import from the facade package (never the legacy
module path), so the legacy path can be deleted once a native
implementation of the Protocol lands.

| Layer | Protocol (`flowmirror.core.protocols`) | Today's module(s) | Import from | Status |
|---|---|---|---|---|
| core: vocabulary | - | `flowmirror/core/types.py` | `flowmirror.core.types` | native |
| core: contracts | (defines all Protocols) | `flowmirror/core/protocols.py` | `flowmirror.core.protocols` | native |
| platform | `Platform` | `flowmirror/channels/feed.py` | `flowmirror.platform` | facade |
| society | `Society` | `flowmirror/engine/world.py`; `flowmirror/population/sampler.py` | `flowmirror.society` | facade |
| engine: game master | `GameMaster` | `flowmirror/engine/loop.py` (`apply_decision`, `_adapt_record`); `flowmirror/engine/world.py` (`EventLog`, `check_invariants`); `flowmirror/regulator/cn_cxr.py` (`cxr_outcome`) | `flowmirror.engine.gm` | facade |
| agent: perceive | `Perceiver` | `flowmirror/agents/prompt.py` (`render_*`, exposed as `PERCEIVERS`) | `flowmirror.agents.perceive` | facade |
| agent: memory | `Memory` | `flowmirror/agents/runtime.py` (`LLMCache`); `flowmirror/engine/loop.py` (`_adapt_record`) | - | planned |
| agent: deliberate | `Deliberator` | `flowmirror/agents/runtime.py` (`decide`, `reflect`, `call_glm`) | - | planned |
| agent: actuate | `Actuator` | inside `flowmirror/engine/loop.py` `apply_decision` | - | planned |
| regulator: institution | `InstitutionPolicy` | `flowmirror/regulator/cn_cxr.py` | - | planned |
| experiment | `Experiment` | `flowmirror/engine/loop.py` (`run_simulation`, CLI) | - | planned |

Lag rules in one line each (normative text lives in the Protocol
docstrings):

- Perceiver: renders only day-t published content; never pending proposals.
- Memory: day-t writes commit at day close; day-t reads see up to t-1.
- Deliberator: one decision per agent per day, on percepts(t) + memory(t-1).
- Actuator: proposals take effect only after GameMaster adjudication.
- GameMaster: adjudicates after all agents decided; single writer of the log.
- Platform: rank on tallies frozen at end of t-1; aggregate feeds day t+1.
- InstitutionPolicy: publishes after adjudication; visible from day t+1.
- Society: observes the closed day-t ledger; feeds the day t+1 refresh.
- Experiment: runs the whole scenario; no partial state into the live loop.

Notes:

- Facades add zero behaviour: every re-exported name is the same object as
  the original (asserted in each module's `--self-test` and in
  `tests/unit/test_facades.py`); `event_log.jsonl` is unchanged.
- `ARM_MODALITY` reserves `"TC"` (image-as-text). Today `arm_for_agent`
  returns only `"T"`/`"TV"`, so `"TC"` is vocabulary for the caption arm,
  not a live arm.
- Design stance unchanged: the engine computes numbers and renders one
  Chinese sentence; the LLM decides. Anti-priming words are checked only
  in our own framing blocks; real creatives are data.
- No existing file is modified and no run-config key is added, so the M0
  mock command and all old-config runs are unaffected.
