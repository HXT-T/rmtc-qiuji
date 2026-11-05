"""Will a visible rival take this resource before we get there? Offline calibration (docs/算法设计.md 9.1b).

From the referee's true frames: at each tick (<= 200) for every bot X ("we") and every resource r that X
has seen and that is still present, the event is "a rival visible to X takes r within X's path steps".
Three predictors are scored by log loss and reliability, cross-validated across maps:
  f1  0.55 if the nearest visible rival is closer by Manhattan distance (+1), else 0 (planner.route);
  mi  Miraculy's intent shares: value/(steps+1), x3 after a step towards, x0.5 after one away, an
      outside option of 0.5, times urgency clamp((our steps - its steps)/4) and 0.9;
  fit logistic regression per rival on its steps, our lead, progress, value and its share,
      combined as 1 - prod(1 - p) over the visible rivals.

  python3 eval/intent.py ~/rmtc-eval/runs/pool ~/rmtc-eval/runs/opool
"""
import glob
import json
import math
import sys
from collections import deque

import numpy as np

HORIZON = 200


def bfs(obstacles, w, h, start):
    dist, queue = {start: 0}, deque([start])
    while queue:
        x, y = queue.popleft()
        for q in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= q[0] < w and 0 <= q[1] < h and q not in obstacles and q not in dist:
                dist[q] = dist[x, y] + 1
                queue.append(q)
    return dist


def match_rows(path, group):
    frames = [json.loads(line) for line in open(path)]
    view = frames[0]["map_view"]
    w, h = view["map"]["width"], view["map"]["height"]
    obstacles = {tuple(p) for p in view["obstacles"]}
    cache = {}
    steps = lambda a, b: (cache.setdefault(a, bfs(obstacles, w, h, a))).get(b, 99)
    present = [{tuple(r["position"]): r["value"] for r in f["map_view"]["resources"]} for f in frames]
    robots = [{r["team"]: (tuple(r["position"]), r["alive"]) for r in f["map_view"]["robots"]} for f in frames]
    taken = {}  # resource -> (tick, team)
    for t in range(1, len(frames)):
        for p in present[t - 1]:
            if p not in present[t]:
                taken[p] = (frames[t]["tick"], next((k for k, (q, _) in robots[t].items() if q == p), None))
    seen = {k: set() for k in robots[0]}
    rows = []
    for t in range(1, min(HORIZON, len(frames))):
        tick = frames[t]["tick"]
        for me, (pos, alive) in robots[t].items():
            if not alive:
                continue
            near = lambda q: max(abs(q[0] - pos[0]), abs(q[1] - pos[1])) <= 4
            seen[me] |= {p for p in present[t] if near(p)}
            rivals = [k for k, (q, a) in robots[t].items() if k != me and a and near(q)]
            known = [p for p in present[t] if p in seen[me]]
            if not rivals or not known:
                continue
            for r in known:
                ours = steps(pos, r)
                if ours >= 99:
                    continue
                when, who = taken.get(r, (10 ** 6, None))
                y = float(who in rivals and when <= tick + ours)
                pairs = []
                for e in rivals:
                    q, (prev, prev_alive) = robots[t][e][0], robots[t - 1][e]
                    moved = prev_alive and prev != q and near(prev)
                    progress = (steps(prev, r) - steps(q, r)) if moved else 0
                    weight = {s: present[t][s] / 10 / (steps(q, s) + 1) for s in known}
                    heading = {s: (3.0 if steps(prev, s) > steps(q, s) else 0.5) if moved else 1.0 for s in known}
                    pairs.append((steps(q, r), abs(q[0] - r[0]) + abs(q[1] - r[1]), progress, moved,
                                  weight[r] / (0.5 + sum(weight.values())),
                                  weight[r] * heading[r] / (0.5 + sum(weight[s] * heading[s] for s in known))))
                rows.append((group, y, ours, present[t][r], pairs))
    return rows


def p_f1(ours, pairs):
    return 0.55 if min(m for _, m, *_ in pairs) + 1 < ours else 0.0


def p_mi(ours, value, pairs):
    keep = 1.0
    for d, _, _, _, _, share in pairs:
        keep *= 1 - 0.9 * share * min(1.0, max(0.0, (ours - d) / 4))
    return 1 - keep


def features(ours, value, pair):
    d, _, progress, moved, share, _ = pair
    lead = ours - d
    return [1.0, d / 10, max(-8, min(8, lead)) / 4, float(lead > 0), float(progress > 0), float(moved and progress < 0),
            value / 20, math.log(share + 1e-4)]


def fit(rows, iters=3000, lr=2.0):
    """Logistic regression on per-rival probabilities combined as 1 - prod(1 - p), by gradient ascent."""
    X = np.array([features(o, v, p) for _, _, o, v, pairs in rows for p in pairs])
    idx = np.array([i for i, (*_, pairs) in enumerate(rows) for _ in pairs])
    y = np.array([r[1] for r in rows])
    beta = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ beta))
        keep = np.exp(np.bincount(idx, np.log1p(-np.minimum(p, 1 - 1e-9)), len(rows)))
        q = np.clip(1 - keep, 1e-6, 1 - 1e-6)
        # d loglik / d beta: through q = 1 - prod(1 - p_i), dq/dz_i = keep * p_i
        coef = (y / q - (1 - y) / (1 - q)) * keep
        beta += lr * X.T @ (coef[idx] * p) / len(rows)
    return beta


def p_fit(beta, ours, value, pairs):
    p = 1 / (1 + np.exp(-np.array([features(ours, value, x) for x in pairs]) @ beta))
    return 1 - np.prod(1 - p)


def report(name, ys, ps):
    ys, ps = np.array(ys), np.clip(np.array(ps), 1e-3, 1 - 1e-3)
    loss = -np.mean(ys * np.log(ps) + (1 - ys) * np.log(1 - ps))
    bins = [(lo, hi) for lo, hi in ((0, .05), (.05, .2), (.2, .4), (.4, .6), (.6, 1.01))]
    table = "  ".join(f"[{lo:.2f},{hi:.2f}) {np.mean(ps[m]):.2f}/{np.mean(ys[m]):.2f} n{m.sum()}"
                      for lo, hi in bins if (m := (ps >= lo) & (ps < hi)).sum())
    print(f"{name:<4} log loss {loss:.4f}  brier {np.mean((ps - ys) ** 2):.4f}   predicted/actual {table}")


def main():
    rows = []
    for run in sys.argv[1:]:
        for i, path in enumerate(sorted(glob.glob(f"{run}/m*/referee/*/frames.jsonl"))):
            rows += match_rows(path, path.split("/m")[-1].split("_r")[0])
    maps = sorted({g for g, *_ in rows})
    folds = [set(maps[0::2]), set(maps[1::2])]
    ys, pf, pm, pc = [], [], [], []
    for k, test in enumerate(folds):
        train = [r for r in rows if r[0] not in test]
        beta = fit(train)
        print(f"fold {k}: beta " + " ".join(f"{b:+.2f}" for b in beta))
        for g, y, ours, value, pairs in rows:
            if g in test:
                ys.append(y)
                pf.append(p_f1(ours, pairs))
                pm.append(p_mi(ours, value, pairs))
                pc.append(p_fit(beta, ours, value, pairs))
    print(f"{len(ys)} (observer, resource, tick) rows, base rate {np.mean(ys):.3f}, {len(maps)} maps")
    for name, ps in (("f1", pf), ("mi", pm), ("fit", pc)):
        report(name, ys, ps)
    print("all maps: beta " + ", ".join(f"{b:.2f}" for b in fit(rows)))


if __name__ == "__main__":
    main()
