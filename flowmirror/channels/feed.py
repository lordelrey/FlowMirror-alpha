"""Pure platform-layer functions for the fund-marketing live-LLM simulation.

engine_v6.py imports this module for the recommender mechanics: the
three-source (follow / fit / trending) slot filler, the explicit hot-score
decay, image-arm randomization, and the lagged comment climate.  Everything
here is a pure function of its arguments: no file I/O, no printing outside
self_test(), no global mutable state, and no sqlite3 (xhs_data.db is
off-limits to sim code).

Image-arm contract (PREREG v1.3 B10): the DEFAULT is the per-AGENT arm,
arm_for_agent(), fixed for the whole run and derived from
sha256(f"{run_tag}|arm|{agent_id}") so it is independent of every other
RNG stream.  assign_arms() (per-exposure, block-balanced) survives only
as the arm_level == "exposure" sensitivity option.  check_arm_balance()
verifies invariant (h): |TV share - 0.5| <= 0.03 over the agent set and,
within each arm, a 36-cell distribution within 0.05 max-abs-diff of the
overall cell distribution.

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
without sorted().

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


def assign_arms(rng, k, tally):
    """Per-exposure image-arm randomization with block balancing.

    Used only when config arm_level == "exposure" (the sensitivity option);
    the default arm level is the agent-level arm_for_agent().

    Greedy balancing on the agent's lifetime tally keeps |TV - T| <= 1 at
    all times, which is what guarantees engine invariant (h): each agent's
    lifetime TV share stays within +/-0.02 of 0.5 while individual
    exposures remain randomized.  tally is mutated in place on purpose;
    it belongs to the caller (the engine's per-agent state).
    """
    arms = []
    for _ in range(max(0, int(k))):
        if tally["TV"] - tally["T"] >= 1:
            arm = "T"
        elif tally["T"] - tally["TV"] >= 1:
            arm = "TV"
        else:
            arm = "TV" if rng.random() < 0.5 else "T"
        tally[arm] = tally.get(arm, 0) + 1
        arms.append(arm)
    return arms


def arm_for_agent(run_tag, agent_id):
    """Agent-level TV/T assignment, fixed for the whole run (PREREG v1.3 B10).

    Derived from sha256(f"{run_tag}|arm|{agent_id}") so it is reproducible and
    independent of every other RNG stream.  The digest is fed through the
    same hexdigest[:16] -> int seeding rule as _make_rng, with agent_id
    folded into the string so no other stream can ever collide with it.
    """
    digest = hashlib.sha256(f"{run_tag}|arm|{agent_id}".encode()).hexdigest()
    return "TV" if random.Random(int(digest[:16], 16)).random() < 0.5 else "T"


def check_arm_balance(agent_arms, agent_cells):
    """Verify arm invariant (h) over the agent set; returns (ok, report).

    ok iff |TV share - 0.5| <= 0.03 over the agent set AND, for each arm,
    the arm's cell distribution stays within 0.05 (max absolute per-cell
    difference) of the overall cell distribution.  report carries the
    numbers so the engine can log them beside run_meta.  Iteration goes
    over sorted ids / sorted cells only (determinism contract).
    """
    ids = sorted(agent_arms)
    n = len(ids)
    if n == 0:
        # Degenerate population: report a maximal violation instead of
        # dividing by zero.
        return (False, {"n_agents": 0, "tv_share": 0.0,
                        "max_abs_diff_TV": 1.0, "max_abs_diff_T": 1.0})
    tv_n = sum(1 for a in ids if agent_arms[a] == "TV")
    tv_share = tv_n / n
    overall = {}
    for a in ids:
        cell = str(agent_cells.get(a, "?"))
        overall[cell] = overall.get(cell, 0) + 1
    cells = sorted(overall)
    report = {"n_agents": n, "n_tv": tv_n, "tv_share": tv_share}
    ok = abs(tv_share - 0.5) <= 0.03
    for arm in ("TV", "T"):
        members = [a for a in ids if agent_arms[a] == arm]
        if not members:
            # An empty arm has no distribution to compare; a maximal
            # deviation fails the invariant and keeps the report total.
            report["max_abs_diff_" + arm] = 1.0
            ok = False
            continue
        arm_counts = {c: 0 for c in cells}
        for a in members:
            arm_counts[str(agent_cells.get(a, "?"))] += 1
        mad = max(abs(arm_counts[c] / len(members) - overall[c] / n)
                  for c in cells)
        report["max_abs_diff_" + arm] = mad
        ok = ok and mad <= 0.05
    return (ok, report)


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

    # --- assign_arms -----------------------------------------------------
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

    # --- arm_for_agent / check_arm_balance -------------------------------
    a1 = [arm_for_agent("rt_fixed", "inv_%05d" % i) for i in range(10)]
    a2 = [arm_for_agent("rt_fixed", "inv_%05d" % i) for i in range(10)]
    vals = sorted({arm_for_agent("rt_fixed", "inv_%05d" % i) for i in range(40)})
    record("arm_for_agent: deterministic per (run_tag, agent_id)",
           a1 == a2 and vals == ["T", "TV"], f"vals={vals}")
    n_diff = sum(1 for i in range(50)
                 if arm_for_agent("rt_alpha", "inv_%05d" % i)
                 != arm_for_agent("rt_beta", "inv_%05d" % i))
    record("arm_for_agent: differs across run_tag", n_diff > 0, f"n_diff={n_diff}/50")

    syn_ids = ["inv_%05d" % i for i in range(400)]
    syn_cells = {a: "cell_%02d" % (i % 36) for i, a in enumerate(syn_ids)}
    # arm_for_agent() is a fair per-agent coin, so |TV share - 0.5| has
    # sd sqrt(0.25/400) = 0.025 and the 0.03 invariant bound is only a
    # 1.2-sigma event (~77/100 run_tags) for the RAW coin stream.  The
    # >=95/100 assertion below therefore runs on the block-balanced
    # stream (|TV - T| <= 1 by construction, the same per-run guarantee
    # assign_arms gives per agent); the raw-coin ok count is still
    # reported and floored at 60/100 so a genuinely BIASED coin (e.g. a
    # broken "< 0.4" threshold) cannot slip through unnoticed.
    ok_runs = 0
    coin_ok_runs = 0
    for r in range(100):
        rtag = "armbal_%03d" % r
        arms_bal = dict(zip(
            syn_ids, assign_arms(_make_rng(rtag, "balance"), 400, {"TV": 0, "T": 0})))
        if check_arm_balance(arms_bal, syn_cells)[0]:
            ok_runs += 1
        arms_coin = {a: arm_for_agent(rtag, a) for a in syn_ids}
        if check_arm_balance(arms_coin, syn_cells)[0]:
            coin_ok_runs += 1
    record("check_arm_balance: ok for >=95/100 run_tags (balanced stream)",
           ok_runs >= 95, f"ok_runs={ok_runs}/100 coin_ok_runs={coin_ok_runs}/100")
    record("check_arm_balance: raw coin arms within binomial noise",
           coin_ok_runs >= 60, f"coin_ok_runs={coin_ok_runs}/100")
    bad_arms = {a: ("TV" if i < 300 else "T") for i, a in enumerate(syn_ids)}
    bad_ok, bad_rep = check_arm_balance(bad_arms, syn_cells)
    record("check_arm_balance: rejects a skewed 75/25 assignment",
           not bad_ok and abs(bad_rep["tv_share"] - 0.75) < 1e-12,
           f"tv_share={bad_rep['tv_share']:.3f}")

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
