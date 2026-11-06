import unittest
from rmtc_bot.config import Config
from rmtc_bot.engine import Engine
from rmtc_bot.planner import TAKE, KNOWN
from .helpers import frame, enemy, resource

BIG = {"width": 24, "height": 24}


def keep(*frames):
    """Our chance to find the resource at (6, 8) when we get there, after the given frames."""
    e = Engine(Config(explore_weight=0.0))
    for f in frames:
        e.step(f)
    enemies = [x for x in e.latest.entities if x["type"] == "robot"]
    arrivals = e.planner._arrivals(e.latest, e.events, enemies)
    steps = e.planner.distances(e.latest.position, e.latest, e.events)[1]
    return e.planner._keep((6, 8), steps[(6, 8)], arrivals), [state for state, *_ in arrivals]


class SurvivalTests(unittest.TestCase):
    def test_no_rival_can_arrive_first_means_it_is_ours(self):
        rivals = [enemy((6, 2), team="B"), enemy((2, 6), team="C"), enemy((10, 10), team="D")]
        p, states = keep(frame(tick=60, pos=(6, 6), entities=[*rivals, resource((6, 8))], map=BIG))
        self.assertEqual(states, [0, 0, 0])
        self.assertEqual(p, 1.0)  # each is 6 steps away, we are 2

    def test_a_nearer_visible_rival_takes_it_at_its_measured_rate(self):
        rivals = [enemy((6, 9), team="B"), enemy((2, 6), team="C"), enemy((10, 10), team="D")]
        p, _ = keep(frame(tick=60, pos=(6, 6), entities=[*rivals, resource((6, 8))], map=BIG))
        self.assertAlmostEqual(p, 1 - KNOWN[0])  # B is 1 step away (slack 1) and its view holds it

    def test_a_rival_that_saw_it_and_could_have_walked_there_probably_took_it(self):
        seen = frame(tick=20, pos=(6, 4), entities=[enemy((3, 7), team="B"), resource((6, 8))], map=BIG)
        p, states = keep(seen, frame(tick=40, pos=(6, 4), entities=[resource((6, 8))], map=BIG))
        self.assertEqual(states, [1, 2, 2])
        # B saw it 20 ticks ago, 4 steps away: it could have been there long since; we need 4 steps (slack 4).
        self.assertAlmostEqual(p, (1 - KNOWN[1]) * (1 - TAKE[2][1]) ** 2)

    def test_an_unseen_rival_may_be_anywhere_it_could_have_walked(self):
        seen = frame(tick=0, pos=(6, 6), entities=[enemy((2, 2), team="B"), resource((6, 8))], map=BIG)
        p, states = keep(seen, frame(tick=10, pos=(6, 6), entities=[resource((6, 8))], map=BIG))
        self.assertEqual(states, [1, 2, 2])
        # B was 10 steps away 10 ticks ago, so it could be there now, but its view never held (6, 8);
        # C and D could be anywhere by tick 10.
        self.assertAlmostEqual(p, (1 - TAKE[1][0]) * (1 - TAKE[2][0]) ** 2)
        early, _ = keep(frame(tick=0, pos=(6, 6), entities=[enemy((2, 2), team="B"), resource((6, 8))], map=BIG))
        self.assertEqual(early, 1.0)  # at tick 0 nobody can be nearer than their spawn allows


if __name__ == "__main__":
    unittest.main()
