"""Resource headroom of a batch, from the referee's per-tick frames and per-seat perceptions.

For every seat of every match:
- ceiling: best collection from spawn with full knowledge, each resource vanishing at the
  tick someone else actually took it (others' play fixed; exact label-setting search);
- of the resources others took that this seat could have reached first from its spawn:
  never seen / seen too late / seen in time but not taken.

  python3 eval/headroom.py --bot ~/rmtc-eval/versions/v6 ~/rmtc-eval/runs/v6
"""
import argparse
import collections
import glob
import json
import sys
from collections import deque
from pathlib import Path


def bfs(obstacles, start, n=24):
    dist, queue = {start: 0}, deque([start])
    while queue:
        x, y = queue.popleft()
        for q in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= q[0] < n and 0 <= q[1] < n and q not in obstacles and q not in dist:
                dist[q] = dist[(x, y)] + 1
                queue.append(q)
    return dist


def ceiling(start, items, dist):
    """items: {pos: (value, deadline)}; each stop must be reached strictly before its deadline."""
    labels, best = {(frozenset(), start): 0}, 0
    while labels:
        nxt = {}
        for (taken, last), time in labels.items():
            for p, (value, deadline) in items.items():
                t = time + dist[last].get(p, 10 ** 6)
                if p not in taken and t < deadline and t < nxt.get((taken | {p}, p), 10 ** 6):
                    nxt[(taken | {p}, p)] = t
        best = max([best] + [sum(items[q][0] for q in taken) for taken, _ in nxt])
        labels = nxt
    return best


def analyse(run, Frame):
    frames = [json.loads(line) for line in open(glob.glob(f"{run}/referee/*/frames.jsonl")[0])]
    obstacles = {tuple(p) for p in frames[0]["map_view"]["obstacles"]}
    gone, prev = {}, None  # pos -> (value, tick taken, team)
    for f in frames:
        res = {tuple(r["position"]): r["value"] for r in f["map_view"]["resources"]}
        pos = {r["team"]: tuple(r["position"]) for r in f["map_view"]["robots"]}
        for p, v in (prev or {}).items():
            if p not in res:
                gone[p] = (v, f["tick"], next((t for t, q in pos.items() if q == p), "?"))
        prev = res
    first_seen = collections.defaultdict(dict)  # team -> pos -> (tick, own position)
    for line in open(glob.glob(f"{run}/referee/*/communication.jsonl")[0]):
        d = json.loads(line)
        if d["kind"] == "perception":
            frame = Frame.parse(d["data"], d["seat"])
            for e in frame.entities:
                if e["type"].startswith("resource"):
                    first_seen[d["seat"]].setdefault(e["pos"], (frame.tick, frame.position))
    bots = {t["team"]: t["bot"] for t in json.load(open(f"{run}/result.json"))["teams"]}
    dist = {p: bfs(obstacles, p) for p in gone}
    rows = []
    for robot in json.load(open(f"{run}/config.json"))["robots"]:
        team, spawn = robot["team"], tuple(robot["spawn"])
        home = dist[spawn] = bfs(obstacles, spawn)
        row = collections.Counter(bot=0)
        items = {p: (v, t if who != team else 10 ** 6) for p, (v, t, who) in gone.items()}
        row["ceiling"] = ceiling(spawn, items, dist)
        for p, (v, t, who) in gone.items():
            seen = first_seen[team].get(p)
            if who == team:
                row["taken"] += v
            elif home.get(p, 10 ** 6) < t:
                key = ("never_seen" if not seen else "seen_in_time_not_taken"
                       if seen[0] + bfs(obstacles, seen[1]).get(p, 10 ** 6) < t else "seen_too_late")
                row[key] += v
        rows.append((bots[team], row))
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bot", required=True, help="directory with an rmtc_bot package (perception parser)")
    parser.add_argument("batch", help="batch output directory")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.bot).expanduser()))
    from rmtc_bot.protocol import Frame
    totals = collections.defaultdict(collections.Counter)
    runs = [r for r in sorted(glob.glob(f"{args.batch}/m*"))  # finished matches only
            if json.load(open(f"{r}/result.json")).get("ok") and glob.glob(f"{r}/referee/*/frames.jsonl")]
    for run in runs:
        for bot, row in analyse(run, Frame):
            totals[bot].update(row)
            totals[bot]["seats"] += 1
    keys = ("taken", "ceiling", "never_seen", "seen_too_late", "seen_in_time_not_taken")
    print(f"{'bot':<10}{'seats':>6}" + "".join(f"{k:>24}" for k in keys) + "   (per seat)")
    for bot, t in sorted(totals.items()):
        print(f"{bot:<10}{t['seats']:>6}" + "".join(f"{t[k] / t['seats']:>24.1f}" for k in keys))


if __name__ == "__main__":
    main()
