"""Unit tests for per-agent comment excerpts: following someone must change what the card shows."""
import unittest

from flowmirror.agents.prompt import render_social
from flowmirror.engine.loop import _merged_excerpt


def c(aid, text="x", stance="bullish"):
    return {"i": aid, "p": "P1", "stance": stance, "text": text, "fam_phrase": "路人"}


TOP = {"P1": [c("a1"), c("a2"), c("a3")]}
POOL = {"P1": [c("a1"), c("a2"), c("a3"), c("z9", text="被关注者说的")]}


class MergedExcerptTests(unittest.TestCase):
    def test_off_returns_shared_top_unchanged(self):
        """Graph off, or nobody followed, must hand back the shared top-k unchanged."""
        self.assertEqual(_merged_excerpt("P1", TOP, POOL, {"z9"}, False), TOP["P1"])
        self.assertEqual(_merged_excerpt("P1", TOP, POOL, None, True), TOP["P1"])

    def test_no_pool_returns_shared_top(self):
        """No t-1 pool to promote from: fall back to the shared ranking."""
        self.assertEqual(_merged_excerpt("P1", TOP, None, {"z9"}, True), TOP["P1"])

    def test_followee_promoted_to_front(self):
        """A followee the shared top-3 missed still takes a front slot; 3 quotes, deduped."""
        got = _merged_excerpt("P1", TOP, POOL, {"z9"}, True)
        self.assertEqual([x["i"] for x in got], ["z9", "a1", "a2"])

    def test_two_followees_take_two_slots(self):
        """Two followees fill both priority slots (engine append order); shared top fills the rest."""
        got = _merged_excerpt("P1", TOP, POOL, {"z9", "a3"}, True)
        self.assertEqual([x["i"] for x in got], ["a3", "z9", "a1"])

    def test_dedup_when_followee_already_in_top(self):
        """A followee already ranked is not quoted twice; they just move to the front."""
        got = _merged_excerpt("P1", TOP, POOL, {"a2"}, True)
        self.assertEqual(len(got), 3)
        self.assertEqual(len({x["i"] for x in got}), 3)
        self.assertEqual(got[0]["i"], "a2")

    def test_unknown_post_id_is_empty(self):
        """A post absent from both rankings has no excerpt."""
        self.assertEqual(_merged_excerpt("PX", TOP, POOL, {"z9"}, True), [])


class RenderSocialTests(unittest.TestCase):
    def test_render_marks_followed(self):
        """The rendered card must say when a quote is shown because its author is followed."""
        card = {"post_id": "P1", "climate_label": "no_signal",
                "comments_prev": [{"stance": "bullish", "text": "x", "fam_phrase": "路人",
                                   "handle": "@u3f9a2", "followers": 2, "followed": True}]}
        self.assertIn("（你关注的）", render_social(card))
        card["comments_prev"][0]["followed"] = False
        self.assertNotIn("（你关注的）", render_social(card))


if __name__ == "__main__":
    unittest.main()
