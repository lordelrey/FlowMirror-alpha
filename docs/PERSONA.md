# Persona design (v7)

Personas are built in three layers and a runtime belief state. Field lists here
match `config/schemas/persona.schema.json` exactly.

## Layer 1 -- cell (persona grid)

One row of `data/population/persona_grid_v3.json` (`$defs.cell`):

| field | type | notes |
|---|---|---|
| `cell_id` | str | `bucket|gender|segment`, e.g. `c2_35|female|new` |
| `age` / `asset` | number | cell means |
| `risk_latent` | enum | `fragile` / `typical` / `tolerant` (true tolerance) |
| `weight` | 0..1 | sampling weight of the cell |
| `reported_C` | enum | self-reported risk class `C1`..`C5` (what the checkout sees) |
| `max_R_default` | enum | default product ceiling `R1`..`R5` |
| `cpt` | object | `lambda`, `alpha`, `w_plus`, `w_minus` (CPT parameters) |
| `kernel` | object | `chaser`, `allocator`, `social` mix, sums to ~1 |
| `traits` | object | free-form covariates (fin literacy, platform hours, ...) |

`reported_C` vs `risk_latent` is the crux of the suitability experiment: agents
may report a higher class than their latent tolerance supports.

## Layer 2 -- individual (cohort)

One row of `data/population/agents_seed2027.json` (`$defs.individual`):
`id` (`inv_00000`-style), `cell` (back-reference), `age`, `asset`,
`risk_latent`, `core` (`chaser`/`allocator`/`social`), `wealth_wan` (>=0),
`entry_day` (0..365), `reported_C`, `c_misreported` (bool), `traits`,
optional `strat_weight` (>0).

## Layer 3 -- card (rendered for the LLM)

A compact persona card assembled at runtime from layers 1-2 plus the current
belief state (text + image parts). The card -- not the raw JSON -- is what the
vision-LLM sees on every decision. Rendering lands in P1.

## Belief state (8 dimensions, `$defs.belief_state`)

| dim | type | meaning |
|---|---|---|
| `market_view` | int 1..5 | where the agent thinks the market is going |
| `risk_mood` | int 1..5 | current risk appetite |
| `trust` | map org -> number | trust per institution |
| `attention` | map | attention weights per fund / topic |
| `ref_point` | map | reference points per holding |
| `gain_loss` | map | unrealized gain/loss per holding |
| `experience` (optional) | map | recent memorable events |
| `trend_read` (optional) | map | the agent's reading of chart signals |
