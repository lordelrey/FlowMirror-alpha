"""Pure platform-layer functions for the fund-marketing live-LLM simulation.

engine_v6.py imports this module for the recommender mechanics: the
three-source (follow / fit / trending) slot filler, the explicit hot-score
decay, modality-arm randomization, and the lagged comment climate.  Everything
here is a pure function of its arguments: no file I/O, no printing outside
self_test(), no global mutable state, and no sqlite3 (xhs_data.db is
off-limits to sim code).

Modality-arm contract (PREREG v1.5 A; tolerances per the corrected v1.3
B10): the PRE-REGISTERED agent-level assignment is assign_agent_arms(), a
stratified block randomisation over the population cells.  Within each
cell the agent ids are ordered by sha256(f"{run_tag}|arm|{agent_id}") and
the arms are dealt round-robin down that order; the per-cell starting arm
is the rotation int(sha256(f"{run_tag}|{cell}")[:16], 16) % k, and among
the starting arms that leave the CELL equally balanced the one that also
keeps the running global counts (cells are dealt in sorted cell order)
closest to exact balance wins.  Every cell therefore comes out as
balanced as its size allows (each arm receives floor(n_cell/k) or
ceil(n_cell/k) agents, so no cell of size >= 2 is single-armed) and the
overall share stays within one agent of 1/k for the project's k=2/k=3
arm sets.  arm_for_agent() is the UNBALANCED per-agent coin on the same
sha256(run_tag|arm|agent_id) stream: it is NOT the pre-registered
assignment (an independent coin cannot meet the B10 tolerances at
n=400) and survives only for callers that have no cohort (tests, the
exposure path) and for bit-for-bit replay of pre-v1.5 two-arm configs --
arms=("T","TV") keeps the exact v1.3 coin (random() < 0.5 -> "TV"),
retained verbatim as _arm_for_agent_legacy(), the regression reference
the self-tests assert against.  Any other arm set -- e.g.
("T","TC","TV") for the three-arm modality, where TC shows the note's
OCR text / frozen caption instead of pixels -- draws int(rng.random()*k)
on the SAME stream and indexes into arms in the order given.
assign_arms() (per-exposure, the modality_level == "exposure"
sensitivity option) keeps the v1.3 greedy block balancing verbatim for
the {T, TV} set; other arm sets use the same int(rng.random()*k)
multinomial draws.  check_arm_balance() verifies invariant (h)
generalized to k arms against what block randomisation can achieve:
OVERALL every arm's share within 0.01 of 1/k, and WITHIN EACH CELL
within _cell_tolerance(n_cell, k) of 1/k (exactly 1/(2*n_cell) + 1e-9
for the two-arm set; the provable floor max(n mod k, k - n mod k)/(k*n)
for k >= 3 when that is larger).  The arm set is INFERRED from the
values unless arms= is passed explicitly (the engine passes the
configured set so an arm that received zero agents still fails), and
the report carries the observed worst cases (worst_overall_dev,
worst_cell_dev, single_armed_cells) so the engine's richer invariants
report can surface them.

Runtime climate weighting (PREREG v1.2 B, kept in v1.3): climate_for()
accepts weights (agent_id -> strat_weight) and counts each comment at
that weight; under proportional sampling strat_weight ~= 1 so this path
is the identity, but it must exist so the GAP_S6_CLIMWT consistency
check can be run.  hot_score() stays weight-agnostic: the engine passes
already-weighted engagement counts.

Ranking contract: rank_feed(mode="three_source") is the frozen reference
recommender; mode="random" fills the K slots uniformly at random over the
candidate posts and is the primary control for anti-claim A1 (PREREG
v1.2 C).  Any other mode raises ValueError.  The trending stage selects,
among candidates not already placed by follow/fit, the highest heat_prev
posts, ties broken by score DESC then post_id ASC.

Determinism contract: the only hash used anywhere is hashlib.sha256
(builtin hash() is PYTHONHASHSEED-dependent); every RNG is seeded as
random.Random(int(sha256(f"{seed}|{tag}")[:16], 16)); no set is iterated
without sorted().  assign_agent_arms() uses no RNG at all -- it is pure
sha256 ordering -- and iterates cells and ids in sorted order only.

Lag contract: every social quantity consumed here (comments_prev,
heat_prev, clim_prev) was produced on day t-1 or earlier.  This module
must never be handed day-t engagement data, or the simulation's
"lagged social signal" invariant is silently broken.
"""

import hashlib
import math
import random
import sys

# The Windows console codec is GBK; force UTF-8 so the PASS/FAIL table and
# ids survive redirection.  Only counts/codes/ids/labels are ever printed.
sys.stdout.reconfigure(encoding="utf-8")

__all__ = [
    "hot_score",
    "climate_for",
    "top_comments",
    "assign_arms",
    "assign_agent_arms",
    "arm_for_agent",
    "check_arm_balance",
    "fit",
    "climate_bonus",
    "rank_feed",
    "self_test",
]


def _make_rng(seed, tag):
    """Project-standard deterministic RNG factory (see module docstring)."""
    return random.Random(
        int(hashlib.sha256(f"{seed}|{tag}".encode()).hexdigest()[:16], 16)
    )


def _state_get(agent_state, key, default=None):
    """Read a field from either an attribute-style agent or a plain dict.

    The engine is free to represent agents either way; refusing one of
    the two representations would force an edit here later.
    """
    if isinstance(agent_state, dict):
        return agent_state.get(key, default)
    return getattr(agent_state, key, default)


def _f(x, default=0.0):
    """Coerce JSON-ish numerics (None, numeric strings) to float, else default."""
    if x is None:
        return default
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def hot_score(likes, saves, comments, age_days, gamma=1.8):
    """TwinMarket/Reddit-style engagement decay.

    h = log10(likes + 2*saves + 3*comments + 1) / (age_days + 1) ** gamma

    age_days counts TRADING days since publication (0 on the publication
    day).  The engine must pass day-(t-1) engagement only, because every
    social signal an agent sees on day t was produced on day t-1 or
    earlier.  None and negative inputs are treated as 0 so a single
    malformed engagement field cannot poison a whole feed.  This function
    is weight-agnostic by design: when climate_weighting is on the engine
    multiplies likes/saves/comments by strat_weight BEFORE calling this.
    """
    cleaned = [0 if x is None or x < 0 else x for x in (likes, saves, comments)]
    likes, saves, comments = cleaned
    age = 0 if age_days is None or age_days < 0 else age_days
    weighted = likes + 2.0 * saves + 3.0 * comments + 1.0
    return math.log10(weighted) / ((age + 1.0) ** gamma)


def climate_for(post_id, comments_prev, min_n=4, weights=None):
    """Aggregate day-(t-1) comment stances for one post into a climate label.

    The +/-1/3 thresholds and the min_n=4 floor are frozen design
    parameters: below the floor a post shows "no_signal" rather than a
    noisy majority, which keeps thin comment threads from manufacturing
    fake social proof for the LLM prompt.  Returns (label, counts) where
    counts covers {bullish, bearish, watching} only.

    weights (agent_id -> strat_weight) enables the runtime climate
    weighting path (PREREG v1.2 B): each comment contributes its
    commenter's weight to the stance count instead of 1, the min_n floor
    is compared against the WEIGHTED total, and the returned counts are
    the weighted counts (floats).  weights=None reproduces the current
    integer behaviour exactly (it is also the numerical identity when
    every strat_weight equals 1.0, which is what the GAP_S6_CLIMWT
    consistency check relies on).
    """
    counts = {"bullish": 0, "bearish": 0, "watching": 0}
    for c in comments_prev or []:
        if c.get("post_id") != post_id:
            continue
        stance = c.get("stance")
        if stance in counts:  # no_comment rows never appear here, but stay defensive
            if weights is None:
                counts[stance] += 1
            else:
                # Unknown commenters count at 1.0 so a missing agent_id
                # cannot silently zero out a thread's climate.
                counts[stance] += float(weights.get(c.get("agent_id"), 1.0))
    total = counts["bullish"] + counts["bearish"] + counts["watching"]
    if total < min_n:
        return ("no_signal", counts)
    d = (counts["bullish"] - counts["bearish"]) / total
    if d > 1.0 / 3.0:
        label = "bullish_majority"
    elif d < -1.0 / 3.0:
        label = "bearish_majority"
    else:
        label = "mixed"
    return (label, counts)


def top_comments(post_id, comments_prev, k=3, weights=None):
    """Pick the k most authoritative day-(t-1) comments for a post.

    Familiarity with the posting org (fam_level) DESC, then agent_id ASC as
    the frozen deterministic tie-break.  This ranking must never be
    randomized: the excerpt shown inside an LLM prompt has to be identical
    across identically-seeded runs or the whole run is irreproducible.

    weights is accepted for signature symmetry with climate_for();
    reserved: candidate sampling by weighted stance share is performed by
    the engine before calling this function, so weights never alters the
    ranking here.
    """
    pool = [c for c in comments_prev or [] if c.get("post_id") == post_id]
    pool.sort(key=lambda c: (-int(c.get("fam_level") or 0), str(c.get("agent_id") or "")))
    return pool[: max(0, k)]


def _check_arms(arms):
    """Normalize/validate an arm sequence shared by arm_for_agent/assign_arms."""
    arms_t = tuple(arms)
    if not arms_t:
        raise ValueError("arms must be a non-empty sequence of modality labels")
    if len(set(arms_t)) != len(arms_t):
        raise ValueError("arms must be distinct modality labels")
    return arms_t


def _is_legacy_two_arm(arms_t):
    """True for any ordering of the frozen v1.3 two-arm set {T, TV}.

    Only this set has a frozen legacy draw that old configs must replay
    bit-for-bit; every other arm set uses the k-arm index draw.
    """
    return len(arms_t) == 2 and set(arms_t) == {"T", "TV"}


def assign_arms(rng, k, tally, arms=("T", "TV")):
    """Per-exposure modality-arm randomization (PREREG v1.3 B10, v1.5 A).

    Used only when config modality_level == "exposure" (the sensitivity
    option); the default modality level is the agent-level
    assign_agent_arms().

    For the {T, TV} set the v1.3 greedy block balancing is kept VERBATIM:
    |TV - T| <= 1 at all times, which is what guarantees engine invariant
    (h)'s OVERALL clause for exposure-level two-arm runs (each agent's
    lifetime TV share stays within +/-0.02 of 0.5 while individual
    exposures remain randomized).  For any other arm set each draw is
    int(rng.random()*k) indexed into arms (PREREG v1.5 A): per-agent
    lifetime shares then drift within binomial noise, which the
    exposure-level sensitivity design accepts.  tally is mutated in place
    on purpose; it belongs to the caller (the engine's per-agent state).
    """
    arms_t = _check_arms(arms)
    out = []
    if _is_legacy_two_arm(arms_t):
        for _ in range(max(0, int(k))):
            if tally["TV"] - tally["T"] >= 1:
                arm = "T"
            elif tally["T"] - tally["TV"] >= 1:
                arm = "TV"
            else:
                arm = "TV" if rng.random() < 0.5 else "T"
            tally[arm] = tally.get(arm, 0) + 1
            out.append(arm)
        return out
    n_arms = len(arms_t)
    for _ in range(max(0, int(k))):
        # min() is a belt-and-braces clamp: random() < 1.0 strictly, so
        # int(random()*k) is already in [0, k-1] for the small k used here.
        arm = arms_t[min(int(rng.random() * n_arms), n_arms - 1)]
        tally[arm] = tally.get(arm, 0) + 1
        out.append(arm)
    return out


def _arm_for_agent_legacy(run_tag, agent_id):
    """v1.3 two-arm draw, kept VERBATIM as the regression reference.

    arm_for_agent() with the default arms=("T","TV") must reproduce this
    bit-for-bit so runs configured before PREREG v1.5 replay identically;
    the self-tests assert equality over fixed id lists against this copy.
    """
    digest = hashlib.sha256(f"{run_tag}|arm|{agent_id}".encode()).hexdigest()
    return "TV" if random.Random(int(digest[:16], 16)).random() < 0.5 else "T"


def arm_for_agent(run_tag, agent_id, arms=("T", "TV")):
    """Agent-level modality-arm draw -- the UNBALANCED per-agent coin.

    This is NOT the pre-registered assignment: keyed only on
    sha256(f"{run_tag}|arm|{agent_id}") it is an independent coin per
    agent, which cannot meet the (corrected) PREREG B10 tolerances -- the
    SD of the two-arm overall share is 0.025 at n=400 (vs the 0.01
    tolerance) and cells of size 2 can never be balanced by coin flips.
    Callers that hold the cohort MUST use assign_agent_arms(); this
    function is kept only for callers that have no cohort (tests, the
    exposure path) and for bit-for-bit replay of pre-v1.5 two-arm
    configs.

    Derived from sha256(f"{run_tag}|arm|{agent_id}") so it is
    reproducible and independent of every other RNG stream; the digest is
    fed through the same hexdigest[:16] -> int seeding rule as _make_rng
    (note that _make_rng(f"{run_tag}|arm", agent_id) hashes the IDENTICAL
    string, so the stream is the same one the legacy draw used).

    arms defaults to the v1.3 two-arm set ("T","TV"); that case keeps the
    exact legacy coin (random() < 0.5 -> "TV") so existing runs are
    unchanged.  Any other arm set -- e.g. ("T","TC","TV") for the v1.5
    three-arm modality -- draws int(rng.random()*k) on the same stream and
    indexes into arms in the order given (PREREG v1.5 A).
    """
    arms_t = _check_arms(arms)
    if _is_legacy_two_arm(arms_t):
        return _arm_for_agent_legacy(run_tag, agent_id)
    rng = _make_rng(f"{run_tag}|arm", agent_id)
    n_arms = len(arms_t)
    return arms_t[min(int(rng.random() * n_arms), n_arms - 1)]


def _agent_digest(run_tag, agent_id):
    """sha256(f"{run_tag}|arm|{agent_id}") -- the frozen per-agent stream.

    The same digest arm_for_agent() seeds its draw from;
    assign_agent_arms() reuses it as the WITHIN-CELL sort key so the
    pre-registered assignment and the cohort-less coin stay on one
    auditable per-agent stream.
    """
    return hashlib.sha256(f"{run_tag}|arm|{agent_id}".encode()).hexdigest()


def assign_agent_arms(run_tag, agents, arms=("T", "TV")):
    """Stratified block randomisation of the agent-level arm (PREREG B10).

    agents is a sequence of (agent_id, cell) pairs (cell is stringified
    for grouping; duplicate agent ids are ignored after their first
    occurrence; the returned dict is keyed by the agent_id objects as
    passed, so int and str ids both round-trip).  Returns
    {agent_id: arm} covering exactly the distinct agent ids.

    Within each cell (cells are dealt in sorted cell order):
      1. the ids are ordered by sha256(f"{run_tag}|arm|{agent_id}"), the
         same per-agent stream arm_for_agent() draws on;
      2. the arms are dealt round-robin down that order starting from the
         rotation int(sha256(f"{run_tag}|{cell}")[:16], 16) % k, so the
         remainder agents of odd-sized cells do not all land on the same
         arm across cells;
      3. every starting arm leaves the CELL equally balanced (a
         round-robin deal always yields floor(n/k) / ceil(n/k) counts),
         so the start is chosen among the rotation order
         rot, rot+1, ..., rot+k-1 by which keeps the RUNNING GLOBAL
         counts closest to exact balance.  This correction is what pins
         the overall share to within one agent of 1/k for the project's
         k=2 / k=3 arm sets; a pure per-cell coin rotation would leave
         the odd-cell +/-1 remainders to chance and could not guarantee
         the 0.01 overall tolerance.

    Guarantees (k >= 2): each cell's counts are floor(n_cell/k) or
    ceil(n_cell/k) -- so no cell of size >= 2 is single-armed -- and the
    overall per-arm spread stays <= 1 agent (exact balance whenever k
    divides the cohort size; e.g. 200/200 at n=400, k=2).  Deterministic
    in all inputs (pure sha256, no RNG); changes with run_tag through
    both the per-cell rotation and the digest sort order.
    """
    arms_t = _check_arms(arms)
    k = len(arms_t)
    by_cell = {}
    seen = set()
    for agent_id, cell in agents:
        key = str(agent_id)
        if key in seen:
            continue
        seen.add(key)
        by_cell.setdefault(str(cell), []).append(agent_id)
    out = {}
    counts = {arm: 0 for arm in arms_t}
    for cell in sorted(by_cell):
        ids = sorted(by_cell[cell], key=lambda a: _agent_digest(run_tag, a))
        n_c = len(ids)
        rot = int(
            hashlib.sha256(f"{run_tag}|{cell}".encode()).hexdigest()[:16], 16
        ) % k
        best_start = rot
        best_spread = None
        for j in range(k):
            start = (rot + j) % k
            cand = dict(counts)
            for i in range(n_c):
                cand[arms_t[(start + i) % k]] += 1
            spread = max(cand.values()) - min(cand.values())
            if best_spread is None or spread < best_spread:
                best_spread = spread
                best_start = start
        for i, agent_id in enumerate(ids):
            arm = arms_t[(best_start + i) % k]
            out[agent_id] = arm
            counts[arm] += 1
    return out


# Frozen arm iteration/report order: TV before T keeps the two-arm {T, TV}
# report key-for-key identical to the v1.3 implementation; TC follows; any
# label outside the known modalities sorts after them (determinism).
_ARM_ORDER = ("TV", "T", "TC")

# Overall tolerance of invariant (h): every arm's share within this much
# of 1/k.  Stratified block randomisation guarantees <= 1/n, far inside
# this bound at cohort scale (1/400 = 0.0025 at n=400).
_OVERALL_ARM_TOL = 0.01


def _order_arms(arm_set):
    """Deterministic ordering of an arm label set (see _ARM_ORDER)."""
    known = [a for a in _ARM_ORDER if a in arm_set]
    extra = sorted(a for a in arm_set if a not in _ARM_ORDER)
    return tuple(known + extra)


def _cell_tolerance(n_cell, k):
    """Per-cell share tolerance: as balanced as the cell size permits.

    A round-robin deal gives every arm floor(n/k) or ceil(n/k) agents,
    so the largest per-arm share deviation a PERFECT deal must still
    allow is max(m, k-m)/(k*n) with m = n mod k (0 when k divides n).
    For k=2 this is exactly 1/(2*n) -- the corrected PREREG B10 per-cell
    tolerance.  For k>=3 it can exceed 1/(2*n): a 2-agent 3-arm cell is
    necessarily (1,1,0) and its empty arm deviates by 1/3 > 1/4, so the
    tolerance is the max of the two -- never failing a perfectly dealt
    cell, never accepting one a deal could have balanced further.
    """
    m = n_cell % k
    reach = max(m, k - m) if m else 0
    return max(1.0 / (2.0 * n_cell), reach / (k * n_cell)) + 1e-9


def check_arm_balance(agent_arms, agent_cells, arms=None):
    """Verify arm invariant (h) for stratified block randomisation; (ok, report).

    Checks what block randomisation can actually achieve (PREREG B10 as
    corrected): OVERALL, every arm's share is within _OVERALL_ARM_TOL
    (0.01) of 1/k; WITHIN EACH CELL, every arm's share is within
    _cell_tolerance(n_cell, k) of 1/k, i.e. as balanced as the cell size
    permits (exactly |share - 1/2| <= 1/(2*n_cell) + 1e-9 for the
    pre-registered two-arm set).

    The arm set is INFERRED from the values in agent_arms unless arms= is
    passed explicitly (the engine passes the configured modality_arms so
    an arm that received zero agents still fails); values outside the arm
    set fail via n_out_of_set.  report carries the observed numbers so
    the engine's richer invariants report can surface them: n_agents /
    n_arms / arms, per-arm n_<arm> and <arm>_share, overall_tolerance,
    worst_overall_dev (+ worst_overall_arm), n_cells, worst_cell_dev (+
    worst_cell + worst_cell_arm + worst_cell_tolerance),
    single_armed_cells (cells of size >= 2 served by a single arm),
    ok_overall, ok_cells, n_out_of_set.  Iteration goes over sorted ids /
    sorted cells only (determinism contract).
    """
    ids = sorted(agent_arms)
    n = len(ids)
    present = {str(v) for v in agent_arms.values()}
    wanted = {str(a) for a in arms} if arms is not None else present
    order = _order_arms(wanted)
    k = len(order)
    if n == 0 or k == 0:
        # Degenerate population/arm set: report a maximal violation instead
        # of dividing by zero.
        report = {"n_agents": n, "n_arms": k, "arms": list(order), "n_cells": 0,
                  "overall_tolerance": _OVERALL_ARM_TOL,
                  "worst_overall_dev": 1.0, "worst_overall_arm": None,
                  "worst_cell_dev": 1.0, "worst_cell": None,
                  "worst_cell_arm": None, "worst_cell_tolerance": 0.0,
                  "single_armed_cells": [], "n_out_of_set": 0,
                  "ok_overall": False, "ok_cells": False}
        for arm in order:
            report["n_" + arm.lower()] = 0
            report[arm.lower() + "_share"] = 0.0
        return (False, report)

    counts = {arm: 0 for arm in order}
    n_out = 0
    cell_arm = {}
    overall = {}
    for a in ids:
        v = str(agent_arms[a])
        if v in counts:
            counts[v] += 1
        else:
            n_out += 1
        c = str(agent_cells.get(a, "?"))
        overall[c] = overall.get(c, 0) + 1
        armc = cell_arm.setdefault(c, {})
        armc[v] = armc.get(v, 0) + 1
    cells = sorted(overall)

    report = {"n_agents": n, "n_arms": k, "arms": list(order),
              "n_cells": len(cells), "n_out_of_set": n_out}
    ok_overall = n_out == 0
    worst_overall_dev = 0.0
    worst_overall_arm = None
    for arm in order:
        n_arm = counts[arm]
        share = n_arm / n
        dev = abs(share - 1.0 / k)
        report["n_" + arm.lower()] = n_arm
        report[arm.lower() + "_share"] = share
        if dev > worst_overall_dev:
            worst_overall_dev, worst_overall_arm = dev, arm
        ok_overall = ok_overall and dev <= _OVERALL_ARM_TOL
    report["overall_tolerance"] = _OVERALL_ARM_TOL
    report["worst_overall_dev"] = worst_overall_dev
    report["worst_overall_arm"] = worst_overall_arm

    ok_cells = True
    worst_cell_dev = 0.0
    worst_cell = None
    worst_cell_arm = None
    worst_cell_tol = 0.0
    single_armed = []
    for cell in cells:
        n_c = overall[cell]
        tol = _cell_tolerance(n_c, k)
        armc = cell_arm[cell]
        n_present = sum(1 for arm in order if armc.get(arm, 0) > 0)
        if n_c >= 2 and n_present < 2:
            single_armed.append(cell)
        for arm in order:
            dev = abs(armc.get(arm, 0) / n_c - 1.0 / k)
            if dev > worst_cell_dev:
                worst_cell_dev, worst_cell, worst_cell_arm = dev, cell, arm
                worst_cell_tol = tol
            ok_cells = ok_cells and dev <= tol
    ok_cells = ok_cells and not single_armed
    report["ok_overall"] = ok_overall
    report["ok_cells"] = ok_cells
    report["worst_cell_dev"] = worst_cell_dev
    report["worst_cell"] = worst_cell
    report["worst_cell_arm"] = worst_cell_arm
    report["worst_cell_tolerance"] = worst_cell_tol
    report["single_armed_cells"] = single_armed
    return (ok_overall and ok_cells, report)


def fit(agent_state, post):
    """Bounded [0,1] suitability-adjacency heuristic for a (agent, post) pair.

    Deliberately a coarse prior, not a gate: the hard CSRC-style C x R rule
    lives in the engine and applies to SUBSCRIBE only.  Keeping this term
    bounded keeps the w_fit weighting stable regardless of agent type.
    """
    s = 0.5
    ig = post.get("intent_group")
    risk = _state_get(agent_state, "risk_latent", None)
    core = _state_get(agent_state, "core", None)
    if ig == "I2":
        if risk == "tolerant":
            s += 0.25
        elif risk == "fragile":
            s -= 0.25
        if core == "chaser":
            s += 0.15
    elif ig == "nonI2":
        if core == "allocator":
            s += 0.15
    return max(0.0, min(1.0, s))


def climate_bonus(label):
    """Map a lagged climate label onto the additive social term of the score."""
    if label == "bullish_majority":
        return 1.0
    if label == "bearish_majority":
        return -1.0
    # "mixed", "no_signal" and anything unrecognized contribute nothing.
    return 0.0


def rank_feed(agent_state, candidates, heat_prev, clim_prev, cfg, rng,
              mode="three_source"):
    """PolicySim-style three-source recommender: follow -> fit -> trending.

    mode="three_source" (default, the frozen reference feed) behaves as
    described below.  mode="random" is the A1 anti-claim control (PREREG
    v1.2 C): scores, sources and slot quotas are ignored entirely and
    min(K, len(candidates)) distinct candidates are drawn uniformly with
    rng.sample, each labelled "random"; the behavioural agent is left
    unchanged, only the feed differs.  Any other mode raises ValueError.

    cfg keys: slots {"follow":2,"fit":2,"trending":2} (summing to K,
    default 6) and weights w_trust, w_fit, w_heat, w_soc (defaults
    1.0/1.0/1.0/0.5) plus eps (default 0.05).  eps must stay SMALL:
    engine_v5 added an unscaled U(0,1) tie-break that dominated ranking
    for low-familiarity agents; that bug must not be reproduced.

    Returns a list of (post_dict, source_label) tuples of length
    min(K, len(candidates)) with no duplicate posts; source_label is one
    of "follow", "fit", "trending", "spill" (three_source mode) or
    "random".  Starved sources spill their unfilled slots to the next
    source in that same order; a final score-ordered catch-all (label
    "spill") exists as a safety net.
    """
    cfg = cfg or {}
    slots = dict(cfg.get("slots") or {"follow": 2, "fit": 2, "trending": 2})
    k_follow = int(slots.get("follow", 2))
    k_fit = int(slots.get("fit", 2))
    k_trend = int(slots.get("trending", 2))
    K = k_follow + k_fit + k_trend

    candidates = candidates or []

    if mode == "random":
        # Control arm: uniform slots, no recommender signal at all.  The
        # rng is consumed only by rng.sample, so two identically-seeded
        # runs produce identical feeds and no per-candidate eps draws are
        # wasted (the stream stays comparable across modes if reused).
        return [(post, "random")
                for post in rng.sample(list(candidates), min(K, len(candidates)))]
    if mode != "three_source":
        raise ValueError(f"rank_feed: unknown mode {mode!r}")

    heat_prev = heat_prev or {}
    clim_prev = clim_prev or {}

    follow_orgs = set(_state_get(agent_state, "follow", None) or set())
    trust = _state_get(agent_state, "trust", None) or {}
    # Restored from the v1.1 revision (dropped by the v1.3 rewrite): weights
    # are read once per call so eps stays a SMALL, explicit tie-break.
    w_trust = _f(cfg.get("w_trust", 1.0), 1.0)
    w_fit = _f(cfg.get("w_fit", 1.0), 1.0)
    w_heat = _f(cfg.get("w_heat", 1.0), 1.0)
    w_soc = _f(cfg.get("w_soc", 0.5), 0.5)
    eps = _f(cfg.get("eps", 0.05), 0.05)

    # Score every candidate exactly once, in list order, so the rng stream
    # is consumed identically for identical inputs (reproducibility).
    recs = []
    for post in candidates:
        pid = post.get("post_id")
        # Posts without a post_id fall back to object identity so they are
        # not wrongly collapsed into one another by dedup.
        key = pid if pid is not None else ("__obj__", id(post))
        heat = _f(heat_prev.get(pid, 0.0))
        f = fit(agent_state, post)
        score = (
            w_trust * math.tanh(_f(trust.get(post.get("org"), 0.0)))
            + w_fit * f
            + w_heat * heat
            + w_soc * climate_bonus(clim_prev.get(pid, "no_signal"))
            + eps * rng.random()
        )
        recs.append({
            "post": post,
            "key": key,
            "sortkey": str(pid) if pid is not None else "",
            "org": post.get("org"),
            "fit": f,
            "heat": heat,
            "score": score,
        })

    used = set()
    chosen = []

    def _take(quota, pool, keyfn, label):
        avail = [r for r in pool if r["key"] not in used]
        avail.sort(key=keyfn)
        taken = []
        for r in avail:
            if len(taken) >= quota:
                break
            used.add(r["key"])
            taken.append((r["post"], label))
        return taken

    # Stage 1: followed orgs, ordered by score DESC.
    follow_pool = [r for r in recs if r["org"] in follow_orgs]
    got = _take(k_follow, follow_pool, lambda r: (-r["score"], r["sortkey"]), "follow")
    chosen += got

    # Stage 2: fit source absorbs any follow shortfall (spillover rule).
    k_fit_eff = k_fit + max(0, k_follow - len(got))
    got = _take(k_fit_eff, recs, lambda r: (-r["fit"], -r["score"], r["sortkey"]), "fit")
    chosen += got

    # Stage 3: trending absorbs any fit shortfall.  Among candidates not
    # already placed by follow/fit, take the highest heat_prev; ties are
    # broken by score DESC, then post_id ASC (frozen deterministic order).
    k_trend_eff = k_trend + max(0, k_fit_eff - len(got))
    got = _take(k_trend_eff, recs, lambda r: (-r["heat"], -r["score"], r["sortkey"]), "trending")
    chosen += got

    # Final catch-all, only reachable when a stage was starved and
    # candidates still remain; labelled "spill".
    if len(chosen) < K:
        chosen += _take(K - len(chosen), recs, lambda r: (-r["score"], r["sortkey"]), "spill")

    return chosen


def self_test():
    """Deterministic self-check on synthetic inputs; returns failure count."""
    results = []

    def record(name, ok, detail=""):
        results.append((name, bool(ok), detail))

    # --- hot_score -------------------------------------------------------
    hs = [hot_score(120, 40, 12, a) for a in range(20)]
    record("hot_score: monotone decay in age_days",
           all(hs[i] > hs[i + 1] for i in range(len(hs) - 1)))
    base = hot_score(10, 10, 10, 3)
    record("hot_score: increases in likes/saves/comments",
           hot_score(11, 10, 10, 3) > base
           and hot_score(10, 11, 10, 3) > base
           and hot_score(10, 10, 11, 3) > base)
    record("hot_score: None/negative inputs treated as 0",
           hot_score(None, None, None, None) == 0.0
           and hot_score(-3, -4, -5, -2) == hot_score(0, 0, 0, 0))

    # --- climate_for -----------------------------------------------------
    def _cmts(stances, pid="p1"):
        return [{"post_id": pid, "agent_id": "inv_%05d" % i, "stance": s,
                 "text": "t", "fam_level": 0} for i, s in enumerate(stances)]

    lab, cnt = climate_for("p1", _cmts(["bullish", "bullish", "bullish"]))
    record("climate_for: no_signal below min_n",
           lab == "no_signal" and cnt == {"bullish": 3, "bearish": 0, "watching": 0},
           f"lab={lab} cnt={cnt}")
    lab_b, _ = climate_for("p1", _cmts(["bullish", "bullish", "bullish", "bearish"]))
    lab_m, _ = climate_for("p1", _cmts(["bullish", "bullish", "bearish", "bearish"]))
    lab_x, _ = climate_for("p1", _cmts(["bullish", "bearish", "bearish", "bearish"]))
    lab_w, _ = climate_for("p1", _cmts(["bullish", "bearish", "watching", "watching"]))
    record("climate_for: labels at d=+0.5/0/-0.5",
           lab_b == "bullish_majority" and lab_m == "mixed"
           and lab_x == "bearish_majority" and lab_w == "mixed",
           f"b={lab_b} m={lab_m} x={lab_x} w={lab_w}")
    lab_iso, _ = climate_for("p1", _cmts(["bullish"] * 4, pid="p1")
                             + _cmts(["bearish"] * 4, pid="p2"))
    record("climate_for: ignores other posts' comments", lab_iso == "bullish_majority")

    # Weighted path: all-1.0 weights must equal the unweighted result
    # exactly (dict equality holds because 3.0 == 3 in Python).
    cwb = _cmts(["bullish", "bullish", "bullish", "bearish"])  # inv_00003 bearish
    lab_u2, cnt_u2 = climate_for("p1", cwb)
    lab_w2, cnt_w2 = climate_for("p1", cwb, weights={
        "inv_00000": 1.0, "inv_00001": 1.0, "inv_00002": 1.0, "inv_00003": 1.0})
    record("climate_for: weights all 1.0 equal unweighted exactly",
           lab_w2 == lab_u2 and cnt_w2 == cnt_u2,
           f"lab={lab_w2} cnt={cnt_w2}")
    # Boundary flip: 3 bullish vs 1 bearish is d=+0.5 (bullish_majority),
    # but the bearish commenter at weight 3.0 ties the weighted counts
    # (3.0 vs 3.0, d=0) and the label must flip to "mixed".
    lab_w3, cnt_w3 = climate_for("p1", cwb, weights={
        "inv_00000": 1.0, "inv_00001": 1.0, "inv_00002": 1.0, "inv_00003": 3.0})
    record("climate_for: one commenter at weight 3.0 flips the label",
           lab_u2 == "bullish_majority" and lab_w3 == "mixed"
           and cnt_w3 == {"bullish": 3.0, "bearish": 3.0, "watching": 0.0},
           f"{lab_u2} -> {lab_w3} cnt={cnt_w3}")
    # The min_n floor compares against the WEIGHTED total: 3 raw comments
    # stay no_signal, but the same thread with one weight-3.0 commenter
    # totals 5.0 and is labelled.
    cwf = _cmts(["bullish", "bullish", "bearish"])
    lab_f0, _ = climate_for("p1", cwf)
    lab_f1, _ = climate_for("p1", cwf, weights={"inv_00002": 3.0})
    record("climate_for: min_n floor compares the weighted total",
           lab_f0 == "no_signal" and lab_f1 == "mixed",
           f"{lab_f0} -> {lab_f1}")

    # --- top_comments ----------------------------------------------------
    tc = [
        {"post_id": "p1", "agent_id": "inv_00007", "stance": "bullish", "text": "t", "fam_level": 0},
        {"post_id": "p1", "agent_id": "inv_00003", "stance": "bearish", "text": "t", "fam_level": 2},
        {"post_id": "p1", "agent_id": "inv_00009", "stance": "watching", "text": "t", "fam_level": 1},
        {"post_id": "p1", "agent_id": "inv_00001", "stance": "bullish", "text": "t", "fam_level": 2},
        {"post_id": "p2", "agent_id": "inv_00000", "stance": "bearish", "text": "t", "fam_level": 2},
    ]
    ids3 = [c["agent_id"] for c in top_comments("p1", tc, k=3)]
    record("top_comments: fam_level DESC then agent_id ASC",
           ids3 == ["inv_00001", "inv_00003", "inv_00009"], f"ids={ids3}")
    record("top_comments: capped at k and post-scoped",
           len(top_comments("p1", tc, k=2)) == 2 and top_comments("p9", tc, k=3) == [])
    ids_w = [c["agent_id"] for c in top_comments("p1", tc, k=3,
                                                 weights={"inv_00001": 2.0})]
    record("top_comments: weights kwarg reserved, ranking unchanged",
           ids_w == ids3, f"ids={ids_w}")

    # --- assign_arms (legacy two-arm path) --------------------------------
    rng_a = _make_rng("feed_selftest", "arms")
    tally_a = {"TV": 0, "T": 0}
    arms_a = []
    for _ in range(1000):
        arms_a.extend(assign_arms(rng_a, 1, tally_a))
    n = tally_a["TV"] + tally_a["T"]
    share = tally_a["TV"] / n
    record("assign_arms: |TV share - 0.5| <= 0.02 over 1000 draws",
           n == 1000 and abs(share - 0.5) <= 0.02, f"share={share:.4f}")

    rng_b = _make_rng("feed_selftest", "arms")
    tally_b = {"TV": 0, "T": 0}
    arms_b = []
    for _ in range(1000):
        arms_b.extend(assign_arms(rng_b, 1, tally_b))
    record("assign_arms: reproducible for a fixed seed", arms_a == arms_b)

    rng_c = _make_rng("feed_selftest", "arms_block")
    tally_c = {"TV": 0, "T": 0}
    max_imb = 0
    for _ in range(997):
        assign_arms(rng_c, 1, tally_c)
        max_imb = max(max_imb, abs(tally_c["TV"] - tally_c["T"]))
    record("assign_arms: running imbalance stays <= 1",
           max_imb <= 1, f"max_imb={max_imb}")

    # --- assign_arms (three-arm path, PREREG v1.5 A) ----------------------
    three = ("T", "TC", "TV")
    rng_3a = _make_rng("feed_selftest", "arms3_a")
    tally_3a = {}
    draws3 = []
    for _ in range(3000):
        draws3.extend(assign_arms(rng_3a, 1, tally_3a, arms=three))
    rng_3b = _make_rng("feed_selftest", "arms3_a")
    tally_3b = {}
    draws3b = []
    for _ in range(3000):
        draws3b.extend(assign_arms(rng_3b, 1, tally_3b, arms=three))
    record("assign_arms: three-arm reproducible, in-set, tally-consistent",
           draws3 == draws3b and set(draws3) == set(three)
           and all(tally_3a[a] == draws3.count(a) for a in three))
    shares3 = {a: tally_3a[a] / 3000 for a in three}
    shares3_str = " ".join(f"{a}={shares3[a]:.4f}" for a in sorted(shares3))
    record("assign_arms: three-arm shares within 0.05 of 1/3 over 3000 draws",
           all(abs(shares3[a] - 1.0 / 3.0) <= 0.05 for a in three), shares3_str)
    t3c = {}
    batch3 = assign_arms(_make_rng("feed_selftest", "arms3_batch"), 5, t3c, arms=three)
    record("assign_arms: three-arm batch returns k in-set arms, tally updated",
           len(batch3) == 5 and all(a in three for a in batch3)
           and sum(t3c.values()) == 5)
    try:
        assign_arms(_make_rng("feed_selftest", "arms_bad"), 1, {}, arms=())
        empty_arms_raised = False
    except ValueError:
        empty_arms_raised = True
    record("assign_arms: empty arms raises ValueError", empty_arms_raised)

    # --- arm_for_agent (unbalanced cohort-less coin, kept for callers) ----
    a1 = [arm_for_agent("rt_fixed", "inv_%05d" % i) for i in range(10)]
    a2 = [arm_for_agent("rt_fixed", "inv_%05d" % i) for i in range(10)]
    vals = sorted({arm_for_agent("rt_fixed", "inv_%05d" % i) for i in range(40)})
    record("arm_for_agent: deterministic per (run_tag, agent_id)",
           a1 == a2 and vals == ["T", "TV"], f"vals={vals}")
    n_diff = sum(1 for i in range(50)
                 if arm_for_agent("rt_alpha", "inv_%05d" % i)
                 != arm_for_agent("rt_beta", "inv_%05d" % i))
    record("arm_for_agent: differs across run_tag", n_diff > 0, f"n_diff={n_diff}/50")

    # PREREG v1.5 A: the default arms path must equal the frozen v1.3 coin
    # bit-for-bit (old-config replay identity), for the implicit default and
    # for both orderings of the explicit two-arm set.
    legacy_eq = True
    for ltag in ("tag", "rt_fixed", "rt_alpha"):
        for i in range(20):
            aid = "inv_%05d" % i
            want = _arm_for_agent_legacy(ltag, aid)
            got = (arm_for_agent(ltag, aid),
                   arm_for_agent(ltag, aid, arms=["T", "TV"]),
                   arm_for_agent(ltag, aid, arms=("TV", "T")))
            if got != (want, want, want):
                legacy_eq = False
    record("arm_for_agent: default two arms equal the legacy coin (3 tags x 20 ids)",
           legacy_eq)

    det3 = ([arm_for_agent("rt3", "inv_%05d" % i, arms=three) for i in range(30)]
            == [arm_for_agent("rt3", "inv_%05d" % i, arms=three) for i in range(30)])
    diff3 = sum(1 for i in range(50)
                if arm_for_agent("rt3a", "inv_%05d" % i, arms=three)
                != arm_for_agent("rt3b", "inv_%05d" % i, arms=three))
    record("arm_for_agent: three-arm deterministic and tag-sensitive",
           det3 and diff3 > 0, f"n_diff={diff3}/50")
    # The three-arm draw is the documented int(rng.random()*3) index on the
    # same run_tag|arm|agent_id stream (frozen-rule regression guard).
    idx_ok = True
    for i in (0, 1, 7, 19, 42):
        aid = "inv_%05d" % i
        r = _make_rng("rt3", "arm|" + aid)
        if arm_for_agent("rt3", aid, arms=three) != three[min(int(r.random() * 3), 2)]:
            idx_ok = False
    record("arm_for_agent: three-arm draw is int(rng.random()*3) on the frozen stream",
           idx_ok)

    # --- assign_agent_arms (stratified block randomisation) ----------------
    # Synthetic cohort mirroring the frozen population's shape: 36 cells
    # (2 of size 2, 12 of size 11, 22 of size 12 -> 400 agents), so the
    # stratification guarantees are exercised on the real size mix.  The
    # REAL cohort is covered by tests/unit/test_arm_balance.py (feed.py
    # itself does no file I/O).
    coh = []
    _idx = 0
    for _ci, _sz in enumerate([2, 2] + [11] * 12 + [12] * 22):
        _cell = "cell_%02d" % _ci
        for _ in range(_sz):
            coh.append(("inv_%05d" % _idx, _cell))
            _idx += 1
    coh_cells = dict(coh)
    coh_by_cell = {}
    for _a, _c in coh:
        coh_by_cell.setdefault(_c, []).append(_a)
    two = ("T", "TV")
    tags = ["sat_%02d" % r for r in range(24)]

    m_0 = assign_agent_arms("sat_00", coh, two)
    m_0r = assign_agent_arms("sat_00", list(reversed(coh)), two)
    record("assign_agent_arms: deterministic, input-order insensitive",
           m_0 == assign_agent_arms("sat_00", coh, two) == m_0r
           and set(m_0) == set(coh_cells) and set(m_0.values()) == {"T", "TV"},
           f"n={len(m_0)}")
    try:
        assign_agent_arms("sat_00", coh, ())
        aa_raise = False
    except ValueError:
        aa_raise = True
    record("assign_agent_arms: empty arms raise, empty cohort -> {}",
           aa_raise and assign_agent_arms("sat_00", [], two) == {})

    ok2 = 0
    worst2 = 0.0
    floor_ceil2 = True
    single2 = 0
    split2 = 0
    maps2 = set()
    for tag in tags:
        amap = assign_agent_arms(tag, coh, two)
        maps2.add(tuple(sorted(amap.items())))
        ok, rep = check_arm_balance(amap, coh_cells)
        ok2 += 1 if ok else 0
        worst2 = max(worst2, rep["worst_overall_dev"])
        extras = set()
        for cell in sorted(coh_by_cell):
            members = coh_by_cell[cell]
            n_c = len(members)
            n_tv = sum(1 for a in members if amap[a] == "TV")
            if not (n_tv in (n_c // 2, n_c - n_c // 2)):
                floor_ceil2 = False
            if n_c >= 2 and n_tv in (0, n_c):
                single2 += 1
            if n_c % 2 == 1:
                extras.add("TV" if n_tv > n_c - n_tv else "T")
        if extras == {"T", "TV"}:
            split2 += 1
    record("assign_agent_arms: 2-arm invariant ok 24/24 tags, worst dev <= 0.01",
           ok2 == len(tags) and worst2 <= 0.01,
           f"ok={ok2}/{len(tags)} worst_overall_dev={worst2:.4f}")
    record("assign_agent_arms: 2-arm cells floor/ceil, none single-armed",
           floor_ceil2 and single2 == 0, f"single_armed_cells={single2}")
    record("assign_agent_arms: 2-arm odd-cell remainders split across arms",
           split2 == len(tags), f"tags_with_split={split2}/{len(tags)}")
    record("assign_agent_arms: 2-arm maps distinct across run tags",
           len(maps2) == len(tags), f"distinct={len(maps2)}/{len(tags)}")
    m_1 = assign_agent_arms("sat_01", coh, two)
    d01 = sum(1 for a in coh_cells if m_0[a] != m_1[a])
    record("assign_agent_arms: assignment changes with run_tag",
           d01 > 50, f"diff_agents={d01}/400")
    tv0 = sum(1 for v in m_0.values() if v == "TV")
    record("assign_agent_arms: 2-arm overall within one agent of exact",
           abs(tv0 - (400 - tv0)) <= 1, f"TV={tv0} T={400 - tv0}")

    ok3 = 0
    worst3 = 0.0
    floor_ceil3 = True
    maps3 = set()
    for tag in tags:
        amap = assign_agent_arms(tag, coh, three)
        maps3.add(tuple(sorted(amap.items())))
        ok, rep = check_arm_balance(amap, coh_cells, arms=three)
        ok3 += 1 if ok else 0
        worst3 = max(worst3, rep["worst_overall_dev"])
        for cell in sorted(coh_by_cell):
            members = coh_by_cell[cell]
            n_c = len(members)
            cnts = [sum(1 for a in members if amap[a] == arm) for arm in three]
            if not all(n_c // 3 <= c <= n_c // 3 + 1 for c in cnts):
                floor_ceil3 = False
    record("assign_agent_arms: 3-arm invariant ok 24/24 tags, worst dev <= 0.01",
           ok3 == len(tags) and worst3 <= 0.01,
           f"ok={ok3}/{len(tags)} worst_overall_dev={worst3:.4f}")
    record("assign_agent_arms: 3-arm cells floor/ceil-balanced", floor_ceil3)
    record("assign_agent_arms: 3-arm maps distinct across run tags",
           len(maps3) == len(tags), f"distinct={len(maps3)}/{len(tags)}")
    m3_0 = assign_agent_arms("sat_00", coh, three)
    c3 = {arm: sum(1 for v in m3_0.values() if v == arm) for arm in three}
    record("assign_agent_arms: 3-arm overall within one agent of exact",
           max(c3.values()) - min(c3.values()) <= 1,
           " ".join(f"{a}={c3[a]}" for a in sorted(c3)))

    # --- check_arm_balance (achievable tolerances + numeric report) -------
    coh_ids_sorted = sorted(coh_cells)
    skew2 = {a: ("TV" if i < 300 else "T") for i, a in enumerate(coh_ids_sorted)}
    okk, repk = check_arm_balance(skew2, coh_cells)
    record("check_arm_balance: rejects a skewed 75/25 assignment",
           not okk and abs(repk["tv_share"] - 0.75) < 1e-12
           and repk["worst_overall_arm"] == "TV",
           f"tv_share={repk['tv_share']:.3f}")

    # An exactly-50/50 map that ignores stratification: every cell of the
    # spread mapping holds ids of one parity only, so all 36 cells come out
    # single-armed.  The old distribution-vs-overall check missed the fine
    # print; the per-cell tolerance now fails it with named cells.
    spread_cells = {a: "cell_%02d" % (i % 36) for i, a in enumerate(coh_ids_sorted)}
    alt = {a: ("TV" if i % 2 == 0 else "T") for i, a in enumerate(coh_ids_sorted)}
    okl, repl = check_arm_balance(alt, spread_cells)
    record("check_arm_balance: per-cell check fails an unstratified 50/50 map",
           not okl and repl["ok_overall"] and repl["tv_share"] == 0.5
           and len(repl["single_armed_cells"]) == 36,
           f"single_armed={len(repl['single_armed_cells'])}")

    sab = dict(m_0)
    for a in coh_cells:
        if coh_cells[a] == "cell_02":
            sab[a] = "TV"
        elif coh_cells[a] == "cell_03":
            sab[a] = "T"
    oks, reps = check_arm_balance(sab, coh_cells)
    record("check_arm_balance: flags deliberately single-armed cells",
           not oks and not reps["ok_cells"]
           and reps["single_armed_cells"] == ["cell_02", "cell_03"]
           and reps["worst_cell_dev"] >= 0.5 - 1e-9,
           f"cells={reps['single_armed_cells']} worst_cell_dev={reps['worst_cell_dev']:.3f}")

    okm, repm = check_arm_balance(alt, spread_cells, arms=("T", "TC", "TV"))
    record("check_arm_balance: explicit 3 arms fails when TC got no agents",
           not okm and repm.get("tc_share") == 0.0 and repm.get("n_out_of_set") == 0,
           f"tc_share={repm.get('tc_share')}")

    skew3 = {a: ("T" if i < 180 else ("TC" if i < 320 else "TV"))
             for i, a in enumerate(coh_ids_sorted)}
    oks3, reps3 = check_arm_balance(skew3, coh_cells, arms=three)
    record("check_arm_balance: rejects a skewed 45/35/20 three-arm assignment",
           not oks3 and abs(reps3["t_share"] - 0.45) < 1e-12
           and abs(reps3["tv_share"] - 0.20) < 1e-12,
           f"T={reps3['t_share']:.2f} TC={reps3['tc_share']:.2f} TV={reps3['tv_share']:.2f}")

    okp, repp = check_arm_balance(m_0, coh_cells)
    record("check_arm_balance: passing report carries worst-case detail",
           okp and isinstance(repp.get("worst_overall_dev"), float)
           and isinstance(repp.get("worst_cell_dev"), float)
           and repp.get("worst_cell") in set(coh_cells.values())
           and repp.get("n_cells") == 36
           and repp.get("single_armed_cells") == []
           and repp.get("ok_overall") and repp.get("ok_cells"),
           f"worst_cell={repp.get('worst_cell')} dev={repp.get('worst_cell_dev'):.4f}")

    # The exposure-level greedy balancing (assign_arms) still satisfies the
    # OVERALL clause by construction; its per-cell balance is by design not
    # required at exposure level (invariant (h) is agent-level).
    ok_runs = 0
    for r in range(20):
        rtag = "armbal_%03d" % r
        arms_bal = dict(zip(coh_ids_sorted,
                            assign_arms(_make_rng(rtag, "balance"), 400,
                                        {"TV": 0, "T": 0})))
        okb, repb = check_arm_balance(arms_bal, coh_cells)
        if repb["ok_overall"] and repb["worst_overall_dev"] <= 0.01:
            ok_runs += 1
    record("check_arm_balance: exposure-balanced stream meets overall tol 20/20",
           ok_runs == 20, f"ok_overall_runs={ok_runs}/20")

    # --- fit / climate_bonus ---------------------------------------------
    record("fit: bounded [0,1] heuristic values",
           fit({"risk_latent": "tolerant", "core": "chaser"}, {"intent_group": "I2"}) == 0.9
           and fit({"risk_latent": "fragile", "core": "chaser"}, {"intent_group": "I2"}) == 0.4
           and fit({"risk_latent": "fragile", "core": "allocator"}, {"intent_group": "nonI2"}) == 0.65)
    record("climate_bonus: +1/0/-1/0 mapping",
           climate_bonus("bullish_majority") == 1.0
           and climate_bonus("mixed") == 0.0
           and climate_bonus("bearish_majority") == -1.0
           and climate_bonus("no_signal") == 0.0)

    # --- rank_feed -------------------------------------------------------
    class _Agent(object):
        def __init__(self, follow):
            self.follow = set(follow)
            self.trust = {"orgA": 0.6, "orgB": -0.2, "orgC": 0.1, "orgD": 0.0}
            self.fam_level = {"orgA": 2, "orgB": 0, "orgC": 1, "orgD": 0}
            self.risk_latent = "tolerant"
            self.core = "chaser"

    def _post(pid, org, ig):
        return {"post_id": pid, "org": org, "intent_group": ig,
                "note": {"note_id": pid}, "age_days": 1,
                "likes": 0, "saves": 0, "comments": 0}

    spec = [
        ("pA1", "orgA", "I2", 0.30), ("pA2", "orgA", "nonI2", 0.10), ("pA3", "orgA", "I2", 0.20),
        ("pB1", "orgB", "I2", 0.90), ("pB2", "orgB", "nonI2", 0.40), ("pB3", "orgB", "I2", 0.15),
        ("pC1", "orgC", "I2", 0.70), ("pC2", "orgC", "nonI2", 0.25), ("pC3", "orgC", "I2", 0.05),
        ("pD1", "orgD", "I2", 0.60), ("pD2", "orgD", "nonI2", 0.35), ("pD3", "orgD", "I2", 0.50),
    ]
    cands = [_post(p, o, g) for (p, o, g, _h) in spec]
    heat_prev = {p: h for (p, _o, _g, h) in spec}
    clim_prev = {"pB1": "bullish_majority", "pC1": "bearish_majority", "pD1": "mixed"}
    cfg = {"slots": {"follow": 2, "fit": 2, "trending": 2},
           "w_trust": 1.0, "w_fit": 1.0, "w_heat": 1.0, "w_soc": 0.5, "eps": 0.05}
    ag = _Agent({"orgA"})

    rng1 = _make_rng("feed_selftest", "rank_a")
    res1 = rank_feed(ag, cands, heat_prev, clim_prev, cfg, rng1)
    pids1 = [p["post_id"] for p, _ in res1]
    labs1 = [l for _, l in res1]
    record("rank_feed: returns exactly K=6 distinct posts",
           len(res1) == 6 and len(set(pids1)) == 6, f"n={len(res1)} pids={pids1}")
    record("rank_feed: honours 2/2/2 slot quotas when supply allows",
           labs1.count("follow") == 2 and labs1.count("fit") == 2
           and labs1.count("trending") == 2, f"labs={labs1}")
    record("rank_feed: follow slots are followed-org posts, listed first",
           labs1[:2] == ["follow", "follow"]
           and all(p["org"] == "orgA" for p, _l in res1[:2]), f"labs={labs1}")
    # "Remaining" means: not already placed by the FOLLOW/FIT stages.  The
    # old version subtracted the trending picks themselves too, making the
    # expected set circular and the check unpassable (got=pC1/pD3, which
    # ARE the top-heat leftovers).  Heats in `spec` are unique, so heat
    # order alone pins the expected picks here.
    placed_12 = {p["post_id"] for p, l in res1 if l in ("follow", "fit")}
    n_trend1 = labs1.count("trending")
    trend_expected = sorted(
        (p for p in cands if p["post_id"] not in placed_12),
        key=lambda p: -heat_prev[p["post_id"]])[:n_trend1]
    got_trend = sorted(p["post_id"] for p, l in res1 if l == "trending")
    record("rank_feed: trending slots are top heat among remaining",
           got_trend == sorted(p["post_id"] for p in trend_expected), f"got={got_trend}")

    ag_z = _Agent({"orgZ"})  # follows an org with zero posts -> starved source
    rng2 = _make_rng("feed_selftest", "rank_b")
    res2 = rank_feed(ag_z, cands, heat_prev, clim_prev, cfg, rng2)
    labs2 = [l for _, l in res2]
    record("rank_feed: starved follow spills into fit (0/4/2 labels)",
           len(res2) == 6 and labs2.count("follow") == 0 and labs2.count("fit") == 4
           and labs2.count("trending") == 2, f"labs={labs2}")

    rng4 = _make_rng("feed_selftest", "rank_c")
    res4 = rank_feed(ag, cands[:4], heat_prev, clim_prev, cfg, rng4)
    record("rank_feed: short supply returns all distinct candidates",
           len(res4) == 4 and len({p["post_id"] for p, _ in res4}) == 4)

    rng3 = _make_rng("feed_selftest", "rank_a")
    res3 = rank_feed(ag, cands, heat_prev, clim_prev, cfg, rng3)
    record("rank_feed: identical output for identically-seeded RNGs",
           [p["post_id"] for p, _ in res3] == pids1
           and [l for _, l in res3] == labs1)

    pair = [_post("pWin", "orgA", "I2"), _post("pLose", "orgA", "I2")]
    pair_heat = {"pWin": 1.0, "pLose": 0.0}  # exactly 1.0 score edge (w_heat=1.0)
    wins = 0
    for t in range(200):
        r = _make_rng("feed_selftest", "eps_%03d" % t)
        out = rank_feed(ag, pair, pair_heat, {}, cfg, r)
        if out and out[0][0]["post_id"] == "pWin":
            wins += 1
    record("rank_feed: 1.0 score edge survives eps over 200 trials",
           wins == 200, f"wins={wins}/200")

    # Trending tie-breaks: equal heat -> higher score wins; with eps=0 a
    # full tie falls through to post_id ASC.
    cfg_tie = {"slots": {"follow": 0, "fit": 0, "trending": 1},
               "w_trust": 1.0, "w_fit": 1.0, "w_heat": 1.0, "w_soc": 0.5, "eps": 0.05}
    res_t = rank_feed(ag, [_post("tB", "orgB", "I2"), _post("tA", "orgA", "I2")],
                      {"tA": 1.0, "tB": 1.0}, {}, cfg_tie,
                      _make_rng("feed_selftest", "rank_tie"))
    record("rank_feed: heat tie broken by score DESC",
           [p["post_id"] for p, _l in res_t] == ["tA"],
           f"got={[p['post_id'] for p, _l in res_t]}")
    cfg_flat = {"slots": {"follow": 0, "fit": 0, "trending": 1}, "eps": 0.0}
    res_z = rank_feed(ag, [_post("zB", "orgD", "I2"), _post("zA", "orgD", "I2")],
                      {"zA": 1.0, "zB": 1.0}, {}, cfg_flat,
                      _make_rng("feed_selftest", "rank_flat"))
    record("rank_feed: full tie broken by post_id ASC",
           [p["post_id"] for p, _l in res_z] == ["zA"],
           f"got={[p['post_id'] for p, _l in res_z]}")

    # --- rank_feed(mode="random") ----------------------------------------
    rnd_posts = [_post("r%02d" % i, "orgR", "I2") for i in range(60)]
    out_r1 = rank_feed(ag, rnd_posts, {}, {}, cfg,
                       _make_rng("feed_selftest", "rank_rand"), mode="random")
    pids_r1 = [p["post_id"] for p, _l in out_r1]
    labs_r1 = [l for _, l in out_r1]
    record("rank_feed(random): K=6 distinct posts, all labelled random",
           len(out_r1) == 6 and len(set(pids_r1)) == 6 and set(labs_r1) == {"random"},
           f"n={len(out_r1)} labels={sorted(set(labs_r1))}")
    out_r2 = rank_feed(ag, rnd_posts, {}, {}, cfg,
                       _make_rng("feed_selftest", "rank_rand"), mode="random")
    record("rank_feed(random): identical for identically-seeded RNGs",
           [(p["post_id"], l) for p, l in out_r1]
           == [(p["post_id"], l) for p, l in out_r2])
    rng_freq = _make_rng("feed_selftest", "rank_freq")
    inc = {p["post_id"]: 0 for p in rnd_posts}
    n_draws = 2000
    for _ in range(n_draws):
        for p, _l in rank_feed(ag, rnd_posts, {}, {}, cfg, rng_freq, mode="random"):
            inc[p["post_id"]] += 1
    # 60 candidates, K=6 -> uniform inclusion 0.10 per draw; the sd of a
    # share is sqrt(0.1*0.9/2000) ~ 0.0067, so 3pp is a ~4.5-sigma bound.
    max_dev = max(abs(v / n_draws - 6.0 / 60.0) for v in inc.values())
    record("rank_feed(random): freqs within 3pp of uniform over 2000 draws",
           max_dev <= 0.03, f"max_dev={max_dev:.4f} uniform=0.100")
    try:
        rank_feed(ag, rnd_posts, {}, {}, cfg, rng_freq, mode="bogus")
        bad_mode_raised = False
    except ValueError:
        bad_mode_raised = True
    record("rank_feed: invalid mode raises ValueError", bad_mode_raised)

    # --- report ----------------------------------------------------------
    width = 58
    print("=" * (width + 12))
    print("feed.py self_test")
    print("=" * (width + 12))
    print(f"{'CHECK':<{width}} RESULT DETAIL")
    print("-" * (width + 12))
    failures = 0
    for name, ok, detail in results:
        if not ok:
            failures += 1
        print(f"{name:<{width}} {'PASS' if ok else 'FAIL'} {detail}")
    print("-" * (width + 12))
    print(f"checks={len(results)} failures={failures}")
    return failures


def main():
    # Direct execution can only mean the self-test; engine_v6 imports the
    # functions instead of running anything.
    return self_test()


if __name__ == "__main__":
    raise SystemExit(main())
