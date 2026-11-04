"""Stage 1 offline tools for one practice map (single robot, MOVE/WAIT, 100 ticks).

  sim:     run an rmtc_bot version through a local simulator that follows rules §4–§6
           (9x9 vision, obstacles do not block sight, auto-collect, failed moves stay).
  optimum: full-information orienteering optimum: the ceiling no online bot can beat.

The bot only receives protocol-shaped perception frames, unless --route-map turns on
the known-map mode.
  python eval/stage1.py optimum MAP.json
  python eval/stage1.py sim MAP.json [--bot DIR] [--route-map MAP.json] [--trace]
  bench:   online value vs the optimum on the practice map plus random maps 1..N, in
           parallel; several --bot give paired differences from the first one, and
           --reveal hands the bot part of the truth (value-of-information diagnostics)
  python eval/stage1.py bench [--bot DIR ...] [--maps 400] [--reveal terrain,resources] [--out FILE]
"""
import argparse
import hashlib
import importlib
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rmtc_bot.known_map import DIRS, load, optimum  # noqa: E402
import maps  # noqa: E402
from maps import STAGE1, generate_stage1  # noqa: E402

CACHE = Path(__file__).resolve().parents[1] / "work" / "stage1_optimum.json"


def relative(dx, dy):
    rows = [["0"] * 9 for _ in range(9)]
    rows[4][4] = "1"
    if dx or dy:
        rows[dy + 4][dx + 4] = "2"
    return "\n".join("".join(r) for r in rows)


def frame(world, tick, pos, resources, score, receipt):
    r = world["radius"]
    entities = []
    for p in sorted(world["obstacles"]):
        if max(abs(p[0] - pos[0]), abs(p[1] - pos[1])) <= r:
            entities.append({"type": "obstacle", "position": relative(p[0] - pos[0], p[1] - pos[1])})
    for p, (rid, value) in sorted(resources.items()):
        if max(abs(p[0] - pos[0]), abs(p[1] - pos[1])) <= r:
            entities.append({"type": "resource_high" if value >= 20 else "resource_normal", "id": rid,
                             "value": value, "position": relative(p[0] - pos[0], p[1] - pos[1])})
    own = {"team": "A", "position": list(pos), "hp": 100, "alive": True, "protected": False,
           "last_action": receipt}
    if tick == 0:
        own["position_reason"] = "initial"
    return {"tick": tick, "session_id": "stage1-sim", "vision_radius": r, "self": own,
            "visible_entities": entities, "map": {"width": world["width"], "height": world["height"]},
            "score": {"resource_value": score, "effective_damage": 0, "invalid_actions": 0,
                      "mission_completed": 0, "mission_completed_ids": []},
            "missions": [], "mission_text": "", "event_text": ""}


def simulate(world, bot_dir, ticks=100, config_overrides=None, trace=False, reveal=()):
    # Import the requested version fresh, so several versions can be compared in one process.
    sys.path.insert(0, str(Path(bot_dir).resolve()))
    for name in [m for m in sys.modules if m == "rmtc_bot" or m.startswith("rmtc_bot.")]:
        del sys.modules[name]
    Config = importlib.import_module("rmtc_bot.config").Config
    Engine = importlib.import_module("rmtc_bot.engine").Engine
    sys.path.pop(0)
    engine = Engine(Config(stage=1, team="A", **(config_overrides or {})))
    if reveal:
        reset = engine._reset

        def seeded(f):
            # The first frame resets the planner; seed its fresh memory with part of the truth.
            reset(f)
            m = engine.planner.memory
            if "terrain" in reveal:
                m.obstacles |= world["obstacles"]
            if "resources" in reveal:
                for p, (rid, value) in world["resources"].items():
                    m.resources[p] = {"type": "resource_high" if value >= 20 else "resource_normal",
                                      "id": rid, "value": value, "pos": p, "last_seen": 0}
                    m.ever_seen[p] = value
        engine._reset = seeded
    pos, resources, score, receipt = world["spawn"], dict(world["resources"]), 0, None
    pickups, path, slowest = [], [pos], 0.0
    for tick in range(ticks + 1):
        start = time.perf_counter()
        act = engine.step(json.dumps(frame(world, tick, pos, resources, score, receipt)))
        slowest = max(slowest, time.perf_counter() - start)
        if tick == ticks:
            break
        receipt = {"tick": tick, "action": act["action"], "moved": False}
        if act["action"] == "move":
            dx, dy = DIRS[act["direction"]]
            q = (pos[0] + dx, pos[1] + dy)
            if 0 <= q[0] < world["width"] and 0 <= q[1] < world["height"] and q not in world["obstacles"]:
                pos = q
                receipt.update(moved=True, effective_direction=act["direction"])
        path.append(pos)
        if pos in resources:
            score += resources.pop(pos)[1]
            pickups.append((tick, pos))
        if trace:
            print(tick, act["action"], act.get("direction", ""), pos, score)
    return dict(value=score, pickups=pickups, missed=sorted(resources), path=path,
                last_pickup=pickups[-1][0] if pickups else None, slowest=slowest)


def world_of(config):
    m = config["map"]
    return dict(width=m["width"], height=m["height"], radius=config.get("vision_radius", 4),
                obstacles={tuple(p) for p in m["obstacles"]},
                resources={tuple(r["position"]): (r["id"], r["value"]) for r in m["resources"]},
                spawn=tuple(config["robots"][0]["spawn"]))


def bench_map(job):
    seed, bots, reveal, ceiling = job
    world = load(STAGE1) if seed == 0 else world_of(generate_stage1(seed))
    runs = [simulate(world, bot, reveal=reveal) for bot in bots]
    return dict(seed=seed, optimum=optimum(world)["value"] if ceiling is None else ceiling,
                values=[r["value"] for r in runs], slowest=max(r["slowest"] for r in runs))


def bench(bots, count, reveal=(), out=""):
    """Online value / exact optimum over the practice map (seed 0) and random maps 1..count.
    Optima are cached per generator version; with several bots, each one is compared map by
    map with the first (paired difference ± standard error)."""
    tag = hashlib.sha1(Path(maps.__file__).read_bytes() + STAGE1.read_bytes()).hexdigest()
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    known = cache.get("optima", {}) if cache.get("tag") == tag else {}
    jobs = [(seed, bots, reveal, known.get(str(seed))) for seed in range(count + 1)]
    with Pool() as pool:
        rows = sorted(pool.imap_unordered(bench_map, jobs), key=lambda r: r["seed"])
    known.update({str(r["seed"]): r["optimum"] for r in rows})
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps({"tag": tag, "optima": known}))
    if out:
        Path(out).write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    best, n = sum(r["optimum"] for r in rows), len(rows)
    for k, bot in enumerate(bots):
        values = [r["values"][k] for r in rows]
        short = sum(v < r["optimum"] for v, r in zip(values, rows))
        line = f"{bot}: {sum(values)} / {best} = {sum(values) / best:.2%}, {short} maps short"
        if k:
            diff = [v - r["values"][0] for v, r in zip(values, rows)]
            mean = sum(diff) / n
            se = (sum((d - mean) ** 2 for d in diff) / (n - 1) / n) ** 0.5
            line += (f"; vs first {mean:+.2f} +- {se:.2f} per map "
                     f"({sum(d > 0 for d in diff)} better, {sum(d < 0 for d in diff)} worse)")
        print(line)
    print(f"{n} maps, slowest tick {max(r['slowest'] for r in rows) * 1000:.0f} ms")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("optimum", "sim", "bench"))
    parser.add_argument("map", nargs="?")
    parser.add_argument("--maps", type=int, default=400)
    parser.add_argument("--bot", action="append")
    parser.add_argument("--reveal", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--route-map", default="")
    parser.add_argument("--trace", action="store_true")
    args = parser.parse_args()
    bots = args.bot or [str(Path(__file__).resolve().parents[1])]
    if args.command == "bench":
        return bench(bots, args.maps, tuple(filter(None, args.reveal.split(","))), args.out)
    world = load(args.map)
    if args.command == "optimum":
        print(json.dumps(optimum(world)))
    else:
        overrides = {"route_map": args.route_map} if args.route_map else None
        result = simulate(world, bots[0], config_overrides=overrides, trace=args.trace)
        result.pop("path")
        print(json.dumps(result))


if __name__ == "__main__":
    main()
