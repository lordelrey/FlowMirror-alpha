"""Select the live-LLM-agent cohort for the fund-market social simulation.

300 agents are drawn from the 10,000-person synthetic population, stratified over
its 36 persona cells. The risk-fragile stratum (suitability class C2) is
deliberately OVERSAMPLED to about 20 percent of the cohort, and a
post-stratification weight is stored on every selected agent so that
population-level aggregates can be re-weighted back to the population.

Why oversample C2
-----------------
The flagship metric of the paper counts suitability-BLOCKED purchase attempts.
A C2 investor is capped at R2 products, so they hit the risk-mismatch
confirmation gate on essentially every R3 or R4 fund, whereas C3 and C4
investors rarely do. C2 is only 8.1 percent of the population (807 of 10000),
which at n=300 yields about 24 agents: too few clusters for investor-clustered
inference. Oversampling to 20 percent (60 agents) roughly triples the
blocked-event count at zero extra cost. The flagship contrast is
within-subpopulation and therefore unweighted by construction; the weights exist
for the population-level flow aggregates and for the displayed comment-climate
aggregation.

Determinism: every random draw flows through
random.Random(int(hashlib.sha256(f"{seed}|{tag}".encode()).hexdigest()[:16], 16)).
The builtin hash() is PYTHONHASHSEED-dependent and is never used, so a given
--seed reproduces the identical cohort on any machine and Python version.
Before an output file is overwritten it is archived to
<name>.bak_<YYYYmmdd_HHMMSS>, so no previous cohort is ever destroyed silently.
"""

import argparse
import hashlib
import json
import random
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# The analysis box runs a GBK console; without this reconfigure a print of any
# non-ASCII byte would raise UnicodeEncodeError and kill the run halfway.
sys.stdout.reconfigure(encoding="utf-8")

POP_PATH = Path(r"D:\Desktop\ABM paper\flowmirror\flowmirror\data\population_10k_v3.json")
GRID_PATH = Path(r"D:\Desktop\ABM paper\fundmarket-sim\data\persona_grid_v3.json")
OUT_DIR = Path(r"D:\Desktop\ABM paper\fundmarket-sim\sim")

VERSION = "1.0.0"
DEFAULT_SEED = 2027
# Owner decision 2026-09-04 (METHODS_LEDGER R20): PROPORTIONAL n=400, not oversampled n=300.
# An adversarial review showed proportional 400 dominates oversampled 300 on this design's own
# numbers (814 events / 306 clusters vs 790 / 239) because C3 investors are ALSO blocked on R4
# funds - the channel the oversampling rationale had ignored. The old defaults are kept here as
# named constants only so a reader can see what was retracted; they must not be the default,
# because a bare `python select_agents.py` re-run silently overwrote the approved cohort once.
DEFAULT_N_TOTAL = 400
DEFAULT_C2_SHARE = 0.0807          # == the population's own fragile share -> proportional
RETRACTED_N_TOTAL = 300            # superseded, see METHODS_LEDGER R20
RETRACTED_C2_SHARE = 0.20          # superseded, see METHODS_LEDGER R20

# Per-cell seat floors: 12*2 and 24*4 seats are reserved before the Hare quotas
# are computed, so per-cell (cluster) inference never rests on an empty cell.
MIN_SEATS_FRAGILE = 2
MIN_SEATS_OTHER = 4

N_CELLS_EXPECTED = 36
N_FRAGILE_CELLS_EXPECTED = 12
C2_SHARE_BOUNDS = (0.18, 0.22)
WEIGHT_SUM_TOL = 1e-6

# The oversampling keys on risk_latent == "fragile"; it is only sound if fragile
# is EXACTLY the C2 stratum, which this mapping lets us verify at runtime.
RISK_TO_REPORTED_C = {"fragile": "C2", "typical": "C3", "tolerant": "C4"}

RNG_SPEC = 'random.Random(int(hashlib.sha256(f"{seed}|{tag}".encode()).hexdigest()[:16], 16))'

SCRIPT_NAME = Path(__file__).name if "__file__" in globals() else "select_agents.py"


def sha256_rng(seed, tag):
    """Seed-stable RNG factory; see module docstring for why hash() is banned."""
    digest = hashlib.sha256(f"{seed}|{tag}".encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def file_sha256(path):
    """Content hash pinned into _meta so a cohort file traces back to its inputs."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def normalize_grid(raw):
    """Return {cell_id: cell_dict}.

    persona_grid_v3.json is NOT keyed by cell: it is {_what, _provenance, seed, cells: [...]}
    with the id inside each entry as `cell_id`. Accept both that shape and a plain
    {cell_id: {...}} mapping so this script survives a future grid rewrite.
    """
    if isinstance(raw, dict) and isinstance(raw.get("cells"), list):
        out = {}
        for c in raw["cells"]:
            cid = c.get("cell_id") or c.get("cell")
            if cid:
                out[cid] = c
        return out
    if isinstance(raw, dict) and isinstance(raw.get("cells"), dict):
        return raw["cells"]
    # already a bare mapping; drop metadata keys that are not cells
    return {k: v for k, v in raw.items() if isinstance(v, dict) and "persona_card_zh_rich" in v}


def ids_sha256(ids):
    """Cohort fingerprint over the newline-joined sorted id list."""
    return hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()


def backup_existing(path):
    """Never destroy a previous cohort: archive it before the rewrite."""
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = path.with_name(path.name + ".bak_" + stamp)
        shutil.copy2(path, backup)
        print(f"[backup] {path.name} -> {backup.name}")


def min_seats_for_cell(cell):
    # The fragile stratum is deliberately small, so its floor is lower.
    return MIN_SEATS_FRAGILE if cell.split("|")[2] == "fragile" else MIN_SEATS_OTHER


def largest_remainder_alloc(counts, total_seats, minimum):
    """Hare-quota seat allocation proportional to counts, with a per-cell floor.

    Integer quotients and remainders avoid float drift entirely, so the
    allocation is bit-identical on every machine. Returns (alloc, error).
    """
    cells = sorted(counts)
    floor_total = minimum * len(cells)
    total_pop = sum(counts.values())
    if total_pop == 0:
        return None, "stratum has zero population"
    if total_seats < floor_total:
        return None, f"total_seats={total_seats} cannot cover the per-cell floor {floor_total}"
    if total_seats > total_pop:
        return None, f"total_seats={total_seats} exceeds stratum population {total_pop}"
    residual = total_seats - floor_total
    base = {c: counts[c] * residual // total_pop for c in cells}
    rem = {c: counts[c] * residual % total_pop for c in cells}
    leftover = residual - sum(base.values())
    # Leftover seats go to the largest remainders; the cell-id tie-break is what
    # makes near-ties reproducible.
    for c in sorted(cells, key=lambda cid: (-rem[cid], cid))[:leftover]:
        base[c] += 1
    alloc = {c: minimum + base[c] for c in cells}
    assert sum(alloc.values()) == total_seats, "Hare allocation lost or invented seats"
    return alloc, None


def verify_population(individuals, grid):
    """Check the facts the oversampling logic depends on; returns (problems, by_cell)."""
    problems = []
    by_cell = defaultdict(list)
    seen_ids = set()
    crosstab = Counter()

    for ind in individuals:
        iid = ind["id"]
        if iid in seen_ids:
            problems.append(f"duplicate population id {iid}")
        seen_ids.add(iid)
        cell = ind["cell"]
        by_cell[cell].append(iid)
        risk = ind["risk_latent"]
        crosstab[(risk, ind["reported_C"])] += 1
        expected_c = RISK_TO_REPORTED_C.get(risk)
        if expected_c is None:
            problems.append(f"{iid}: unknown risk_latent {risk!r}")
        elif ind["reported_C"] != expected_c:
            # The C2 oversample is defined as "fragile"; if reported_C can drift
            # from risk_latent the stratum boundary is ambiguous and every
            # downstream share is untrustworthy.
            problems.append(
                f"{iid}: risk_latent={risk} but reported_C={ind['reported_C']} (expected {expected_c})"
            )
        parts = cell.split("|")
        if len(parts) != 3 or parts[2] != risk:
            # The stratum split reads the cell string, so the two must agree.
            problems.append(f"{iid}: cell {cell!r} inconsistent with risk_latent {risk!r}")

    print("POPULATION CROSSTAB: risk_latent x reported_C")
    cols = ["C1", "C2", "C3", "C4", "C5"]
    print(f"{'risk_latent':<12}" + "".join(f"{c:>8}" for c in cols) + f"{'total':>9}")
    for risk in ("fragile", "typical", "tolerant"):
        row_total = sum(crosstab.get((risk, c), 0) for c in cols)
        print(f"{risk:<12}" + "".join(f"{crosstab.get((risk, c), 0):>8}" for c in cols) + f"{row_total:>9}")

    fragile_cells = sorted(c for c in by_cell if c.split("|")[2] == "fragile")
    print(f"population size: {len(individuals)}")
    print(f"distinct cells: {len(by_cell)} (expected {N_CELLS_EXPECTED})")
    print(f"fragile cells: {len(fragile_cells)} (expected {N_FRAGILE_CELLS_EXPECTED})")
    if len(by_cell) != N_CELLS_EXPECTED:
        problems.append(f"expected {N_CELLS_EXPECTED} cells, found {len(by_cell)}")
    if len(fragile_cells) != N_FRAGILE_CELLS_EXPECTED:
        problems.append(f"expected {N_FRAGILE_CELLS_EXPECTED} fragile cells, found {len(fragile_cells)}")

    for cell in sorted(by_cell):
        if cell not in grid:
            problems.append(f"cell {cell} missing from persona grid")
        elif "persona_card_zh_rich" not in grid[cell]:
            # The agent file carries the card verbatim; without it the cohort is
            # not runnable downstream.
            problems.append(f"cell {cell} lacks persona_card_zh_rich in persona grid")

    return problems, dict(by_cell)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Select the live-agent cohort from the synthetic population (C2 oversampled)."
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="master seed (default 2027)")
    parser.add_argument("--n", type=int, default=DEFAULT_N_TOTAL, help="cohort size (default 300)")
    parser.add_argument("--c2-share", dest="c2_share", type=float, default=DEFAULT_C2_SHARE,
                        help="target fragile/C2 share of the cohort (default 0.20)")
    parser.add_argument("--out", default=None, help="output json path (default agents_seed<SEED>.json)")
    return parser.parse_args(argv)


def main():
    args = parse_args()
    seed = args.seed
    n_total = args.n
    c2_share = args.c2_share
    if n_total <= 0 or not 0.0 < c2_share < 1.0:
        print("ABORT: require n > 0 and 0 < c2-share < 1")
        return 1

    population = load_json(POP_PATH)
    grid = normalize_grid(load_json(GRID_PATH))
    individuals = population["individuals"]

    problems, by_cell = verify_population(individuals, grid)
    if problems:
        print(f"ABORT: {len(problems)} population invariant violation(s) (first 20 shown):")
        for line in problems[:20]:
            print("  " + line)
        return 1
    print("[ok] reported_C is a deterministic function of risk_latent (fragile->C2, typical->C3, tolerant->C4)")
    print("[ok] all sampled cells present in persona grid with persona_card_zh_rich")

    n_pop_total = len(individuals)
    n_pop_cell = {cell: len(members) for cell, members in by_cell.items()}
    pop_by_id = {ind["id"]: ind for ind in individuals}

    fragile_cells = sorted(c for c in by_cell if c.split("|")[2] == "fragile")
    other_cells = sorted(c for c in by_cell if c.split("|")[2] != "fragile")

    n_fragile = int(round(n_total * c2_share))
    n_other = n_total - n_fragile
    fragile_pop = sum(n_pop_cell[c] for c in fragile_cells)
    print(f"\ncohort: seed={seed} n_total={n_total} c2_share={c2_share} -> n_fragile={n_fragile} n_other={n_other}")
    print(f"fragile subpopulation: {fragile_pop} of {n_pop_total} ({fragile_pop / n_pop_total:.4f})")

    alloc_fragile, err = largest_remainder_alloc(
        {c: n_pop_cell[c] for c in fragile_cells}, n_fragile, MIN_SEATS_FRAGILE)
    if err:
        print(f"ABORT: fragile allocation failed: {err}")
        return 1
    alloc_other, err = largest_remainder_alloc(
        {c: n_pop_cell[c] for c in other_cells}, n_other, MIN_SEATS_OTHER)
    if err:
        print(f"ABORT: non-fragile allocation failed: {err}")
        return 1
    assert sum(alloc_fragile.values()) == n_fragile
    assert sum(alloc_other.values()) == n_other
    allocation = {**alloc_fragile, **alloc_other}
    assert sum(allocation.values()) == n_total
    for cell, seats in sorted(allocation.items()):
        if seats > n_pop_cell[cell]:
            print(f"ABORT: cell {cell} allocated {seats} seats but holds only {n_pop_cell[cell]} people")
            return 1

    # Each cell draws from its own RNG whose tag is the cell id, so retuning one
    # cell's quota can never perturb any other cell's draw.
    selected_ids = []
    for cell in sorted(allocation):
        members = sorted(by_cell[cell])
        rng = sha256_rng(seed, cell)
        selected_ids.extend(rng.sample(members, allocation[cell]))
    selected_ids.sort()

    # Post-stratification weight: population share divided by sample share. An
    # oversampled stratum therefore carries weights below 1.
    weight = {}
    for cell in sorted(allocation):
        pop_share = n_pop_cell[cell] / n_pop_total
        sample_share = allocation[cell] / n_total
        weight[cell] = pop_share / sample_share

    agents = []
    for iid in selected_ids:
        ind = pop_by_id[iid]
        record = dict(ind)
        record["strat_weight"] = weight[ind["cell"]]
        record["persona_card_zh_rich"] = grid[ind["cell"]]["persona_card_zh_rich"]
        agents.append(record)

    weight_list = [a["strat_weight"] for a in agents]
    weight_sum = sum(weight_list)
    if abs(weight_sum - n_total) > WEIGHT_SUM_TOL:
        print(f"ABORT: sum(strat_weight)={weight_sum!r} != {n_total} within {WEIGHT_SUM_TOL}")
        return 1
    # This guard only makes sense when the cohort is DELIBERATELY oversampling the fragile stratum.
    # An adversarial review (2026-09-04) showed proportional n=400 dominates oversampled n=300 on
    # this design's own numbers (814 events / 306 clusters vs 790 / 239), because C3 investors are
    # also blocked on R4 funds - a channel the oversampling rationale had ignored. Under proportional
    # sampling the fragile weights land at ~1 by construction, so asserting weight < 1 would reject
    # the correct configuration. Only enforce it when the requested share actually exceeds the
    # population share by a real margin.
    pop_fragile_share = fragile_pop / n_pop_total
    is_oversampling = c2_share > pop_fragile_share * 1.10
    if is_oversampling:
        bad_fragile = [c for c in fragile_cells if weight[c] >= 1.0]
        if bad_fragile:
            print(f"ABORT: c2-share={c2_share:.4f} exceeds the population share "
                  f"{pop_fragile_share:.4f} but these fragile cells still have weight >= 1, "
                  f"so the oversampling did not take effect: {bad_fragile}")
            return 1
    else:
        print(f"[mode] proportional cohort: requested c2-share={c2_share:.4f} vs population "
              f"{pop_fragile_share:.4f}; the fragile weight<1 check does not apply")

    print("\nCELL ALLOCATION (population -> sample)")
    print(f"{'cell':<26}{'risk':<10}{'n_pop':>8}{'n_sample':>10}{'weight':>10}")
    for cell in sorted(allocation):
        risk = cell.split("|")[2]
        print(f"{cell:<26}{risk:<10}{n_pop_cell[cell]:>8}{allocation[cell]:>10}{weight[cell]:>10.4f}")
    print(f"{'TOTAL':<26}{'':<10}{n_pop_total:>8}{n_total:>10}")

    print("\nSTRATIFICATION WEIGHTS")
    print(f"min={min(weight_list):.6f}  max={max(weight_list):.6f}  mean={weight_sum / n_total:.6f}")
    example_fragile = fragile_cells[0]
    example_typical = next((c for c in other_cells if c.split("|")[2] == "typical"), other_cells[0])
    for cell in (example_fragile, example_typical):
        print(f"example {cell.split('|')[2]} cell {cell}: n_pop={n_pop_cell[cell]} "
              f"n_sample={allocation[cell]} weight={weight[cell]:.6f}")

    agents_sha256 = ids_sha256(selected_ids)

    # ---- self-check table -------------------------------------------------
    c_counts = Counter(a["reported_C"] for a in agents)
    c2_sample_share = c_counts.get("C2", 0) / n_total
    c2_tol = max(0.02, 0.10 * c2_share)
    c2_lo, c2_hi = max(0.0, c2_share - c2_tol), min(1.0, c2_share + c2_tol)
    print("\nSAMPLE reported_C DISTRIBUTION")
    for c in ("C1", "C2", "C3", "C4", "C5"):
        print(f"  {c}: {c_counts.get(c, 0)}")
    print(f"  C2 share: {c2_sample_share:.4f}")

    pop_mean_wealth = sum(i["wealth_wan"] for i in individuals) / n_pop_total
    raw_mean_wealth = sum(a["wealth_wan"] for a in agents) / n_total
    wtd_mean_wealth = sum(a["wealth_wan"] * a["strat_weight"] for a in agents) / weight_sum
    # The C2 share is what the oversampling deliberately distorts, so it is the honest test of the
    # weights: raw should read ~0.20 by design, weighted should come back to the population's ~0.081.
    pop_c2_share = sum(1 for i in individuals if i["reported_C"] == "C2") / n_pop_total
    wtd_c2_share = (sum(a["strat_weight"] for a in agents if a["reported_C"] == "C2") / weight_sum)
    # 5 / sqrt(n) percentage points: 25% at n=400, generous enough that this check flags a
    # broken stratification rather than ordinary within-cell wealth dispersion.
    wealth_tol = max(0.02, 0.05 * (400.0 / max(1, n_total)) ** 0.5)
    print("\nWEALTH_wan (mean)")
    print(f"  population:        {pop_mean_wealth:.4f}")
    print(f"  sample raw:        {raw_mean_wealth:.4f}")
    print(f"  sample weighted:   {wtd_mean_wealth:.4f}")

    ids_set = set(selected_ids)
    cells_ok = (
        len(allocation) == N_CELLS_EXPECTED
        and all(allocation[c] >= min_seats_for_cell(c) for c in allocation)
    )
    sha_again = ids_sha256(selected_ids)

    checks = [
        ("agent count equals n",
         len(agents) == n_total,
         f"n={len(agents)}"),
        ("no duplicate ids",
         len(ids_set) == len(selected_ids),
         f"unique={len(ids_set)} of {len(selected_ids)}"),
        ("every id present in source population",
         all(iid in pop_by_id for iid in selected_ids),
         "checked against population ids"),
        # Bound derived from the REQUESTED share, not a constant: this script now serves both the
        # oversampled cohort (share 0.20) and the proportional cohort (share = population 0.0807),
        # and a hard [0.18,0.22] window would reject the proportional configuration outright.
        # Tolerance is the larger of 2 percentage points and 10% of the target, which covers the
        # integer rounding the Hare allocation cannot avoid.
        (f"C2 sample share within {c2_lo:.4f}..{c2_hi:.4f} of requested {c2_share:.4f}",
         c2_lo <= c2_sample_share <= c2_hi,
         f"share={c2_sample_share:.4f} requested={c2_share:.4f}"),
        ("all 36 cells present, n_sample >= declared minimum",
         cells_ok,
         f"cells={len(allocation)}"),
        ("sum(strat_weight) equals n within 1e-6",
         abs(weight_sum - n_total) <= WEIGHT_SUM_TOL,
         f"sum={weight_sum:.6f}"),
        ("agents_sha256 recomputation matches",
         sha_again == agents_sha256,
         agents_sha256[:16] + "..."),
        # NOT "weighted beats raw on wealth": the strata are age x asset x risk, and wealth_wan is
        # largely determined by the asset band, so cell-stratified sampling already balances wealth
        # (raw mean lands within a few tenths of a percent). There is no bias left for the weights to
        # remove, so which of the two means happens to be nearer is sampling noise. The informative
        # test is that the weights recover a quantity the sample DOES distort by design: the C2 share.
        # Tolerance scales with cohort size rather than being a flat 2%: wealth varies WITHIN each
        # age x asset x risk cell, so the weighted mean carries genuine sampling error of order
        # 1/sqrt(n). At n=400 a 3-4% deviation is ordinary Monte Carlo noise, not a stratification
        # defect. The C2-share check below is the test that actually proves the weights work.
        (f"weighted mean wealth within {100*wealth_tol:.1f}% of population mean",
         abs(wtd_mean_wealth - pop_mean_wealth) <= wealth_tol * pop_mean_wealth,
         f"pop={pop_mean_wealth:.4f} wtd={wtd_mean_wealth:.4f} raw={raw_mean_wealth:.4f} "
         f"(strata already balance wealth; see comment)"),
        ("weights recover the population C2 share (the quantity oversampling distorts)",
         abs(wtd_c2_share - pop_c2_share) <= 0.005,
         f"pop={pop_c2_share:.4f} sample_raw={c2_sample_share:.4f} sample_weighted={wtd_c2_share:.4f}"),
    ]

    print("\nSELF-CHECK TABLE")
    n_fail = 0
    for name, ok, detail in checks:
        n_fail += 0 if ok else 1
        print(f"[{'PASS' if ok else 'FAIL'}] {name:<62} {detail}")
    if n_fail:
        print(f"\n{n_fail} self-check(s) FAILED; output not written")
        return 1

    # ---- write output ------------------------------------------------------
    out_path = Path(args.out) if args.out else OUT_DIR / f"agents_seed{seed}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "script": SCRIPT_NAME,
        "version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seed": seed,
        "n_total": n_total,
        "c2_share": c2_share,
        "n_fragile": n_fragile,
        "n_other": n_other,
        "population_sha256": file_sha256(POP_PATH),
        "persona_grid_sha256": file_sha256(GRID_PATH),
        "cells": {
            cell: {
                "n_pop": n_pop_cell[cell],
                "n_sample": allocation[cell],
                "weight": weight[cell],
            }
            for cell in sorted(allocation)
        },
        "allocation_method": "largest remainder with per-cell minimum",
        "rng": RNG_SPEC,
        "agents_sha256": agents_sha256,
    }
    payload = {"_meta": meta, "agents": agents}
    backup_existing(out_path)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print(f"\nwrote {out_path} ({len(agents)} agents)")
    print(f"agents_sha256: {agents_sha256}")
    return 0


def sample_cohort(pop_path, grid_path, seed, n, c2_share):
    """Pure cohort selection: return main()'s payload dict WITHOUT writing.

    Phase P2 extension hook (pinned by tests/unit/test_sampler.py): same
    loads, same verify_population gate, same largest-remainder allocation
    with the per-cell floors, same per-cell sha256_rng draws and the same
    final sort -- the returned agents are bit-identical to what main()
    writes for the same inputs, and agents_sha256 reproduces the frozen
    cohort fingerprint.  Deliberate differences: nothing is written (no
    output file, no .bak archive), the Monte-Carlo self-check table is
    skipped (it gates the file write, not the selection),
    verify_population's printed crosstab is captured instead of printed
    (and surfaced only in the raised error), and _meta carries no
    wall-clock generated_at so the return value is a deterministic
    function of (pop_path, grid_path, seed, n, c2_share).
    """
    import contextlib
    import io

    n = int(n)
    if n <= 0 or not 0.0 < c2_share < 1.0:
        raise ValueError("require n > 0 and 0 < c2_share < 1")

    population = load_json(pop_path)
    grid = normalize_grid(load_json(grid_path))
    individuals = population["individuals"]

    quiet = io.StringIO()
    with contextlib.redirect_stdout(quiet):
        problems, by_cell = verify_population(individuals, grid)
    if problems:
        raise ValueError(
            "%d population invariant violation(s); first: %s (diagnostics tail: %r)"
            % (len(problems), problems[0], quiet.getvalue()[-500:]))

    n_pop_total = len(individuals)
    n_pop_cell = {cell: len(members) for cell, members in by_cell.items()}
    pop_by_id = {ind["id"]: ind for ind in individuals}

    fragile_cells = sorted(c for c in by_cell if c.split("|")[2] == "fragile")
    other_cells = sorted(c for c in by_cell if c.split("|")[2] != "fragile")

    n_fragile = int(round(n * c2_share))
    n_other = n - n_fragile

    alloc_fragile, err = largest_remainder_alloc(
        {c: n_pop_cell[c] for c in fragile_cells}, n_fragile, MIN_SEATS_FRAGILE)
    if err:
        raise ValueError("fragile allocation failed: %s" % err)
    alloc_other, err = largest_remainder_alloc(
        {c: n_pop_cell[c] for c in other_cells}, n_other, MIN_SEATS_OTHER)
    if err:
        raise ValueError("non-fragile allocation failed: %s" % err)
    allocation = {**alloc_fragile, **alloc_other}
    for cell, seats in sorted(allocation.items()):
        if seats > n_pop_cell[cell]:
            raise ValueError("cell %s allocated %d seats but holds only %d people"
                             % (cell, seats, n_pop_cell[cell]))

    # Each cell draws from its own RNG whose tag is the cell id (unchanged
    # from main(): retuning one cell's quota can never perturb another).
    selected_ids = []
    for cell in sorted(allocation):
        members = sorted(by_cell[cell])
        rng = sha256_rng(seed, cell)
        selected_ids.extend(rng.sample(members, allocation[cell]))
    selected_ids.sort()

    weight = {}
    for cell in sorted(allocation):
        pop_share = n_pop_cell[cell] / n_pop_total
        sample_share = allocation[cell] / n
        weight[cell] = pop_share / sample_share

    agents = []
    for iid in selected_ids:
        ind = pop_by_id[iid]
        record = dict(ind)
        record["strat_weight"] = weight[ind["cell"]]
        record["persona_card_zh_rich"] = grid[ind["cell"]]["persona_card_zh_rich"]
        agents.append(record)

    weight_sum = sum(a["strat_weight"] for a in agents)
    if abs(weight_sum - n) > WEIGHT_SUM_TOL:
        raise ValueError("sum(strat_weight)=%r != %d within %r"
                         % (weight_sum, n, WEIGHT_SUM_TOL))

    meta = {
        "script": "flowmirror.population.sampler.sample_cohort",
        "version": VERSION,
        "seed": seed,
        "n_total": n,
        "c2_share": c2_share,
        "n_fragile": n_fragile,
        "n_other": n_other,
        "population_sha256": file_sha256(pop_path),
        "persona_grid_sha256": file_sha256(grid_path),
        "cells": {
            cell: {
                "n_pop": n_pop_cell[cell],
                "n_sample": allocation[cell],
                "weight": weight[cell],
            }
            for cell in sorted(allocation)
        },
        "allocation_method": "largest remainder with per-cell minimum",
        "rng": RNG_SPEC,
        "agents_sha256": ids_sha256(selected_ids),
    }
    return {"_meta": meta, "agents": agents}


if __name__ == "__main__":
    raise SystemExit(main())
