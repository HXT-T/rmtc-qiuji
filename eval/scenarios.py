"""Sweep random local fights and count actions that break a rule-derived clear answer (docs 9.9).

A position is local when 1-3 rivals stand within 4 steps on open ground. For each one the categories below decide
the answer from the rules alone (worst case: every rival that can hit us does); the program's action is checked
against them. Mid-game, damage below the cap, so damage and deaths score.

  python eval/scenarios.py [N] [SEED]
"""
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rmtc_bot.config import Config  # noqa: E402
from rmtc_bot.engine import Engine  # noqa: E402
from rmtc_bot.planner import between  # noqa: E402
from tests.helpers import frame, enemy  # noqa: E402

SIZE, ME = 24, (10, 10)
STEP = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
MID = {"resource_value": 60, "effective_damage": 200, "mission_completed_ids": [], "invalid_actions": 0,
       "mission_completed": 0}


def lined(a, b):
    return (a[0] == b[0] or a[1] == b[1]) and 1 <= abs(a[0] - b[0]) + abs(a[1] - b[1]) <= 2


def nearest_in(direction, rivals):
    dx, dy = STEP[direction]
    for d in (1, 2):
        hit = [r for r in rivals if r["pos"] == (ME[0] + dx * d, ME[1] + dy * d)]
        if hit:
            return hit[0]
    return None


def shooters(cell, rivals):
    """Rivals that can hit `cell` this tick: in line, not protected, not shielded by a nearer robot."""
    return [r for r in rivals if not r["protected"] and lined(r["pos"], cell)
            and not any(o is not r and between(r["pos"], o["pos"], cell) for o in rivals)]


def worst_next(cell, rivals):
    """Most HP rivals can take from us at `cell` next tick: each may first step one cell (or stay)."""
    total = 0
    for r in rivals:
        x, y = r["pos"]
        if any(lined((x + dx, y + dy), cell) for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1))):
            total += 20
    return total  # protection lasts 2 ticks: a protected rival may attack next tick


def judge(hp, rivals, action, direction, we_protected):
    """The clear-answer categories this position falls in, and whether the action breaks each."""
    out = []
    now = shooters(ME, rivals)
    incoming, defended = 20 * len(now), 10 * len(now)
    kills = [d for d in STEP if (t := nearest_in(d, rivals)) and not t["protected"]
             and t["hp"] <= (10 if t["defending"] else 20)]
    if we_protected:
        out.append(("A7 protected: never attack", action == "attack"))
        return out
    if direction in STEP and action == "attack":
        t = nearest_in(direction, rivals)
        if t is not None and t["protected"]:
            out.append(("A5 never shoot a protected robot", True))
    if incoming >= hp > defended and not kills:
        out.append(("A1 lethal now, defending saves: defend", action != "defend"))
    elif defended >= hp and any(nearest_in(d, rivals) and not nearest_in(d, rivals)["protected"] for d in STEP):
        out.append(("A2 death certain: attack", action != "attack"))
    elif kills and incoming < hp:
        out.append(("A3 kill on offer, not lethal to us: attack it", not (action == "attack" and direction in kills)))
    if incoming < hp and not kills:
        # Two ticks, worst case: this tick's hits (halved by defending, which keeps us in place), then the
        # next tick's line-ups on the cell we end on.
        end = ME if action != "move" else (ME[0] + STEP[direction][0], ME[1] + STEP[direction][1])
        moves = [(ME[0] + dx, ME[1] + dy) for dx, dy in STEP.values()
                 if not any(r["pos"] == (ME[0] + dx, ME[1] + dy) for r in rivals)]
        plans = [defended + worst_next(ME, rivals), incoming + worst_next(ME, rivals)]
        plans += [incoming + worst_next(c, rivals) for c in moves]
        taken = defended if action == "defend" else incoming
        if min(plans) < hp and action != "attack":  # an attack trades risk for damage: not a clear case
            pincer = worst_next(end, rivals) >= 40  # two or more rivals can line up on the cell we end on
            out.append((f"C1 two-tick worst case lethal, a safe plan exists ({'pincer' if pincer else 'one rival'})",
                        taken + worst_next(end, rivals) >= hp))
    return out


def main(n=3000, seed=1):
    rng = random.Random(seed)
    seen, broken, how = Counter(), Counter(), defaultdict(Counter)
    examples = {}
    for _ in range(n):
        k = rng.choice((1, 2, 2, 3))
        cells = [(ME[0] + dx, ME[1] + dy) for dx in range(-4, 5) for dy in range(-4, 5) if 0 < abs(dx) + abs(dy) <= 4]
        rivals = []
        for team, p in zip("BCD", rng.sample(cells, k)):
            rivals.append(dict(enemy(p, hp=rng.choice(range(10, 101, 10)), protected=rng.random() < 0.1,
                                     defending=rng.random() < 0.2, team=team)))
        hp = rng.choice(range(10, 101, 10))
        protected = rng.random() < 0.05
        e = Engine(Config(explore_weight=0.0))
        e.step(frame(tick=199, pos=ME, map={"width": SIZE, "height": SIZE}, score=MID))
        f = frame(tick=200, pos=ME, entities=rivals, map={"width": SIZE, "height": SIZE}, score=MID)
        f["self"].update(hp=hp, protected=protected)
        r = e.step(f)
        for name, bad in judge(hp, rivals, r["action"], r.get("direction"), protected):
            seen[name] += 1
            if bad:
                broken[name] += 1
                how[name][r["action"], "hp<=30" if hp <= 30 else "hp>30"] += 1
                examples.setdefault(name, (hp, [(x["pos"], x["hp"], x["defending"], x["protected"]) for x in rivals],
                                           r["action"], r.get("direction")))
    print(f"{n} random local fights")
    for name in sorted(seen):
        print(f"  {name:<66} {broken[name]:5d} / {seen[name]:5d} broken  {dict(how[name])}")
    for name, ex in examples.items():
        print(f"  e.g. {name[:2]}: our hp {ex[0]}, rivals (pos, hp, defending, protected) {ex[1]} -> {ex[2]} {ex[3]}")


if __name__ == "__main__":
    main(*map(int, sys.argv[1:]))
