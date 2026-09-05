"""Exhaustive equivalence pin: flowmirror.regulator.cn_cxr vs sim/engine_v5.

engine_v5 is referenced lazily from FLOWMIRROR_RESEARCH_ROOT (default
"D:/Desktop/ABM paper/fundmarket-sim").  Importing it must not run the
simulation: the loader first execs the module (module-level constants
only) inside try/except, and on ANY failure falls back to ast-extracting
only C_RANK / R_RANK / cxr_outcome / classify_fund and exec'ing those
defs in a bare namespace, so no other module-level statement ever runs.
"""

import ast
import importlib.util
import os
import sys
from pathlib import Path

import pytest

# Make the package importable no matter where pytest was started from.
_PKG_PARENT = Path(__file__).resolve().parents[2]
if str(_PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(_PKG_PARENT))

from flowmirror.regulator.cn_cxr import CNCXR  # noqa: E402
from flowmirror.regulator.none import NoGate  # noqa: E402

RESEARCH_ROOT = Path(os.environ.get("FLOWMIRROR_RESEARCH_ROOT",
                                    "D:/Desktop/ABM paper/fundmarket-sim"))
CS = ("C1", "C2", "C3", "C4", "C5")
RS = ("R1", "R2", "R3", "R4", "R5")


def _load_reference():
    """Return (cxr_outcome, classify_fund) from engine_v5; skip if unavailable."""
    engine_path = RESEARCH_ROOT / "sim" / "engine_v5.py"
    if not engine_path.is_file():
        pytest.skip(f"engine_v5.py not found under {RESEARCH_ROOT}")
    # Fast path: normal module exec; engine_v5 defines its constants at
    # module level and must not run the simulation on import.
    try:
        spec = importlib.util.spec_from_file_location("_engine_v5_reference", engine_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if (hasattr(mod, "cxr_outcome") and hasattr(mod, "C_RANK")
                and hasattr(mod, "R_RANK")):
            return mod.cxr_outcome, getattr(mod, "classify_fund", None)
    except Exception:
        pass
    # Fallback: extract ONLY the needed defs via ast and exec them in a
    # bare namespace (guards against import-time side effects).
    tree = ast.parse(engine_path.read_text(encoding="utf-8"), filename=str(engine_path))
    wanted = {"C_RANK", "R_RANK", "cxr_outcome", "classify_fund"}
    ns = {}
    for node in tree.body:
        keep = False
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            keep = True
        elif isinstance(node, ast.Assign):
            keep = any(isinstance(t, ast.Name) and t.id in wanted
                       for t in node.targets)
        if keep:
            exec(compile(ast.Module(body=[node], type_ignores=[]),
                         str(engine_path), "exec"), ns)
    if "cxr_outcome" not in ns:
        pytest.skip("cxr_outcome could not be extracted from engine_v5.py")
    return ns["cxr_outcome"], ns.get("classify_fund")


def test_exhaustive_table_matches_engine_v5():
    ref_cxr, _ = _load_reference()
    reg = CNCXR()
    checked = 0
    for reported_c in CS:
        for fund_r in RS:
            for smc in (False, True):
                for is_qdii in (False, True):
                    expected = ref_cxr(reported_c, fund_r, smc)
                    got = reg.checkout(reported_c, fund_r, smc, is_qdii)
                    assert got == expected, (reported_c, fund_r, smc, is_qdii, got, expected)
                    checked += 1
    assert checked == 5 * 5 * 2 * 2


def test_hard_block_only_for_c1_above_r1():
    # PREREG v1.1 B5: only C1 x R>1 can hard-block, and the cohort has no
    # C1 members, so C2..C5 must NEVER yield hard_block.
    reg = CNCXR()
    for reported_c in CS[1:]:
        for fund_r in RS:
            for smc in (False, True):
                for is_qdii in (False, True):
                    assert reg.checkout(reported_c, fund_r, smc, is_qdii) != "hard_block"
    for fund_r in RS[1:]:
        assert reg.checkout("C1", fund_r, False) == "hard_block"
    assert reg.checkout("C1", "R1", False) == "match"


def test_nogate_checkout_and_counterfactual():
    ng = NoGate()
    cxr = CNCXR()
    for reported_c in CS:
        for fund_r in RS:
            for smc in (False, True):
                for is_qdii in (False, True):
                    assert ng.checkout(reported_c, fund_r, smc, is_qdii) == "match"
                    assert (ng.counterfactual(reported_c, fund_r, smc, is_qdii)
                            == cxr.checkout(reported_c, fund_r, smc, is_qdii))


def test_classify_fund_matches_engine_v5():
    ref_cxr, ref_classify = _load_reference()
    if ref_classify is None:
        pytest.skip("classify_fund not available in the engine_v5 reference")
    reg = CNCXR()
    cases = [
        ("债券型", "稳健增利债券"),
        ("债券型", "海外中国债券"),
        ("沪深300指数", "指数增强"),
        ("QDII", "纳斯达克100"),
        ("混合型", "标普500指数"),
        ("ETF联接", "日经225"),
        ("行业股票", "医药主题"),
        ("货币市场型", "现金宝"),
        ("股票型", "价值成长精选"),
    ]
    for ftype, name in cases:
        assert reg.classify_fund(ftype, name) == ref_classify(ftype, name)


def test_module_level_wrappers_match_class():
    from flowmirror.regulator import cn_cxr
    reg = CNCXR()
    for reported_c in CS:
        for fund_r in RS:
            assert (cn_cxr.cxr_outcome(reported_c, fund_r, True)
                    == reg.checkout(reported_c, fund_r, True))
    assert cn_cxr.classify_fund("债券型", "x") == reg.classify_fund("债券型", "x")
