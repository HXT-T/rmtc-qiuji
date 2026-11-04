"""Random held-out stage-2 maps: one quadrant drawn at random, rotated four times about the
centre so every spawn is equivalent. Statistics follow the fixed set in eval/maps/
(obstacle density 7..39 %, 12 normal + 4 high resources, same missions).

  python3 eval/maps.py SEED > map.json
"""
import json
import random
import sys
from collections import deque
from pathlib import Path

N = 24
TEMPLATE = Path(__file__).resolve().parent / "maps" / "stage2_2.json"


def rotations(p):
    """The four images of p under rotation about the map centre."""
    x, y = p
    return [(x, y), (N - 1 - y, x), (N - 1 - x, N - 1 - y), (y, N - 1 - x)]


def connected(free):
    start = next(iter(free))
    seen, queue = {start}, deque([start])
    while queue:
        x, y = queue.popleft()
        for q in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if q in free and q not in seen:
                seen.add(q)
                queue.append(q)
    return len(seen) == len(free)


def generate(seed):
    rng = random.Random(seed)
    quadrant = [(x, y) for x in range(N // 2) for y in range(N // 2)]
    while True:
        # Walls are short straight segments, like the hand-made maps.
        target, walls = rng.uniform(0.07, 0.39) * len(quadrant), set()
        while len(walls) < target:
            (x, y), (dx, dy) = rng.choice(quadrant), rng.choice(((1, 0), (0, 1)))
            walls.update((x + i * dx, y + i * dy) for i in range(rng.randint(1, 4))
                         if x + i * dx < N // 2 and y + i * dy < N // 2)
        open_cells = sorted(set(quadrant) - walls)
        spawn, *items = rng.sample(open_cells, 5)
        obstacles = {q for p in walls for q in rotations(p)}
        free = {(x, y) for x in range(N) for y in range(N)} - obstacles
        if (N // 2, N // 2) in free and connected(free):
            break
    config = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    config["map"]["obstacles"] = sorted(list(p) for p in obstacles)
    config["map"]["resources"] = [
        {"id": f"{kind}_{q[0]}_{q[1]}", "position": list(q), "value": value}
        for i, p in enumerate(items) for kind, value in [("high", 20) if i == 0 else ("normal", 10)]
        for q in rotations(p)]
    config["robots"] = [{"team": team, "spawn": list(q)} for team, q in zip("ABCD", rotations(spawn))]
    return config


STAGE1 = Path(__file__).resolve().parent / "maps" / "stage1.json"


def reachable(free, start):
    seen, queue = {start}, deque([start])
    while queue:
        x, y = queue.popleft()
        for q in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if q in free and q not in seen:
                seen.add(q)
                queue.append(q)
    return seen


def generate_stage1(seed, n=20):
    """Random stage-1 map from a deliberately broad family, so results do not hinge on the
    practice map's look: obstacle density 5..45 % in one of four styles (3x3 hollow blocks
    and bars like the practice map, straight bars only, rooms with doorways, scattered
    cells), a spawn anywhere, and 12 normal + 4 high resources placed uniformly or in
    clusters on cells reachable from the spawn."""
    rng = random.Random(seed)
    cells = [(x, y) for x in range(n) for y in range(n)]
    inside = lambda p: 0 <= p[0] < n and 0 <= p[1] < n
    while True:
        style, target, walls = rng.choice(("blocks", "bars", "rooms", "scatter")), rng.uniform(0.05, 0.45) * len(cells), set()
        if style == "rooms":
            # Full-length walls with random doorways; the reachability check below rejects sealed maps.
            for _ in range(rng.randint(2, 6)):
                k, vertical = rng.randrange(2, n - 2), rng.random() < 0.5
                line = [(k, i) if vertical else (i, k) for i in range(n)]
                walls.update(p for p in line if rng.random() > 0.15)
        while len(walls) < target:
            x, y = rng.choice(cells)
            if style == "blocks" and rng.random() < 0.4:
                walls.update((x + i, y + j) for i in range(3) for j in range(3) if (i, j) != (1, 1))
            elif style == "scatter":
                walls.add((x, y))
            else:
                dx, dy = rng.choice(((1, 0), (0, 1)))
                walls.update((x + i * dx, y + i * dy) for i in range(rng.randint(1, 6)))
        walls = {p for p in walls if inside(p)}
        spawn = rng.choice(cells)
        free = set(cells) - walls
        if spawn not in free:
            continue
        open_cells = sorted(reachable(free, spawn) - {spawn})
        if len(open_cells) >= 0.4 * len(cells):
            break
    if rng.random() < 0.5:
        items = rng.sample(open_cells, 16)
    else:
        centres, items = rng.sample(open_cells, rng.randint(2, 5)), []
        while len(items) < 16:
            c = rng.choice(centres)
            near = [p for p in open_cells if abs(p[0] - c[0]) + abs(p[1] - c[1]) <= 4 and p not in items]
            items.append(rng.choice(near or [p for p in open_cells if p not in items]))
    config = json.loads(STAGE1.read_text(encoding="utf-8"))
    config["map"]["obstacles"] = sorted(list(p) for p in walls)
    config["map"]["resources"] = [
        {"id": f"{kind}_{p[0]}_{p[1]}", "position": list(p), "value": value}
        for i, p in enumerate(items) for kind, value in [("high", 20) if i < 4 else ("normal", 10)]]
    config["robots"] = [{"team": "A", "spawn": list(spawn)}]
    return config


if __name__ == "__main__":
    stage1 = sys.argv[1] == "--stage1"
    seed = int(sys.argv[-1])
    json.dump(generate_stage1(seed) if stage1 else generate(seed), sys.stdout, ensure_ascii=False)
