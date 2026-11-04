"""Known-map mode (stage 1, self-study only): plan the exact optimal collection route
from a full map file. Using map truth breaks rules §12 / P2 in a real contest, so this
is off unless `route_map` is configured."""
import json
from collections import deque
from pathlib import Path

DIRS = {"up": (0, -1), "right": (1, 0), "down": (0, 1), "left": (-1, 0)}


def load(path):
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    m = config["map"]
    return dict(width=m["width"], height=m["height"], radius=config.get("vision_radius", 4),
                obstacles={tuple(p) for p in m["obstacles"]},
                resources={tuple(r["position"]): (r["id"], r["value"]) for r in m["resources"]},
                spawn=tuple(config["robots"][0]["spawn"]))


def bfs(world, start):
    dist, parent, queue = {start: 0}, {}, deque([start])
    while queue:
        p = queue.popleft()
        for dx, dy in DIRS.values():
            q = (p[0] + dx, p[1] + dy)
            if (0 <= q[0] < world["width"] and 0 <= q[1] < world["height"]
                    and q not in world["obstacles"] and q not in dist):
                dist[q], parent[q] = dist[p] + 1, p
                queue.append(q)
    return dist, parent


def optimum(world, budget=100):
    """Best collection route on a fully known map from the spawn."""
    points = [world["spawn"]] + sorted(world["resources"])
    values = [world["resources"][p][1] for p in points[1:]]
    dist = [bfs(world, p)[0] for p in points]
    d = [[dist[i].get(points[j], 10 ** 9) for j in range(len(points))] for i in range(len(points))]
    value, moves, order = solve(d, values, budget)
    return dict(value=value, moves=moves, order=[points[i + 1] for i in order], total_value=sum(values))


def solve(d, values, budget):
    """Exact bitmask DP: d[0] are steps from the start, d[i + 1] from item i.
    best[mask][i] = fewest moves that collect `mask` and end on item i.
    Returns (highest value, then fewest moves, item order)."""
    n = len(values)
    inf = 10 ** 9
    best = [[inf] * n for _ in range(1 << n)]
    parent = {}
    for i in range(n):
        if d[0][i + 1] <= budget:
            best[1 << i][i] = d[0][i + 1]
    mask_value = [0] * (1 << n)
    for mask in range(1, 1 << n):
        low = (mask & -mask).bit_length() - 1
        mask_value[mask] = mask_value[mask & (mask - 1)] + values[low]
    top = (0, 0, 0, -1)
    for mask in range(1, 1 << n):
        row = best[mask]
        for i in range(n):
            t = row[i]
            if t > budget:
                continue
            if (mask_value[mask], -t) > top[:2]:
                top = (mask_value[mask], -t, mask, i)
            di = d[i + 1]
            for j in range(n):
                if mask >> j & 1:
                    continue
                nt = t + di[j + 1]
                nm = mask | 1 << j
                if nt <= budget and nt < best[nm][j]:
                    best[nm][j] = nt
                    parent[(nm, j)] = i
    value, neg_t, mask, last = top
    order = []
    while last >= 0:
        order.append(last)
        previous = parent.get((mask, last), -1)
        mask &= ~(1 << last)
        last = previous
    order.reverse()
    return value, -neg_t, order


class KnownRoute:
    """Follow the precomputed order; BFS on the known terrain from the actual position
    each tick, and drop a target once our own vision shows it is not there."""

    def __init__(self, path, budget=100):
        self.world = load(path)
        self.plan = optimum(self.world, budget)
        self.order = list(self.plan["order"])

    def direction(self, frame):
        visible_resources = {e["pos"] for e in frame.entities if e["type"].startswith("resource_")}
        r = frame.radius
        while self.order:
            target = self.order[0]
            seen = max(abs(target[0] - frame.position[0]), abs(target[1] - frame.position[1])) <= r
            if target == frame.position or (seen and target not in visible_resources):
                self.order.pop(0)  # collected, or the map file disagrees with what we see
                continue
            break
        if not self.order:
            return None
        dist, parent = bfs(self.world, frame.position)
        target = self.order[0]
        if target not in dist:
            self.order.pop(0)
            return None
        step = target
        while parent[step] != frame.position:
            step = parent[step]
        delta = step[0] - frame.position[0], step[1] - frame.position[1]
        return next(k for k, v in DIRS.items() if v == delta)
