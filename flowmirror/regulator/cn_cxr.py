"""CN cross-border (CXR) regulator: engine_v5 semantics, unchanged (P2).

Behaviour contract, frozen from ``sim/engine_v5.py``:

* ``CNCXR.checkout(reported_c, fund_r, smc)`` reproduces
  ``engine_v5.cxr_outcome(reported_c, fund_r, smc)`` EXACTLY, including
  the branching order:

      rank(C) == 1 and rank(R) > 1   ->  "hard_block"
      rank(C) >= rank(R)             ->  "match"
      else, smc is True              ->  "confirm_signed"
      else                           ->  "confirm_declined"

  ``smc`` is the "sign mismatch confirm" flag from the agent decision:
  True means the agent signed the risk-mismatch confirmation.

* ONLY C1 x R>1 yields ``hard_block``.  The v7 cohort contains no C1
  clients, so ``hard_block`` is never observed in the experiment
  (PREREG v1.1 §B5); the branch is kept because the rule is frozen, not
  because it can fire.

* Redemptions are never gated: ``checkout`` answers purchase attempts
  only.  Regulating the sell side is the caller's responsibility.

* ``is_qdii_blocked`` is accepted (protocol-compatible call sites may
  pass it) and deliberately does NOT branch: the QDII quota gate is
  layered by the caller BEFORE checkout, so this method never returns
  "purchase_blocked" and its verdict equals engine_v5 for either value
  of the flag.  tests/unit/test_cn_cxr.py pins that independence over
  the full C1..C5 x R1..R5 x {0,1} x {0,1} table.

``CNCXR.classify_fund`` reproduces ``engine_v5.classify_fund`` exactly
and returns the ``(r, qdii)`` pair (R from the flow-panel ``type``
string; the QDII flag from the type or overseas keywords in the name).

The module-level ``cxr_outcome`` and ``classify_fund`` thin wrappers
keep old engine_v5 call sites working unchanged.
"""

__all__ = ["C_RANK", "R_RANK", "CNCXR", "cxr_outcome", "classify_fund"]

# Rank orders mirroring the module-level constants of sim/engine_v5.py;
# the rule compares ranks, never labels.
C_RANK = {"C1": 1, "C2": 2, "C3": 3, "C4": 4, "C5": 5}
R_RANK = {"R1": 1, "R2": 2, "R3": 3, "R4": 4, "R5": 5}


class CNCXR:
    """Stateless CN C x R regulator; satisfies flowmirror.regulator.base.Regulator."""

    def checkout(self, reported_c: str, fund_r: str, smc: bool,
                 is_qdii_blocked: bool = False) -> str:
        """engine_v5.cxr_outcome, verbatim branching (see module docstring)."""
        # is_qdii_blocked: intentionally not consulted (module docstring).
        cr, rr = C_RANK[reported_c], R_RANK[fund_r]
        if cr == 1 and rr > 1:
            return "hard_block"
        if cr >= rr:
            return "match"
        return "confirm_signed" if smc else "confirm_declined"

    # For an ACTIVE gate the observed outcome IS its counterfactual, so
    # counterfactual is a plain alias.  (NoGate overrides it; see none.py.)
    counterfactual = checkout

    def classify_fund(self, ftype: str, name: str):
        """R from flow_panel 'type'; QDII flag from type or overseas keywords in name."""
        if "债" in ftype:
            r = "R2"
        elif any(k in ftype for k in ("指数", "ETF", "行业")):
            r = "R4"
        else:
            r = "R3"
        qdii = ("QDII" in ftype) or any(k in name for k in ("海外", "纳斯达克", "日经", "标普"))
        return r, qdii


def cxr_outcome(reported_c: str, fund_r: str, smc: bool,
                is_qdii_blocked: bool = False) -> str:
    """Old-call-site wrapper: identical to engine_v5.cxr_outcome(reported_c, fund_r, smc)."""
    return CNCXR().checkout(reported_c, fund_r, smc, is_qdii_blocked)


def classify_fund(ftype: str, name: str):
    """Old-call-site wrapper: identical to engine_v5.classify_fund(ftype, name)."""
    return CNCXR().classify_fund(ftype, name)
