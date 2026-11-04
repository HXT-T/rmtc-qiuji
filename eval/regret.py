"""Stage 1 decision regret: how much better a single step could have been with the bot's own
information. At each decision tick the unseen part of the map is completed at random: unseen
walls from a map of the broad generator family (density closest to what has been observed),
and the not-yet-seen part of the published 4 high + 12 normal resources placed uniformly on
unseen free cells. Each legal
action is taken now and the bot then plays on in every sample (common samples for all
actions); regret = best mean final value - mean final value of the bot's own action.
This is a policy-improvement lower bound on the bot's per-step loss: it uses no hidden truth.

  python eval/regret.py [--maps 20] [--samples 8] [--stride 5] [--until 60]
"""
import argparse
import copy
import json
import random
import sys
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from maps import STAGE1, generate_stage1  # noqa: E402
from rmtc_bot.config import Config  # noqa: E402
from rmtc_bot.engine import Engine  # noqa: E402
from rmtc_bot.known_map import DIRS, load  # noqa: E402
from stage1 import frame, world_of  # noqa: E402

TICKS, HIGH, NORMAL = 100, 4, 12


def step(world, pos, action):
    """Rules §4–§6 movement: a blocked or out-of-bounds move stays put."""
    if action == "wait":
        return pos, False
    dx, dy = DIRS[action]
    q = (pos[0] + dx, pos[1] + dy)
    ok = 0 <= q[0] < world["width"] and 0 <= q[1] < world["height"] and q not in world["obstacles"]
    return (q, True) if ok else (pos, False)


def play(engine, world, tick, pos, resources, score, receipt, first=None):
    """Run the bot to the end of the match; `first` overrides its action on this tick."""
    resources = dict(resources)
    for t in range(tick, TICKS + 1):
        act = engine.step(json.dumps(frame(world, t, pos, resources, score, receipt)))
        if t == TICKS:
            break
        action = first if t == tick and first else (act["direction"] if act["action"] == "move" else "wait")
        pos, moved = step(world, pos, action)
        receipt = {"tick": t, "action": "wait" if action == "wait" else "move", "moved": moved}
        if moved:
            receipt["effective_direction"] = action
        if pos in resources:
            score += resources.pop(pos)[1]
    return score


def sample(memory, world, pos, rng):
    """A full world consistent with everything the bot has seen."""
    w, h = world["width"], world["height"]
    unseen = [(x, y) for y in range(h) for x in range(w) if (x, y) not in memory.seen]
    # Unseen terrain comes from a map of the broad generator family (structured walls make
    # paths far longer than scattered cells), picking the candidate whose obstacle density
    # is closest to the density observed so far.
    density = len(memory.obstacles) / max(1, len(memory.seen))
    donor = min((generate_stage1(rng.randrange(10 ** 9), n=w) for _ in range(5)),
                key=lambda m: abs(len(m["map"]["obstacles"]) / (w * h) - density))
    walls = set(memory.obstacles) | ({tuple(p) for p in donor["map"]["obstacles"]} & set(unseen) - {pos})
    free = [p for p in unseen if p not in walls]
    seen_values = list(memory.ever_seen.values())
    values = ([20] * (HIGH - sum(v >= 20 for v in seen_values))
              + [10] * (NORMAL - sum(v < 20 for v in seen_values)))
    resources = {p: (f"r{p}", r["value"]) for p, r in memory.resources.items()}
    for p, v in zip(rng.sample(free, min(len(free), len(values))), values):
        resources[p] = (f"s{p}", v)
    return dict(width=w, height=h, radius=world["radius"], obstacles=walls, resources=resources)


def analyse(job):
    seed, samples, stride, until = job
    world = load(STAGE1) if seed == 0 else world_of(generate_stage1(seed))
    engine = Engine(Config(stage=1, team="A"))
    pos, resources, score, receipt = world["spawn"], dict(world["resources"]), 0, None
    rng, rows = random.Random(seed), []
    for t in range(TICKS + 1):
        before = copy.deepcopy(engine)
        act = engine.step(json.dumps(frame(world, t, pos, resources, score, receipt)))
        if t == TICKS:
            break
        chosen = act["direction"] if act["action"] == "move" else "wait"
        if t < until and t % stride == 0:
            # Seen state on this tick (the engine has just observed it); collected value
            # stays in `score`, so only uncollected resources go into the samples.
            worlds = [sample(engine.planner.memory, world, pos, rng) for _ in range(samples)]
            finals = {}
            for a in ("wait", *DIRS):
                if a != "wait" and step(world, pos, a)[0] == pos:
                    continue  # blocked by a known wall: same as waiting
                finals[a] = [play(copy.deepcopy(before), wd, t, pos, wd["resources"], score, receipt, a)
                             for wd in worlds]
            q = {a: sum(v) / samples for a, v in finals.items()}
            best = max(q, key=q.get)
            mine = finals.get(chosen, finals["wait"])
            diff = [b - m for b, m in zip(finals[best], mine)]
            mean = sum(diff) / samples
            se = (sum((x - mean) ** 2 for x in diff) / max(1, samples - 1) / samples) ** 0.5
            rows.append(dict(seed=seed, tick=t, pos=pos, chosen=chosen, best=best, regret=mean, se=se, q=q))
        pos, moved = step(world, pos, chosen)
        receipt = {"tick": t, "action": act["action"], "moved": moved}
        if moved:
            receipt["effective_direction"] = chosen
        if pos in resources:
            score += resources.pop(pos)[1]
    return seed, score, rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--maps", type=int, default=20)
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--stride", type=int, default=5)
    parser.add_argument("--until", type=int, default=60)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    jobs = [(seed, args.samples, args.stride, args.until) for seed in range(args.maps + 1)]
    total, rows = 0.0, []
    with Pool() as pool:
        for seed, score, found in pool.imap_unordered(analyse, jobs):
            rows += found
            worst = max(found, key=lambda r: r["regret"])
            regret = sum(r["regret"] for r in found)
            total += regret
            print(f"map {seed:3}  value {score:4}  regret {regret:6.1f}  worst t={worst['tick']} "
                  f"{worst['chosen']}->{worst['best']} {worst['regret']:.1f}±{worst['se']:.1f}", flush=True)
    clear = [r for r in rows if r["regret"] > 2 * r["se"] > 0]
    print(f"total regret {total:.1f} over {len(jobs)} maps; {len(clear)} of {len(rows)} decisions "
          f"beyond 2 se, worth {sum(r['regret'] for r in clear):.1f}")
    if args.out:
        Path(args.out).write_text("\n".join(json.dumps(r, default=list) for r in rows), encoding="utf-8")


if __name__ == "__main__":
    main()
