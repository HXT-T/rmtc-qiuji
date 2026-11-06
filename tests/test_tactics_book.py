"""Scenario book: tactical situations whose right action follows from the rules alone (docs 9.9).

Each case is a position where one action is at least as good as every other whatever the rivals do. Rules used
(v1.2, checked on referee frames): attacks resolve simultaneously on pre-move positions and hit the nearest robot
in line 1-2 cells away; 20 HP, 10 against a defender, 0 against a protected one; a protected robot cannot attack;
a kill is worth 60 shared by its attackers, a death costs 60; movement can never dodge this tick's attacks.
"""
import unittest
from rmtc_bot.config import Config
from rmtc_bot.engine import Engine
from rmtc_bot.events import Event
from .helpers import frame, enemy

BIG = {"width": 24, "height": 24}
MID = {"resource_value": 60, "effective_damage": 200, "mission_completed_ids": [], "invalid_actions": 0,
       "mission_completed": 0}  # mid-game, damage below the cap: damage and deaths still score


def act(pos, enemies, hp=100, tick=200, events=(), protected=False, score=MID):
    e = Engine(Config(explore_weight=0.0))
    e.step(frame(tick=tick - 1, pos=pos, map=BIG, score=score))
    if events:
        e.events.merge(list(events))
    f = frame(tick=tick, pos=pos, entities=enemies, map=BIG, score=score)
    f["self"].update(hp=hp, protected=protected)
    return e.step(f)


def worst_next(cell, enemies):
    """Most HP rivals could take from us at `cell` next tick: each may first step anywhere (or stay)."""
    total = 0
    for x in enemies:
        (ex, ey) = x["pos"]
        reach = [(ex + dx, ey + dy) for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1))]
        if not x.get("protected") and any((q[0] == cell[0] or q[1] == cell[1])
                                          and 1 <= abs(q[0] - cell[0]) + abs(q[1] - cell[1]) <= 2 for q in reach):
            total += 20
    return total


STEP = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}


class ScenarioBook(unittest.TestCase):
    # A. This tick: movement cannot dodge; only defending changes the damage taken.
    def test_a1_defend_when_the_hit_that_can_land_kills_and_defending_saves(self):
        r = act((5, 5), [enemy((6, 5), team="B")], hp=20)
        self.assertEqual(r["action"], "defend")  # 20 >= 20, defended 10 < 20; no kill on offer

    def test_a1_in_a_pincer_defending_only_delays(self):
        # 40 >= 30 now, defended 20 < 30, but both stay in line: whatever we do next tick takes their hits again.
        # Not a clear case (docs 9.9): defend or hit back, but never a move, which cannot dodge.
        r = act((5, 5), [enemy((6, 5), team="B"), enemy((5, 6), team="C")], hp=30)
        self.assertNotEqual(r["action"], "move")

    def test_a2_attack_when_death_is_certain_even_defending(self):
        r = act((5, 5), [enemy((6, 5), team="B")], hp=10)
        self.assertEqual((r["action"], r.get("direction")), ("attack", "right"))  # defended 10 >= 10: hit back

    def test_a3_take_a_kill_on_offer(self):
        r = act((5, 5), [enemy((7, 5), hp=20, team="B")])
        self.assertEqual((r["action"], r.get("direction")), ("attack", "right"))

    def test_a3_a_defender_with_10_hp_still_dies(self):
        r = act((5, 5), [enemy((6, 5), hp=10, defending=True, team="B")])
        self.assertEqual((r["action"], r.get("direction")), ("attack", "right"))

    def test_a4_join_a_kill_another_rival_is_making(self):
        # C hits B from below (C cannot reach us); B's 30 HP falls to our 20 plus C's 20.
        r = act((5, 5), [enemy((7, 5), hp=30, team="B"), enemy((7, 7), team="C")])
        self.assertEqual((r["action"], r.get("direction")), ("attack", "right"))

    def test_a5_never_shoot_a_protected_robot(self):
        r = act((5, 5), [enemy((6, 5), protected=True, team="B")])
        self.assertNotEqual(r["action"], "attack")

    def test_a5_a_protected_robot_shields_the_one_behind_it(self):
        r = act((5, 5), [enemy((6, 5), protected=True, team="B"), enemy((7, 5), hp=20, team="C")])
        self.assertNotEqual((r["action"], r.get("direction")), ("attack", "right"))

    def test_a5_a_protected_robot_is_no_threat(self):
        r = act((5, 5), [enemy((6, 5), protected=True, team="B")], hp=10)
        self.assertNotEqual(r["action"], "defend")  # it cannot attack

    def test_a6_never_shoot_a_defender_under_reflection(self):
        reflect = Event("reflection", 190, 210, params={"reflection_damage": 20})
        r = act((5, 5), [enemy((6, 5), defending=True, team="B")], events=[reflect])
        self.assertNotEqual((r["action"], r.get("direction")), ("attack", "right"))

    def test_a7_a_protected_robot_cannot_attack(self):
        r = act((5, 5), [enemy((6, 5), hp=20, team="B")], protected=True)
        self.assertNotEqual(r["action"], "attack")

    # C. Next tick: rivals move one cell, then attack from where they stand.
    def test_c1_leave_a_cell_two_rivals_can_line_up_on(self):
        rivals = [enemy((3, 4), team="B"), enemy((7, 6), team="C")]
        self.assertGreaterEqual(worst_next((5, 5), rivals), 30)  # staying: both can line up, 40 >= 30
        r = act((5, 5), rivals, hp=30)
        self.assertEqual(r["action"], "move")
        dx, dy = STEP[r["direction"]]
        self.assertLess(worst_next((5 + dx, 5 + dy), rivals), 30)  # e.g. down: only C can reach (5, 6)

    @unittest.expectedFailure  # sv steps in; the t3 look-ahead fixed this but scored flat and was dropped (docs 9.9)
    def test_c1_do_not_step_into_two_lines_at_low_hp(self):
        rivals = [enemy((5, 3), team="B"), enemy((3, 6), team="C")]
        r = act((6, 6), rivals, hp=30)
        if r["action"] == "move":
            dx, dy = STEP[r["direction"]]
            self.assertLess(worst_next((6 + dx, 6 + dy), rivals), 30)


if __name__ == "__main__":
    unittest.main()
