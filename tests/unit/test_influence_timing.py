import unittest

from flowmirror.analysis.influence import _copied

FAN = "fan"
STAR = "star"


def copied(star_buys, fan_buys, t0=0):
    buys = {STAR: list(star_buys), FAN: list(fan_buys)}
    return _copied(buys, FAN, STAR, t0)


class TestCopiedTiming(unittest.TestCase):
    def test_copy_after_star(self):
        self.assertEqual(copied([(0, "F1")], [(1, "F1")]), 1)

    def test_no_copy_when_fan_before_star(self):
        self.assertEqual(copied([(2, "F1")], [(1, "F1")]), 0)

    def test_same_day_is_not_copy(self):
        self.assertEqual(copied([(1, "F1")], [(1, "F1")]), 0)

    def test_different_fund_is_not_copy(self):
        self.assertEqual(copied([(0, "F1")], [(1, "F2")]), 0)

    def test_no_star_buys_gives_none(self):
        self.assertIsNone(copied([], [(1, "F1")]))

    def test_fan_before_t0_cutoff_gives_none(self):
        self.assertIsNone(copied([(3, "F1")], [(4, "F1")]))

    def test_copy_after_t0_cutoff(self):
        self.assertEqual(copied([(2, "F1")], [(3, "F1")]), 1)

    def test_no_shortcut_on_later_matching_star_buy(self):
        self.assertEqual(copied([(0, "F1"), (2, "F2")], [(1, "F2")]), 0)


if __name__ == "__main__":
    unittest.main()
