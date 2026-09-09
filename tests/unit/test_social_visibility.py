"""Unit tests for social-graph visibility (card E7).

Covers two coupled fixes:
1. render_social no longer drops the whole yesterday-comments block when the
   climate label is no_signal, provided the comments carry handles (i.e. the
   social graph is on). The off-state (no handles) rendering is unchanged.
2. top_comments tie-breaking: salt=None reproduces the historical ascending
   agent-id order; a salt replaces the id with a deterministic digest so day-0
   exposure does not reduce to "lowest agent ids win", while follower count
   remains the primary key.
"""
import unittest

from flowmirror.agents.prompt import render_social
from flowmirror.channels.feed import top_comments

CMT_PLAIN = [{"stance": "bullish", "text": "看好", "fam_phrase": "路人"}]
CMT_HANDLE = [{"stance": "bullish", "text": "看好", "fam_phrase": "路人",
               "handle": "@u3f9a2", "followers": 7}]


def card(cps, label, n=None):
    c = {"post_id": "P1", "comments_prev": cps, "climate_label": label}
    if n is not None:
        c["n_comments_prev"] = n
    return c


def _cmts(*id_follower_pairs):
    """Comment dicts with long-form keys, all ties except where stated."""
    # top_comments filters by post_id first, so a fixture without it yields an empty pool.
    return [{"post_id": "P1", "stance": "bullish", "text": "x", "agent_id": aid,
             "followers": nf, "fam_level": 0}
            for aid, nf in id_follower_pairs]


def _ids(pool):
    """Extract agent ids from returned comments (long or short key form)."""
    out = []
    for c in pool:
        aid = c.get("agent_id")
        if aid is None:
            aid = c.get("i")
        out.append(str(aid))
    return out


SALTS = ["s|0|P1", "s|1|P1", "s|2|P1", "s|3|P1", "s|4|P1"]


class RenderSocialVisibility(unittest.TestCase):

    def test_no_signal_without_handles_still_suppressed(self):
        """Off-state behaviour: without handles the block stays fully hidden."""
        self.assertEqual(render_social(card(CMT_PLAIN, "no_signal")), "")

    def test_no_signal_with_handles_renders_excerpt(self):
        """Handles survive the no_signal gate; the header asserts no majority."""
        out = render_social(card(CMT_HANDLE, "no_signal", n=1))
        self.assertTrue(out)
        self.assertIn("@u3f9a2", out)
        self.assertIn("粉丝 7", out)
        self.assertNotIn("看法分歧", out)
        self.assertIn("昨日评论（共 1 条）：", out)

    def test_labelled_header_unchanged(self):
        """A labelled climate still renders its phrase after the count."""
        out = render_social(card(CMT_HANDLE, "bullish_majority", n=1))
        head = next(ln for ln in out.splitlines() if ln.startswith("昨日评论"))
        self.assertIn("，", head)
        self.assertNotEqual(head, "昨日评论（共 1 条）：")

    def test_empty_comments_always_empty(self):
        """No comments at all -> empty block regardless of the label."""
        self.assertEqual(render_social(card([], "bullish_majority")), "")


class TopCommentsTiebreak(unittest.TestCase):

    def test_tiebreak_without_salt_is_id_order(self):
        """salt=None reproduces the historical ascending-id order exactly."""
        cmts = _cmts(("a3", 0), ("a1", 0), ("a2", 0))
        self.assertEqual(_ids(top_comments("P1", cmts, k=3)), ["a1", "a2", "a3"])

    def test_tiebreak_with_salt_is_deterministic_and_not_id_order(self):
        """Same salt -> identical order; some salt breaks the pure id order."""
        cmts = _cmts(("a3", 0), ("a1", 0), ("a2", 0))
        r1 = _ids(top_comments("P1", cmts, k=3, salt="s|0|P1"))
        r2 = _ids(top_comments("P1", cmts, k=3, salt="s|0|P1"))
        self.assertEqual(r1, r2)
        for salt in SALTS:
            if _ids(top_comments("P1", cmts, k=3, salt=salt)) != ["a1", "a2", "a3"]:
                return
        self.fail("no salt in %r produced a non-id order" % (SALTS,))

    def test_followers_still_dominate_salt(self):
        """Follower count outranks the salted tiebreak for every salt."""
        cmts = _cmts(("a1", 0), ("a2", 5), ("a3", 0))
        for salt in SALTS + [None]:
            self.assertEqual(_ids(top_comments("P1", cmts, k=3, salt=salt))[0], "a2")


if __name__ == "__main__":
    unittest.main()
