"""Exact solution of the simplest stage-2 tactical case: one enemy, open ground, no events.

State: enemy offset from us (Manhattan 1..WINDOW) and both HPs (multiples of 10). Each tick is a
simultaneous matrix game (rules 5.2: attacks on pre-move positions, then moves; a move fails into a
cell held at the start of the move, into a contested cell, or as a swap). The duel is local but the
match has four teams: our own damage change (dealt, +60 per kill, -60 per death) always counts for
us (score below the cap, tie-break above it), while the partner's counts against us only when it is
the team we must beat, one of three rivals with no scoreboard: payoff = ours - RIVAL * theirs. We
play maximin against this payoff. The duel ends at a death or when the offset leaves the window;
later ticks are discounted by GAMMA. Solved by Shapley value iteration per HP pair (HP never rises),
each stage game by linear programming.

  python3 eval/duel.py OUT.json        (inside WSL: needs numpy and scipy)

OUT.json keeps values and policies of every state (for analysis); rmtc_bot/duel.json keeps only the
policies within BOT_RANGE steps, which is what the bot looks up.
"""
import json
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from scipy.optimize import linprog

WINDOW, GAMMA, HIT, DEFENDED, DEATH, RIVAL, BOT_RANGE = 6, 0.97, 20, 10, 60, 1 / 3, 4
MOVES = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
OFFSETS = [(x, y) for x in range(-WINDOW, WINDOW + 1) for y in range(-WINDOW, WINDOW + 1)
           if 0 < abs(x) + abs(y) <= WINDOW]
HPS = range(10, 101, 10)


def inline(off):
    return (off[0] == 0 or off[1] == 0) and 1 <= abs(off[0]) + abs(off[1]) <= 2


def actions(off):
    return ["wait", "defend"] + (["attack"] if inline(off) else []) + list(MOVES)


def step(off, ha, hb, a, b):
    """-> (reward to us, next state or None when the duel ends)."""
    da = (DEFENDED if b == "defend" else HIT) if a == "attack" else 0
    db = (DEFENDED if a == "defend" else HIT) if b == "attack" else 0
    ours, theirs = min(hb, da), min(ha, db)
    ha, hb = ha - db, hb - da
    ours += DEATH * ((hb <= 0) - (ha <= 0))
    theirs += DEATH * ((ha <= 0) - (hb <= 0))
    reward = ours - RIVAL * theirs
    if ha <= 0 or hb <= 0:
        return reward, None
    ta = MOVES.get(a, (0, 0))
    tb = (off[0] + MOVES.get(b, (0, 0))[0], off[1] + MOVES.get(b, (0, 0))[1])
    ok_a = a in MOVES and ta != off and ta != tb
    ok_b = b in MOVES and tb != (0, 0) and ta != tb
    new = ((tb if ok_b else off)[0] - (ta if ok_a else (0, 0))[0], (tb if ok_b else off)[1] - (ta if ok_a else (0, 0))[1])
    return reward, ((new, ha, hb) if 0 < abs(new[0]) + abs(new[1]) <= WINDOW else None)


def solve_game(m):
    """Value and our maximin mixed strategy of payoff matrix m (rows: ours)."""
    rows, cols = m.shape
    # variables x_1..x_rows, v: maximise v s.t. v <= x . m[:, j] for every column j
    res = linprog(np.r_[np.zeros(rows), -1.0], A_ub=np.c_[-m.T, np.ones(cols)], b_ub=np.zeros(cols),
                  A_eq=np.r_[np.ones(rows), 0.0][None, :], b_eq=[1.0],
                  bounds=[(0, None)] * rows + [(None, None)], method="highs")
    return -res.fun, res.x[:rows]


def solve_pair(args):
    ha, hb, lower = args
    value = {off: 0.0 for off in OFFSETS}
    for _ in range(400):
        change, policy = 0.0, {}
        for off in OFFSETS:
            mine, theirs = actions(off), actions((-off[0], -off[1]))
            m = np.empty((len(mine), len(theirs)))
            for i, a in enumerate(mine):
                for j, b in enumerate(theirs):
                    r, nxt = step(off, ha, hb, a, b)
                    if nxt is not None:
                        noff, nha, nhb = nxt
                        r += GAMMA * (value[noff] if (nha, nhb) == (ha, hb) else lower[f"{noff[0]},{noff[1]},{nha},{nhb}"])
                    m[i, j] = r
            v, x = solve_game(m)
            change = max(change, abs(v - value[off]))
            value[off], policy[off] = v, {a: round(float(p), 3) for a, p in zip(mine, x) if p > 1e-3}
        if change < 0.01:
            break
    return {f"{o[0]},{o[1]},{ha},{hb}": {"value": round(value[o], 2), "policy": policy[o]} for o in OFFSETS}


def main():
    table = {}
    # A pair's next states only have the same or lower HPs, so solve in order of total HP.
    for total in range(20, 201, 10):
        pairs = [(ha, total - ha) for ha in HPS if total - ha in HPS]
        lower = {k: v["value"] for k, v in table.items()}
        with Pool() as pool:
            for part in pool.map(solve_pair, [(ha, hb, lower) for ha, hb in pairs]):
                table.update(part)
        print(f"total HP {total}: {len(table)} states", flush=True)
    json.dump({"window": WINDOW, "gamma": GAMMA, "states": table}, open(sys.argv[1], "w"))
    near = {k: v["policy"] for k, v in table.items() if sum(map(abs, map(int, k.split(",")[:2]))) <= BOT_RANGE}
    bot = Path(__file__).resolve().parents[1] / "rmtc_bot" / "duel.json"
    bot.write_text(json.dumps(near, separators=(",", ":"), sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
