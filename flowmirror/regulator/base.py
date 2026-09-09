"""Regulator protocol for FlowMirror v7 (phase P2).

Phase P2 lifts the CN cross-border (CXR) suitability rule out of
``sim/engine_v5.py`` into the package without changing behaviour:
``cn_cxr.CNCXR`` reproduces ``engine_v5.cxr_outcome`` exactly and
``none.NoGate`` provides the suitability=false arm.  This module defines
the structural interface both implementations satisfy, so a future regime
change is a new implementation, not an edit to frozen code.

Checkout vocabulary (frozen; see ``OUTCOMES``):

    match             reported client class C permits fund risk level R
    confirm_signed    mismatch; agent SIGNED the risk-mismatch confirmation
    confirm_declined  mismatch; agent declined the confirmation
    hard_block        C1 x R>1 (unreachable in the v7 cohort; see cn_cxr)
    purchase_blocked  channel-level block (e.g. QDII quota suspended)

Signatures: the third positional of ``checkout`` is the per-decision
boolean flag carried from the agent decision record.  ``CNCXR`` consumes
it as the sign-mismatch-confirm (``smc``) flag of engine_v5 and accepts
an additional optional ``is_qdii_blocked`` for protocol-compatible call
sites; a channel-gating regulator reads the flag slot as the QDII block
bit and answers ``purchase_blocked``.  ``CNCXR`` itself never returns
``purchase_blocked``: the QDII quota gate is layered by the caller BEFORE
checkout, so the C x R verdict stays byte-identical to engine_v5 for
either value of the flag (pinned by the exhaustive table test).

``counterfactual`` is an alias of ``checkout``: for an ACTIVE gate the
observed outcome and its counterfactual coincide.  ``NoGate``
deliberately breaks the alias (``checkout`` -> "match", while
``counterfactual`` -> what ``CNCXR`` would have decided); that is how
``co.oc_cf`` is produced when suitability=false.

Redemptions are never gated by any Regulator: regulating the sell side
is the caller's responsibility and is documented per implementation.
"""

from typing import Protocol, runtime_checkable

__all__ = ["OUTCOMES", "Regulator"]

OUTCOMES = (
    "match",
    "confirm_signed",
    "confirm_declined",
    "hard_block",
    "purchase_blocked",
)


@runtime_checkable
class Regulator(Protocol):
    """Structural interface for purchase-gate implementations (see module docstring)."""

    def checkout(self, reported_c: str, fund_r: str, is_qdii_blocked: bool) -> str:
        """Decide a purchase attempt; returns one of ``OUTCOMES``.

        ``reported_c``: the client's REPORTED suitability class, C1..C5.
        ``fund_r``: the fund risk level, R1..R5.  The third positional is
        the per-decision flag (``smc`` for the C x R rule; the QDII block
        bit for channel-gating regulators).  Redemptions are never gated.
        """
        ...

    def counterfactual(self, reported_c: str, fund_r: str, is_qdii_blocked: bool) -> str:
        """Alias for ``checkout``; deliberately overridden by ``NoGate``."""
        ...

    def classify_fund(self, ftype: str, name: str) -> str:
        """R-level helper interface for fund labelling.

        Maps a flow-panel fund ``type`` string plus the fund display name
        to its risk level.  The engine_v5-compatible implementation
        (``CNCXR.classify_fund``) returns the ``(r, qdii)`` pair so old
        call sites keep working; callers wanting only the R level use
        element ``[0]``.
        """
        ...
