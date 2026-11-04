"""Batch evaluation on the practice referee (run inside WSL / Ubuntu + ROS 2 Humble).

Each map is played once per seat rotation of the lineup, so every bot starts from every
spawn. Maps are the fixed validation set eval/maps/stage2_*.json plus fresh random maps
(eval/maps.py, a new seed every run) as a held-out check. Results append to
<out>/results.jsonl; `--summary` only reports. `--screen CONTROL_RUN:BOT` plays only the cells where the
target bot would act differently from that control (eval/replay.py) and copies the control's result for
the others, renamed to the target.

  python3 eval/batch.py --practice ~/rmtc_practice --bot new=~/eval/v/new \
      --lineup new,thief,ref,ref --target new --parallel 4 --out ~/eval/runs/x
"""
import argparse
import json
import math
from collections import defaultdict
import random
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from maps import generate  # noqa: E402
from match import run_match  # noqa: E402
from replay import screen  # noqa: E402

SEATS = "ABCD"
MAPS = Path(__file__).resolve().parent / "maps"


def numbers(text):
    """'1-3,5,r42' -> [1, 2, 3, 5, 'r42'] (r-prefixed entries are random-map seeds)"""
    result = []
    for part in filter(None, text.split(",")):
        if part.startswith("r"):
            result.append(part)
            continue
        first, _, last = part.partition("-")
        result += range(int(first), int(last or first) + 1)
    return result


def summarize(rows, compare=None):
    ok = [r for r in rows if r.get("ok")]
    print(f"matches: {len(ok)} ok, {len(rows) - len(ok)} failed")
    stats = {}
    for r in ok:
        for t in r["teams"]:
            s = stats.setdefault(t["bot"], {"n": 0, "score": 0, "res": 0, "dmg": 0, "mis": 0,
                                            "inv": 0, "win": 0, "lead": 0})
            s["n"] += 1
            s["score"] += t.get("score", 0)
            s["res"] += t.get("resource_value", 0)
            s["dmg"] += t.get("effective_damage", 0)
            s["mis"] += t.get("mission_completed", 0)
            s["inv"] += t.get("invalid_actions", 0)
            s["win"] += t["rank"] == 1
            # Lead over the best other team: positive when first, larger is better.
            s["lead"] += t.get("score", 0) - max(o.get("score", 0) for o in r["teams"] if o is not t)
    print(f"{'bot':<14}{'n':>5}{'score':>8}{'res':>7}{'dmg':>8}{'miss':>6}{'inv':>6}{'win':>7}{'lead':>7}")
    for bot, s in sorted(stats.items(), key=lambda kv: (-kv[1]["win"] / kv[1]["n"], -kv[1]["lead"] / kv[1]["n"])):
        n = s["n"]
        print(f"{bot:<14}{n:>5}{s['score'] / n:>8.2f}{s['res'] / n:>7.1f}{s['dmg'] / n:>8.1f}"
              f"{s['mis'] / n:>6.2f}{s['inv'] / n:>6.1f}{s['win'] / n:>7.0%}{s['lead'] / n:>+7.1f}")
    if compare:
        a, b = compare
        # Only first place matters: count the matches each of the two won outright.
        n = wins = losses = seats_a = seats_b = 0
        for r in ok:
            bots = [t["bot"] for t in r["teams"]]
            winner = next(t["bot"] for t in r["teams"] if t["rank"] == 1)
            if a in bots and b in bots:
                n += 1
                wins += winner == a
                losses += winner == b
                seats_a, seats_b = seats_a + bots.count(a), seats_b + bots.count(b)
        if wins + losses:
            # Exact binomial test: with equal strength each seat is equally likely to win,
            # so a's share of the wins should match its share of the seats.
            m, q = wins + losses, seats_a / (seats_a + seats_b)
            pmf = [math.comb(m, i) * q ** i * (1 - q) ** (m - i) for i in range(m + 1)]
            p = min(1.0, sum(x for x in pmf if x <= pmf[wins] * (1 + 1e-9)))
            print(f"\n{a} vs {b} in the same match: first place {wins} vs {losses} over {n} matches; "
                  f"per seat {wins / seats_a:.0%} vs {losses / seats_b:.0%} (binomial p={p:.2f})")
        # Diagnosis: resources of a vs b in the same map and seat (seat luck cancels out).
        seat = {}
        for r in ok:
            for t in r["teams"]:
                if t["bot"] in (a, b):
                    seat.setdefault((str(r["seed"]), t["team"], t["bot"]), []).append(t.get("resource_value", 0))
        diffs = [sum(va) / len(va) - sum(vb) / len(vb) for (m, s, bot), va in seat.items()
                 if bot == a and (vb := seat.get((m, s, b)))]
        if diffs:
            print(f"{a} - {b} resources in the same map and seat: {sum(diffs) / len(diffs):+.1f} over {len(diffs)} cells")
    # Winner of every map x rotation cell: the pass bar is one bot first in all of them.
    cells = {(str(r["seed"]), r["rotation"]): next(t["bot"] for t in r["teams"] if t["rank"] == 1) for r in ok}
    shifts = sorted({s for _, s in cells})
    print("\nwinners  " + "".join(f"{'rot ' + str(s):>9}" for s in shifts))
    for seed in sorted({m for m, _ in cells}, key=lambda m: (m.startswith("r"), m.zfill(9))):
        print(f"{seed:<9}" + "".join(f"{cells.get((seed, s), '-'):>9}" for s in shifts))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--practice", required=True, help="unpacked rmtc_practice directory")
    parser.add_argument("--bot", action="append", default=[], help="name=directory containing rmtc_bot")
    parser.add_argument("--lineup", default="", help="4 comma-separated: bot name, 'ref' or 'thief'; seats A..D")
    parser.add_argument("--target", help="bot the thief aims at (default: first bot in the lineup)")
    parser.add_argument("--maps", default="1-9", help="stage2 map numbers and random seeds, e.g. 1-9 or 2,5,r42")
    parser.add_argument("--random", type=int, default=1, help="fresh random maps added to this run")
    parser.add_argument("--ai-mode", choices=("mock", "real"), default="mock", help="real reads eval/.env")
    parser.add_argument("--rotations", default="0-3", help="seat rotations per map, e.g. 0-3 or 2,3")
    parser.add_argument("--parallel", type=int, default=3)
    parser.add_argument("--first-slot", type=int, default=0,
                        help="port/ROS-domain slot of the first worker; give concurrent batches disjoint slots")
    parser.add_argument("--tick-ms", type=int, default=250, help="250: no decision is late (contest: 500)")
    parser.add_argument("--max-ticks", type=int, default=500)
    parser.add_argument("--out", required=True)
    parser.add_argument("--compare", help="a,b: paired comparison of two bots")
    parser.add_argument("--summary", action="store_true", help="only summarize existing results")
    parser.add_argument("--screen", help="CONTROL_RUN:BOT: play only cells where the target differs from it")
    args = parser.parse_args()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    results = out / "results.jsonl"
    compare = args.compare.split(",") if args.compare else None
    if args.summary:
        summarize([json.loads(line) for line in results.open()], compare)
        return
    versions = {}
    for item in args.bot:
        name, path = item.split("=", 1)
        versions[name] = str(Path(path).expanduser().resolve())
        if not any((Path(versions[name]) / f).exists() for f in ("rmtc_bot", "run_bot.sh")):
            parser.error(f"{path} has neither an rmtc_bot package nor run_bot.sh")
    lineup = args.lineup.split(",")
    if len(lineup) != 4 or any(b not in ("ref", "thief") and b not in versions for b in lineup):
        parser.error("lineup needs 4 entries, each 'ref', 'thief' or a --bot name")
    target = args.target or next((b for b in lineup if b in versions), None)
    if "thief" in lineup and target not in versions:
        parser.error("thief needs a --target bot")
    practice = str(Path(args.practice).expanduser().resolve())
    jobs = []
    fresh = [f"r{random.SystemRandom().randrange(10 ** 6)}" for _ in range(args.random)]
    for seed in [*numbers(args.maps), *fresh]:
        for shift in numbers(args.rotations):
            jobs.append((seed, shift, {SEATS[(i + shift) % 4]: lineup[i] for i in range(4)}))
    rows = []
    if args.screen:
        control_bot = args.screen.rsplit(":", 1)[1]
        # A cell the control failed to play has no frames to screen with: play it.
        cells = defaultdict(lambda: (None, 0), screen(args.screen, versions[target]))
        rename = lambda name: target if name == control_bot else name
        for row, tick in (cells[str(seed), shift] for seed, shift, _ in jobs):
            if tick is None:
                rows.append(dict(row, screened=True, lineup={s: rename(b) for s, b in row["lineup"].items()},
                                 teams=[dict(t, bot=rename(t["bot"]), team_name=rename(t["bot"])) for t in row["teams"]]))
        with results.open("a") as stream:
            stream.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        jobs = [j for j in jobs if cells[str(j[0]), j[1]][1] is not None]
        print(f"screened: {len(rows)} cells identical to {args.screen}", flush=True)
    total = len(rows) + len(jobs)
    print(f"{len(jobs)} matches, {args.parallel} in parallel, {args.tick_ms} ms/tick", flush=True)

    def work(slot, job):
        seed, shift, seats = job
        config = (generate(int(seed[1:])) if str(seed).startswith("r")
                  else json.loads((MAPS / f"stage2_{seed}.json").read_text(encoding="utf-8")))
        # Referee start-up timeouts and stalls under parallel load are infrastructure
        # failures, not bot results: rerun the same match from scratch.
        for attempt in range(3):
            suffix = f"_a{attempt}" if attempt else ""
            result = run_match(config, seats, versions, practice, out / f"m{seed}_r{shift}{suffix}",
                               port=8100 + slot, domain=40 + 2 * slot,
                               tick_ms=args.tick_ms, max_ticks=args.max_ticks,
                               ai_mode=args.ai_mode, target=target)
            if result.get("ok"):
                break
        return dict(result, seed=seed, rotation=shift, attempts=attempt + 1)

    with ThreadPoolExecutor(args.parallel) as pool:
        pending = {}
        queue = list(jobs)
        free = list(range(args.first_slot, args.first_slot + args.parallel))
        while queue or pending:
            while queue and free:
                slot = free.pop()
                pending[pool.submit(work, slot, queue.pop(0))] = slot
            done = next(as_completed(pending))
            free.append(pending.pop(done))
            row = done.result()
            rows.append(row)
            with results.open("a") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            line = row.get("error") or " ".join(f"{t['bot']}:{t['rank']}/{t.get('score', 0):.1f}"
                                                for t in sorted(row["teams"], key=lambda t: t["rank"]))
            print(f"[{len(rows)}/{total}] seed {row['seed']} rot {row['rotation']}: {line}", flush=True)
    summarize(rows, compare)


if __name__ == "__main__":
    main()
