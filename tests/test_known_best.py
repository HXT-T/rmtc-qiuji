"""Steps a third party can call wrong from what we knew (docs 9.12): undoing last tick's step, defending in
place again under the same fire, and giving up a goal because a robot stands in the corridor for now."""
import unittest
from rmtc_bot.config import Config
from rmtc_bot.engine import Engine
from rmtc_bot.protocol import Frame
from .helpers import frame, enemy, resource

BIG = {"width": 24, "height": 24}


class NoDithering(unittest.TestCase):
    def go(self, last_goal):
        # Last tick we stepped from (4, 5) to (5, 5) towards (9, 5); now (1, 5) is worth more than (9, 5).
        e = Engine(Config(explore_weight=0.0))
        e.step(frame(tick=9, pos=(4, 5), map=BIG))
        e.planner.goal = last_goal
        f = frame(tick=10, pos=(5, 5), map=BIG,
                  entities=[resource((1, 5), value=20, id="a"), resource((9, 5), value=10, id="b")])
        return e.step(f)["direction"]

    def test_turning_back_is_the_plain_choice_without_a_plan(self):
        self.assertEqual(self.go(None), "left")

    def test_no_step_back_while_last_ticks_target_is_still_there(self):
        self.assertEqual(self.go((9, 5)), "right")


class RobotsMove(unittest.TestCase):
    def test_a_robot_down_the_corridor_is_a_detour_not_a_wall(self):
        # A dead-end corridor x = 5, y = 6..9 behind walls; a rival stands at (5, 8), the resource is at (5, 9).
        e = Engine(Config(explore_weight=0.0))
        e.step(frame(tick=9, pos=(5, 5), map=BIG))
        e.planner.memory.obstacles |= {(x, y) for x in (4, 6) for y in range(6, 11)} | {(5, 10)}
        f = frame(tick=10, pos=(5, 5), entities=[enemy((5, 8), team="C"), resource((5, 9))], map=BIG,
                  missions=[{"id": "v", "type": "resource_value", "target": 999}])
        f = Frame.parse(f, "A")
        _, steps, _ = e.planner.distances((5, 5), f, e.events, {(5, 8)}, True)
        self.assertEqual(steps.get((5, 9)), 4)  # through (5, 8): the rival will have moved by then
        _, steps, _ = e.planner.distances((5, 7), f, e.events, {(5, 8)}, True)
        self.assertNotIn((5, 8), steps)  # our next step: that cell is held this tick


class NoRepeatedDefend(unittest.TestCase):
    """The 9.11 match, ticks 80-82: C steps into our line two cells below; we defend; it stays and keeps firing.
    A mission is still open there, so the one-on-one table (`_duel`) is off."""

    def play(self, *hps):
        e = Engine(Config(explore_weight=0.0))
        for i, hp in enumerate(hps):
            f = frame(tick=79 + i, pos=(5, 5), entities=[enemy((5, 8) if i == 0 else (5, 7), team="C")], map=BIG,
                      missions=[{"id": "v", "type": "resource_value", "target": 999}])
            f["self"]["hp"] = hp
            yield e.step(f)["action"]

    def test_defend_once_then_stop_bleeding(self):
        self.assertEqual(list(self.play(80, 80, 70))[1:], ["defend", "attack"])

    def test_no_defend_after_a_trade_either(self):
        # 9.11 rerun: defend, attack, defend, ... loses 30 HP per 20 dealt; trading every tick loses 20 per 20.
        self.assertEqual(list(self.play(100, 100, 90, 70))[1:], ["defend", "attack", "attack"])

    def test_defend_again_when_a_full_hit_kills(self):
        self.assertEqual(list(self.play(30, 30, 20))[1:], ["defend", "defend"])


if __name__ == "__main__":
    unittest.main()
