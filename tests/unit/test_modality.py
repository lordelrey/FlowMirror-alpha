"""Unit tests for the three-arm modality contract (T / TC / TV).

Scope: flowmirror.channels.feed (arm_for_agent / assign_arms /
assign_agent_arms / check_arm_balance) and flowmirror.agents.prompt (TC card
rendering and the n_comments_prev social header). Engine-side wiring is
covered by separate tests.

Arm contract: the reference agent-level assignment is
assign_agent_arms(), a cohort-level stratified block randomisation over the
population cells -- every cell receives floor(n_cell/k) or ceil(n_cell/k)
agents per arm, no cell of size >= 2 is single-armed, the overall share
stays within one agent of 1/k, and check_arm_balance must therefore pass
for EVERY tag (zero failures, not "all but 2 of 20").  arm_for_agent() is
the deliberately UNBALANCED per-agent coin used by the exposure path and by
callers without a cohort; it is NOT the cohort-balanced assignment, and only
determinism, arm-set membership and large-n statistical shares are asserted
for it.  check_arm_balance() now reports n_agents, the per-arm
<arm>_share keys and the block-randomisation worst cases
(worst_overall_dev, worst_cell_dev, single_armed_cells); the superseded
max_abs_diff_<ARM> keys and the exact report-key-list assertions went away
with the report that had them.

Run with either:
    python tests/unit/test_modality.py
    python -m unittest discover -s tests/unit -p "test_modality.py"
Pure stdlib; no network; the only filesystem use is one temp JPEG.
Assertion messages stay ASCII (console may be GBK); CJK lives only in the
stimulus strings being compared.
"""

import hashlib
import os
import random
import tempfile
import unittest

from flowmirror.agents import prompt
from flowmirror.channels import feed

THREE = ("T", "TC", "TV")
JPEG_HEX = "ffd8ffe000104a46494600010100000100010000ffd9"  # minimal JFIF


def _seeded(tag):
    """Project-standard RNG: random.Random(int(sha256(tag)[:16], 16))."""
    return random.Random(int(hashlib.sha256(tag.encode()).hexdigest()[:16], 16))


def _legacy_arm(run_tag, agent_id):
    """The pre-v1.5 two-arm coin, reimplemented inline from the frozen rule."""
    digest = hashlib.sha256(("%s|arm|%s" % (run_tag, agent_id)).encode()).hexdigest()
    return "TV" if random.Random(int(digest[:16], 16)).random() < 0.5 else "T"


class ArmForAgentTests(unittest.TestCase):
    """arm_for_agent() is the deliberately UNBALANCED per-agent coin on the
    frozen sha256(run_tag|arm|agent_id) stream. It is NOT the cohort-balanced
    assignment (an independent coin violated the balance target on ~35% of
    run tags and gave whole cells a single arm); it survives for the exposure
    path and for callers without a cohort.  So these tests assert only what
    is true of a coin: determinism, membership in the arm set, and shares
    approaching 1/k in the large-n limit.  check_arm_balance is deliberately
    NEVER called on arm_for_agent output -- that invariant belongs to
    assign_agent_arms (see AssignAgentArmsTests)."""

    def test_default_two_arms_equal_legacy(self):
        for tag in ("tag", "rt_fixed", "m0_mock"):
            for i in range(20):
                aid = "inv_%05d" % i
                want = _legacy_arm(tag, aid)
                self.assertEqual(feed.arm_for_agent(tag, aid), want)
                self.assertEqual(feed.arm_for_agent(tag, aid, arms=["T", "TV"]), want)
                self.assertEqual(feed.arm_for_agent(tag, aid, arms=("TV", "T")), want)
                self.assertEqual(feed._arm_for_agent_legacy(tag, aid), want)

    def test_three_arm_stream_frozen(self):
        # int(rng.random()*3) indexing into ("T","TC","TV") on the frozen
        # sha256(f"{run_tag}|arm|{agent_id}") stream.
        for i in (0, 1, 7, 19, 42):
            aid = "inv_%05d" % i
            r = _seeded("rt3|arm|%s" % aid)
            self.assertEqual(feed.arm_for_agent("rt3", aid, arms=THREE),
                             THREE[min(int(r.random() * 3), 2)])

    def test_three_arm_deterministic_and_tag_sensitive(self):
        a = [feed.arm_for_agent("t1", "inv_%05d" % i, arms=THREE) for i in range(50)]
        b = [feed.arm_for_agent("t1", "inv_%05d" % i, arms=THREE) for i in range(50)]
        c = [feed.arm_for_agent("t2", "inv_%05d" % i, arms=THREE) for i in range(50)]
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertTrue(set(a) <= set(THREE))

    def test_three_arm_shares_over_3000_agents(self):
        # Statistical law-of-large-numbers check ONLY for the unbalanced
        # per-agent draw (the exposure path): arm_for_agent is NOT the
        # cohort-balanced assignment, so no balance invariant is asserted
        # here.  With 3,000 iid draws per tag the per-tag share bound 0.045
        # is a ~5.2-sigma envelope; the 20-tag pooled bound 0.012 is ~6.9
        # sigma. The deterministic balance assertions live in
        # AssignAgentArmsTests below.
        n_tags = 20
        pooled = dict.fromkeys(THREE, 0)
        for k in range(n_tags):
            tag = "mod3k_%02d" % k
            ids = ["inv_%05d" % i for i in range(3000)]
            arms = {aid: feed.arm_for_agent(tag, aid, arms=THREE) for aid in ids}
            counts = {a: sum(1 for v in arms.values() if v == a) for a in THREE}
            for a in THREE:
                pooled[a] += counts[a]
                self.assertLessEqual(
                    abs(counts[a] / 3000.0 - 1.0 / 3.0), 0.045,
                    "tag=%s arm=%s share=%.4f" % (tag, a, counts[a] / 3000.0))
        for a in THREE:
            self.assertLessEqual(
                abs(pooled[a] / (3000.0 * n_tags) - 1.0 / 3.0), 0.012,
                "pooled arm=%s share=%.5f" % (a, pooled[a] / (3000.0 * n_tags)))
        self.assertGreater(min(pooled.values()), 0)  # all three arms observed

    def test_bad_arms_raise(self):
        with self.assertRaises(ValueError):
            feed.arm_for_agent("t", "inv_00000", arms=())
        with self.assertRaises(ValueError):
            feed.arm_for_agent("t", "inv_00000", arms=("T", "T"))
        with self.assertRaises(ValueError):
            feed.assign_arms(_seeded("ut_bad_arms"), 1, {}, arms=[])


class AssignArmsTests(unittest.TestCase):
    def test_default_two_arm_matches_legacy_algorithm(self):
        def legacy(rng, k, tally):
            out = []
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

        r1 = _seeded("ut|assign|legacy")
        r2 = _seeded("ut|assign|legacy")
        t1, t2 = {"TV": 0, "T": 0}, {"TV": 0, "T": 0}
        got, want = [], []
        worst = 0
        for _ in range(500):
            got.extend(feed.assign_arms(r1, 1, t1))
            want.extend(legacy(r2, 1, t2))
            worst = max(worst, abs(t1["TV"] - t1["T"]))
        self.assertEqual(got, want)
        self.assertEqual(t1, t2)
        self.assertLessEqual(worst, 1)

    def test_explicit_default_arms_argument_identical(self):
        r1 = _seeded("ut|assign|explicit")
        r2 = _seeded("ut|assign|explicit")
        t1, t2 = {"TV": 0, "T": 0}, {"TV": 0, "T": 0}
        d1, d2 = [], []
        for _ in range(300):
            d1.extend(feed.assign_arms(r1, 1, t1))
            d2.extend(feed.assign_arms(r2, 1, t2, arms=("T", "TV")))
        self.assertEqual(d1, d2)
        self.assertEqual(t1, t2)

    def test_three_arm_draws(self):
        r1 = _seeded("ut|assign|three_a")
        r2 = _seeded("ut|assign|three_a")
        t1, t2 = {}, {}
        d1, d2 = [], []
        for _ in range(3000):
            d1.extend(feed.assign_arms(r1, 1, t1, arms=THREE))
            d2.extend(feed.assign_arms(r2, 1, t2, arms=THREE))
        self.assertEqual(d1, d2)
        self.assertEqual(t1, t2)
        self.assertTrue(set(d1) <= set(THREE))
        self.assertEqual(sorted(t1), sorted(THREE))
        for a in THREE:
            self.assertEqual(t1[a], d1.count(a))
            self.assertLessEqual(abs(t1[a] / 3000.0 - 1.0 / 3.0), 0.05,
                                 "arm=%s share=%.4f" % (a, t1[a] / 3000.0))

    def test_three_arm_batch(self):
        tally = {}
        batch = feed.assign_arms(_seeded("ut|assign|batch"), 5, tally, arms=list(THREE))
        self.assertEqual(len(batch), 5)
        self.assertTrue(all(a in THREE for a in batch))
        self.assertEqual(sum(tally.values()), 5)


class AssignAgentArmsTests(unittest.TestCase):
    """The reference assignment: assign_agent_arms()
    deals arms stratified by population cell (pure sha256 ordering, cells and
    ids in sorted order).  Contract asserted here, per tag with ZERO
    failures allowed: every cell is floor/ceil balanced, no cell of size
    >= 2 is single-armed, the overall counts stay within one agent of n/k,
    and the tightened check_arm_balance passes for every tag."""

    @staticmethod
    def _ids(n):
        return ["inv_%05d" % i for i in range(n)]

    @staticmethod
    def _cells(ids, n_cells):
        return {aid: "cell_%02d" % (i % n_cells) for i, aid in enumerate(ids)}

    def _assert_cell_contract(self, assigned, cells, arms):
        """Per-cell floor/ceil balance + no single-armed cell of size >= 2.
        -> overall per-arm counts."""
        by_cell = {}
        for aid, arm in assigned.items():
            self.assertIn(arm, arms)
            by_cell.setdefault(cells[aid], []).append(arm)
        k = len(arms)
        overall = dict.fromkeys(arms, 0)
        for cell_id in sorted(by_cell):
            members = by_cell[cell_id]
            n_c = len(members)
            q, r = divmod(n_c, k)
            counts = {a: members.count(a) for a in arms}
            for a in arms:
                overall[a] += counts[a]
            self.assertEqual(sorted(counts.values()),
                             [q] * (k - r) + [q + 1] * r,
                             "cell=%s counts=%s not floor/ceil for n=%d k=%d"
                             % (cell_id, sorted(counts.items()), n_c, k))
            if n_c >= 2:
                self.assertGreaterEqual(
                    len(set(members)), 2,
                    "cell=%s (n=%d) is single-armed" % (cell_id, n_c))
        return overall

    def test_three_arm_zero_failing_tags_over_3000_agents(self):
        # The point of the stratified-block fix: the OLD per-agent coin
        # failed check_arm_balance on ~35% of tags (whole cells single-armed);
        # assign_agent_arms must fail on ZERO of them.
        n_tags = 20
        n_bad = 0
        for k in range(n_tags):
            tag = "mod3k_%02d" % k
            ids = self._ids(3000)
            cells = self._cells(ids, 36)  # 12 cells of 84 (n%3==0), 24 of 83
            assigned = feed.assign_agent_arms(tag, [(i, cells[i]) for i in ids], arms=THREE)
            overall = self._assert_cell_contract(assigned, cells, THREE)
            for a in THREE:
                self.assertLessEqual(
                    abs(overall[a] - 1000), 1,
                    "tag=%s arm=%s overall=%d not within one agent of n/k"
                    % (tag, a, overall[a]))
            ok, _rep = feed.check_arm_balance(assigned, cells, arms=THREE)
            if not ok:
                n_bad += 1
        self.assertEqual(n_bad, 0, "balance failed for %d/%d tags" % (n_bad, n_tags))

    def test_two_arm_strata_balanced(self):
        ids = self._ids(400)
        cells = self._cells(ids, 36)  # cells of 11 and 12 (odd sizes included)
        assigned = feed.assign_agent_arms("ut|agentarms|2a", [(i, cells[i]) for i in ids],
                                          arms=("T", "TV"))
        overall = self._assert_cell_contract(assigned, cells, ("T", "TV"))
        for a in ("T", "TV"):
            self.assertLessEqual(abs(overall[a] - 200), 1,
                                 "arm=%s overall=%d not within one agent of n/k"
                                 % (a, overall[a]))
        ok, rep = feed.check_arm_balance(assigned, cells, arms=("T", "TV"))
        self.assertTrue(ok)
        self.assertFalse(rep["single_armed_cells"])

    def test_deterministic_and_tag_sensitive(self):
        ids = self._ids(600)
        cells = self._cells(ids, 24)
        a1 = feed.assign_agent_arms("t1", [(i, cells[i]) for i in ids], arms=THREE)
        a2 = feed.assign_agent_arms("t1", [(i, cells[i]) for i in ids], arms=THREE)
        b = feed.assign_agent_arms("t2", [(i, cells[i]) for i in ids], arms=THREE)
        self.assertEqual(a1, a2)
        self.assertNotEqual(a1, b)
        self.assertEqual(sorted(a1), sorted(ids))  # every agent, exactly once
        self.assertTrue(set(a1.values()) <= set(THREE))


class CheckBalanceTests(unittest.TestCase):
    @staticmethod
    def _population(n, fn):
        ids = ["inv_%05d" % i for i in range(n)]
        return ids, {a: fn(i) for i, a in enumerate(ids)}

    def test_two_arm_report_shape_and_math(self):
        # Per-cell BALANCED fixture (the old i%2 arms with i%36 cells put a
        # single arm in every cell and now fails the per-cell tolerance):
        # 40 cells of 10, arms alternating WITHIN each cell (cell = i%40,
        # arm = (i//40)%2), so the overall 0.01 rule and the per-cell
        # 1/(2*n_cell) rule both hold exactly.
        ids, arms = self._population(
            400, lambda i: "TV" if (i // 40) % 2 == 0 else "T")
        cells = {a: "cell_%02d" % (i % 40) for i, a in enumerate(ids)}
        ok, rep = feed.check_arm_balance(arms, cells)
        self.assertTrue(ok)
        # Membership of the current keys only -- the report also carries the
        # block-randomisation worst cases and no longer equals the old exact
        # key list, so no sorted(rep) assertion here.
        for key in ("n_agents", "tv_share", "worst_overall_dev",
                    "worst_cell_dev", "single_armed_cells"):
            self.assertIn(key, rep)
        self.assertEqual(rep["n_agents"], 400)
        self.assertEqual(rep["tv_share"], 0.5)
        self.assertAlmostEqual(rep["worst_overall_dev"], 0.0, places=9)
        self.assertAlmostEqual(rep["worst_cell_dev"], 0.0, places=9)
        self.assertFalse(rep["single_armed_cells"])

    def test_two_arm_skew_rejected(self):
        ids, arms = self._population(400, lambda i: "TV" if i < 300 else "T")
        cells = {a: "c%d" % (i % 4) for i, a in enumerate(ids)}
        ok, rep = feed.check_arm_balance(arms, cells)
        self.assertFalse(ok)
        self.assertAlmostEqual(rep["tv_share"], 0.75)

    def test_three_arm_round_robin_passes(self):
        # Exact round-robin arms with cells built from consecutive triples:
        # per-arm cell shares equal the overall shares exactly, so only the
        # 1/k share rule is exercised in isolation.
        ids, arms = self._population(3000, lambda i: THREE[i % 3])
        cells = {a: "cell_%02d" % ((i // 3) % 36) for i, a in enumerate(ids)}
        ok, rep = feed.check_arm_balance(arms, cells)
        self.assertTrue(ok)
        self.assertAlmostEqual(rep["t_share"], 1.0 / 3.0, places=12)
        self.assertAlmostEqual(rep["tc_share"], 1.0 / 3.0, places=12)
        self.assertAlmostEqual(rep["tv_share"], 1.0 / 3.0, places=12)
        # The per-arm share keys are asserted by membership: the report now
        # also carries worst_overall_dev / worst_cell_dev / single_armed_cells
        # (and possibly more), so the old exact share-key list no longer
        # holds.  On this fixture every worst case is exactly zero.
        self.assertTrue({"t_share", "tc_share", "tv_share"} <= set(rep))
        self.assertAlmostEqual(rep["worst_cell_dev"], 0.0, places=9)
        self.assertFalse(rep["single_armed_cells"])

    def test_explicit_arms_empty_arm_fails(self):
        ids, arms = self._population(300, lambda i: "T" if i % 2 == 0 else "TV")
        cells = {a: "cell_%02d" % (i % 36) for i, a in enumerate(ids)}
        ok, rep = feed.check_arm_balance(arms, cells, arms=("T", "TC", "TV"))
        self.assertFalse(ok)
        self.assertEqual(rep.get("tc_share"), 0.0)
        # The old per-arm max_abs_diff_<ARM> keys are gone; the report now
        # surfaces the worst observed deviations and the single-armed cells.
        # With TC at share 0 the worst overall deviation is 1/3; and because
        # 36 is even, every i%36 cell holds a single parity arm.
        self.assertGreaterEqual(rep["worst_overall_dev"], 1.0 / 3.0 - 1e-9)
        self.assertTrue(rep["single_armed_cells"])
        self.assertAlmostEqual(rep.get("tv_share"), 0.5)

    def test_three_arm_skew_rejected(self):
        ids, arms = self._population(
            400, lambda i: "T" if i < 180 else ("TC" if i < 320 else "TV"))
        cells = {a: "cell_%02d" % ((i // 3) % 36) for i, a in enumerate(ids)}
        ok, rep = feed.check_arm_balance(arms, cells)
        self.assertFalse(ok)
        self.assertAlmostEqual(rep["t_share"], 0.45)
        self.assertAlmostEqual(rep["tc_share"], 0.35)
        self.assertAlmostEqual(rep["tv_share"], 0.20)

    def test_empty_population_fails(self):
        ok, rep = feed.check_arm_balance({}, {})
        self.assertFalse(ok)
        self.assertEqual(rep.get("n_agents"), 0)


class PromptCardTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="fm_tc_ut_")
        self.jpg = os.path.join(self._tmp.name, "tiny.jpg")
        with open(self.jpg, "wb") as fh:
            fh.write(bytes.fromhex(JPEG_HEX))

    def tearDown(self):
        self._tmp.cleanup()

    def _card(self, **kw):
        base = {"post_id": "p1", "org": "orgA", "title": "标题", "caption": "正文。",
                "landing": None, "likes": 1, "arm": "T", "image_path": None,
                "image_sha": None, "comments_prev": [], "climate": "no_signal"}
        base.update(kw)
        return base

    @staticmethod
    def _view():
        return {"persona_card_zh_rich": "你是测试投资者甲，四十岁，风险偏好中等。",
                "market_view": 3, "risk_mood": 3, "memory": [], "c_class": "C3",
                "cash": 10000.0, "holdings": [], "familiarity": {}}

    @staticmethod
    def _cfg():
        return {"channels": {"social": True}, "social": True}

    def test_tc_renders_ocr_and_caption_never_attaches_image(self):
        card = self._card(arm="TC", image_path=self.jpg,
                          ocr_masked="OCR主文本。",
                          ocr_text="不应采用的备用OCR。",
                          image_caption_frozen=["冻结描述一。", "冻结描述二。"])
        text = prompt.render_card(card)
        self.assertIn("图片信息（文字）：OCR主文本。冻结描述一。冻结描述二。", text)
        self.assertNotIn("不应采用的备用OCR。", text)
        msgs, _sha, ishas, notes = prompt.build_decision_messages(
            self._view(), [card], self._cfg())
        parts = msgs[1]["content"]
        self.assertEqual([p for p in parts if p.get("type") == "image_url"], [])
        self.assertEqual(ishas, [])
        self.assertNotIn("tc_no_caption", notes)

    def test_tc_ocr_text_fallback_and_str_caption(self):
        card = self._card(arm="TC", ocr_text="只用备用OCR。", image_caption_frozen="整段冻结描述。")
        self.assertIn("图片信息（文字）：只用备用OCR。整段冻结描述。", prompt.render_card(card))

    def test_tc_ocr_capped_at_200_chars(self):
        card = self._card(arm="TC", ocr_text="长" * 500)
        text = prompt.render_card(card)
        self.assertIn("图片信息（文字）：" + "长" * 200, text)
        self.assertNotIn("长" * 201, text)

    def test_tc_without_payload_notes_tc_no_caption(self):
        card = self._card(arm="TC")
        self.assertIn("配图信息不可用。", prompt.render_card(card))
        _m, _s, _i, notes = prompt.build_decision_messages(self._view(), [card], self._cfg())
        self.assertIn("tc_no_caption", notes)

    def test_tv_and_t_branches_unchanged(self):
        tv = self._card(arm="TV", image_path=self.jpg)
        msgs, _s, ishas, _n = prompt.build_decision_messages(self._view(), [tv], self._cfg())
        imgs = [p for p in msgs[1]["content"] if p.get("type") == "image_url"]
        self.assertEqual(len(imgs), 1)
        self.assertEqual(len(ishas), 1)
        t = self._card(arm="T")
        self.assertIn("配图不展示。", prompt.render_card(t))
        self.assertNotIn("图片信息", prompt.render_card(t))

    def test_social_header_uses_n_comments_prev(self):
        cps = [{"stance": "bullish", "text": "第一条", "fam_phrase": "老持有人"},
               {"stance": "watching", "text": "第二条", "fam_phrase": "新人"},
               {"stance": "bearish", "text": "第三条", "fam_phrase": "路人"},
               {"stance": "watching", "text": "第四条不展示", "fam_phrase": "路人"}]
        card = self._card(comments_prev=cps, climate="mixed", n_comments_prev=11)
        s = prompt.render_social(card)
        self.assertIn("昨日评论（共 11 条，看法分歧）：", s)
        self.assertEqual(len([ln for ln in s.splitlines() if ln]), 4)  # header + top-3
        self.assertNotIn("第四条不展示", s)

    def test_social_header_fallback_without_n_comments_prev(self):
        cps = [{"stance": "bullish", "text": "a", "fam_phrase": "f"},
               {"stance": "bearish", "text": "b", "fam_phrase": "f"}]
        card = self._card(comments_prev=cps, climate="mixed")
        self.assertIn("昨日评论（共 2 条，看法分歧）：", prompt.render_social(card))

    def test_social_header_clamps_inconsistent_n(self):
        cps = [{"stance": "bullish", "text": "a", "fam_phrase": "f"},
               {"stance": "bullish", "text": "b", "fam_phrase": "f"},
               {"stance": "bullish", "text": "c", "fam_phrase": "f"}]
        card = self._card(comments_prev=cps, climate="bullish_majority", n_comments_prev=1)
        self.assertIn("昨日评论（共 3 条，多数看多）：", prompt.render_social(card))

    def test_social_no_signal_stays_empty(self):
        card = self._card(comments_prev=[], climate="no_signal", n_comments_prev=9)
        self.assertEqual(prompt.render_social(card), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
