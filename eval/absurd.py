"""Search a batch for decisions a person would call absurd, per match, on referee truth (every robot, so our bot
compares with the opponents) and on our own decision log (our bot only). Each class names its test.

Truth, every robot:
  deaths, of which within 5 ticks of a respawn; mutual kills
  standoff     >= 10 ticks without moving with a rival within 2 steps, nobody hit
  back-forth   ticks alternating between two cells (runs >= 4)
  hits taken / damage dealt per death
Our log only:
  whiff        ATTACK with no rival 1-2 cells in that direction (pre-move truth)
  idle defend  DEFEND although no rival could hit us (in line, not protected)
  failed move  MOVE the referee did not execute (blocked or contested)
  exposed exit left the spawn while protected, into a cell a rival hit next tick

  python3 eval/absurd.py RUN_DIR:BOT
"""
import glob
import json
import sys
from collections import Counter, defaultdict

STEP = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}


def lined(a, b):
    return (a[0] == b[0] or a[1] == b[1]) and 1 <= abs(a[0] - b[0]) + abs(a[1] - b[1]) <= 2


def runs(ticks, least):
    ticks, out, start = sorted(ticks), [], None
    for i, t in enumerate(ticks):
        if start is None or t != ticks[i - 1] + 1:
            if start is not None:
                out.append((start, ticks[i - 1]))
            start = t
    if start is not None:
        out.append((start, ticks[-1]))
    return [(a, b) for a, b in out if b - a + 1 >= least]


def main(spec):
    run, bot = spec.rsplit(":", 1)
    per, ours, n = defaultdict(Counter), Counter(), 0
    for row in map(json.loads, open(f"{run}/results.jsonl")):
        n += 1
        names = {t["team"]: t["bot"] for t in row["teams"]}
        me = next(t for t, b in names.items() if b == bot)
        fr = {f["tick"]: f for f in (json.loads(l) for l in open(glob.glob(row["out"] + "/referee/*/frames.jsonl")[0]))}
        ticks = sorted(fr)
        walls = {tuple(o) for o in json.load(open(f"{row['out']}/config.json"))["map"]["obstacles"]}
        state = {t: {r["team"]: r for r in fr[t]["map_view"]["robots"]} for t in ticks}
        for team in names:
            name = "us" if team == me else names[team]
            c = per[name]
            alive = {t: state[t][team]["alive"] for t in ticks if team in state[t]}
            pos = {t: tuple(state[t][team]["position"]) for t in ticks if alive.get(t)}
            hp = {t: state[t][team]["hp"] for t in ticks if team in state[t]}
            respawn = [t for t in ticks[1:] if alive.get(t) and not alive.get(t - 1, True)]
            for t in ticks[1:]:
                if alive.get(t - 1) and not alive.get(t):
                    c["deaths"] += 1
                    if any(0 <= t - r <= 5 for r in respawn):
                        c["deaths within 5 ticks of respawn"] += 1
                    if any(o != team and state[t - 1].get(o, {}).get("alive") and not state[t].get(o, {}).get("alive", True)
                           for o in names):
                        c["mutual kill (a rival died the same tick)"] += 1
                if alive.get(t - 1) and hp.get(t - 1, 0) > (hp.get(t, 0) if alive.get(t) else 0):
                    c["hits taken"] += 1
            still = [t for t in pos if t - 1 in pos and pos[t] == pos[t - 1]
                     and any(o != team and t in state and state[t].get(o, {}).get("alive")
                             and abs(state[t][o]["position"][0] - pos[t][0]) + abs(state[t][o]["position"][1] - pos[t][1]) <= 2
                             for o in names)
                     and hp.get(t) == hp.get(t - 1)]
            c["standoff ticks (>= 10, rival within 2, unhurt)"] += sum(b - a + 1 for a, b in runs(still, 10))
            osc = [t for t in pos if t - 1 in pos and t - 2 in pos and pos[t] == pos[t - 2] != pos[t - 1]]
            c["back-and-forth ticks"] += sum(b - a + 1 for a, b in runs(osc, 4))
        final = {t["team"]: t for t in row["teams"]}
        for team in names:
            per["us" if team == me else names[team]]["effective damage"] += final[team]["effective_damage"]
        # Our own decisions, judged on the referee's pre-move state of the same tick.
        log = [json.loads(l) for l in open(f"{row['out']}/bot_{me}.jsonl")]
        receipts = {r["last_action"]["tick"]: r["last_action"] for r in log if r["kind"] == "receipt" and r.get("last_action")}
        for d in (r for r in log if r["kind"] == "decision"):
            t, a = d["tick"], d["action"]
            snap = fr.get(t + 1)  # referee frame t is the state before action tick t, frame t+1 the state after
            before = state.get(t)
            if not before or not before.get(me, {}).get("alive"):
                continue
            here = tuple(before[me]["position"])
            rivals = [r for o, r in before.items() if o != me and r["alive"]]
            if a["action"] == "attack" and a.get("direction") in STEP:
                dx, dy = STEP[a["direction"]]
                if not any(tuple(r["position"]) in ((here[0] + dx, here[1] + dy), (here[0] + 2 * dx, here[1] + 2 * dy))
                           for r in rivals):
                    ours["whiff (attack hit nobody)"] += 1
            if a["action"] == "defend" and not any(lined(tuple(r["position"]), here) for r in rivals):
                ours["idle defend (nobody could hit us)"] += 1
            got = receipts.get(t)
            if a["action"] == "move" and got and not got.get("moved") and a.get("direction") in STEP:
                dx, dy = STEP[a["direction"]]
                q = here[0] + dx, here[1] + dy
                after = state.get(t + 1, {})
                if any(tuple(r["position"]) == q for r in rivals):
                    why = "into a rival's cell"
                elif any(abs(r["position"][0] - q[0]) + abs(r["position"][1] - q[1]) == 1 for r in rivals):
                    why = "contested: a rival next to the target cell tried too"
                elif q in walls:
                    why = "into a wall"
                else:
                    why = "other"
                ours["failed move: " + why] += 1
            if t + 1 in state and before[me]["alive"] and not state[t + 1].get(me, {}).get("alive", True):
                d0 = next(x["effective_damage"] for x in fr[t]["teams"] if x["team"] == me)
                for o in names:
                    if o == me:
                        continue
                    k0 = next(x["effective_damage"] for x in fr[t]["teams"] if x["team"] == o)
                    k1 = next(x["effective_damage"] for x in fr[t + 1]["teams"] if x["team"] == o)
                    end = next(x["effective_damage"] for x in fr[max(fr)]["teams"] if x["team"] == o)
                    if k1 - k0 >= 30 and end - (k1 - k0) < 500:  # its share of our kill raised its final, capped score
                        ours["our deaths that fed a rival's final score"] += 1
                        ours["points fed to rivals by our deaths"] += 15 * (min(end, 500) - min(end - (k1 - k0), 500)) / 500
                ours["our deaths with our damage >= 560 (cost us nothing)"] += d0 >= 560
                ours["our deaths that dropped us below 500"] += 500 <= d0 < 560
            if a["action"] == "move" and d.get("hp") and snap and t + 2 in fr:
                mine = state[t].get(me, {})
                if mine.get("protected") or False:
                    after = state.get(t + 2, {}).get(me, {})
                    if after.get("alive") is False or after.get("hp", 100) < state[t + 1].get(me, {}).get("hp", 100):
                        ours["exposed exit (left protection, hit next tick)"] += 1
            if d.get("reason") in ("engage",) and d["effective_damage"] >= 500:
                ours["engage after our damage cap (ticks)"] += 1
            if a["action"] == "wait" and not rivals_near(rivals, here) and t < 450:
                ours["wait with no rival within 4 (ticks)"] += 1
    print(f"{spec}: {n} matches, per match")
    keys = sorted({k for c in per.values() for k in c})
    print(f"  {'':<48}" + "".join(f"{name:>10}" for name in sorted(per)))
    for k in keys:
        print(f"  {k:<48}" + "".join(f"{per[name][k] / n:10.2f}" for name in sorted(per)))
    for k, v in sorted(ours.items()):
        print(f"  ours: {k:<56} {v / n:8.2f}")


def rivals_near(rivals, here):
    return any(abs(r["position"][0] - here[0]) + abs(r["position"][1] - here[1]) <= 4 for r in rivals)


if __name__ == "__main__":
    for spec in sys.argv[1:]:
        main(spec)
