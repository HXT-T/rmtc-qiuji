"""Decision audit against referee truth: resources a rival took although we had seen them while we
were strictly nearest of all robots by the true path around walls. Every such case is a decision a
person would call absurd, so it is counted per match on every batch, not only spotted by eye.

  python3 eval/audit.py RUN_DIR:BOT [...]       per batch: cases per match and value lost, worst cells
"""
import json
import sys
from collections import deque


def paths(start, width, height, walls):
    dist, queue = {start: 0}, deque([start])
    while queue:
        x, y = queue.popleft()
        for q in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= q[0] < width and 0 <= q[1] < height and q not in walls and q not in dist:
                dist[q] = dist[(x, y)] + 1
                queue.append(q)
    return dist


def match(row, bot):
    """[(resource, value, tick taken, rival, first tick we saw it nearest)] of one match."""
    world = json.load(open(f"{row['out']}/config.json"))["map"]
    walls = {tuple(o) for o in world["obstacles"]}
    resources = {tuple(r["position"]): r["value"] for r in world["resources"]}
    me = next(t["team"] for t in row["teams"] if t["bot"] == bot)
    where, takes = {}, {}  # team -> {tick: position}; resource -> (tick, team)
    for t in row["teams"]:
        try:
            log = [json.loads(line) for line in open(f"{row['out']}/bot_{t['team']}.jsonl")]
        except FileNotFoundError:
            continue
        decisions = [r for r in log if r["kind"] == "decision"]
        where[t["team"]] = {r["tick"]: tuple(r["position"]) for r in decisions}
        for a, b in zip(decisions, decisions[1:]):
            p = tuple(b["position"])
            if b["resource_value"] > a["resource_value"] and p in resources:
                takes.setdefault(p, (b["tick"], t["team"]))
    w, h, cache = world["width"], world["height"], {}
    dist = lambda q: cache.setdefault(q, paths(q, w, h, walls))
    out = []
    for p, (taken, team) in takes.items():
        if team == me:
            continue
        for tick in range(taken):
            mine = where[me].get(tick)
            if mine is None or max(abs(mine[0] - p[0]), abs(mine[1] - p[1])) > 4:
                continue  # not in our vision window
            ours = dist(mine).get(p, 10 ** 6)
            theirs = min((dist(pos[tick]).get(p, 10 ** 6) for t, pos in where.items() if t != me and tick in pos),
                         default=10 ** 6)
            if ours < theirs and ours < taken - tick:
                out.append((p, resources[p], taken, team, tick))
                break
    return out


if __name__ == "__main__":
    for spec in sys.argv[1:]:
        run, bot = spec.rsplit(":", 1)
        rows = [r for r in map(json.loads, open(f"{run}/results.jsonl")) if r.get("ok") and not r.get("screened")]
        cells = {(str(r["seed"]), r["rotation"]): match(r, bot) for r in rows}
        n = sum(len(c) for c in cells.values())
        lost = sum(v for c in cells.values() for _, v, *_ in c)
        print(f"{spec}: {len(rows)} matches, {n / len(rows):.2f} cases/match, {lost / len(rows):.2f} value lost/match")
        for cell, cases in sorted(cells.items(), key=lambda kv: -sum(c[1] for c in kv[1]))[:5]:
            if cases:
                print("  ", cell, [(p, v, f"seen nearest t{s}", f"taken t{t} by {team}") for p, v, t, team, s in cases])
