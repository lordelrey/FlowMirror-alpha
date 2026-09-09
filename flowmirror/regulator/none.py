"""No-op regulator for the suitability=false arm.

When the platform runs with suitability checking disabled, purchases are
never gated: ``NoGate.checkout`` answers ``"match"`` for every attempt.
``co.oc_cf`` must still record what the ACTIVE rule set would have
decided for the same attempt, so ``counterfactual`` delegates to
``CNCXR.checkout`` with identical arguments.  This is the documented
pairing of the two regulators; the counterfactual source is injectable
via ``__init__(cxr=...)`` for tests and future rule sets.
"""

from .cn_cxr import CNCXR

__all__ = ["NoGate"]


class NoGate:
    """Pass-through regulator; satisfies flowmirror.regulator.base.Regulator."""

    def __init__(self, cxr=None):
        # Extension hook: the counterfactual rule is injectable; the
        # default is the frozen CN C x R implementation.
        self._cxr = CNCXR() if cxr is None else cxr

    def checkout(self, reported_c: str, fund_r: str, smc: bool = False,
                 is_qdii_blocked: bool = False) -> str:
        """Always ``"match"``: the no-gate arm never blocks a purchase.

        Signature kept positional-compatible with ``CNCXR.checkout`` so
        call sites need not branch on the regulator kind; every argument
        is ignored.  Redemptions are likewise never gated.
        """
        return "match"

    def counterfactual(self, reported_c: str, fund_r: str, smc: bool = False,
                       is_qdii_blocked: bool = False) -> str:
        """What ``CNCXR.checkout`` would have returned for the same attempt."""
        return self._cxr.checkout(reported_c, fund_r, smc, is_qdii_blocked)

    def classify_fund(self, ftype: str, name: str):
        """Delegate to the CN rule so fund labels stay consistent across arms."""
        return self._cxr.classify_fund(ftype, name)
