"""Does the format pick the strongest programs of a group? (docs/赛制公平性.md 6, R2)

The reference order is the bots' first-place rate over the whole batch. For each format, the share of
draws in which it names the same winner and the same top two: one match (the contest's rule: one map,
one seat draw), the total of the 4 seat rotations on one map, and the total over k maps of rotations.

  python3 eval/format.py ~/rmtc-eval/runs/pool
"""
import json
import random
import sys
from collections import defaultdict
from pathlib import Path


def ranking(totals):
    return sorted(totals, key=lambda b: -totals[b])


def main():
    run = Path(sys.argv[1]).expanduser()
    matches = [r for r in map(json.loads, (run / "results.jsonl").open()) if r.get("ok")]
    first = defaultdict(int)
    for r in matches:
        first[next(t["bot"] for t in r["teams"] if t["rank"] == 1)] += 1
    truth = sorted({t["bot"] for t in matches[0]["teams"]}, key=lambda b: -first[b])
    print("reference order " + " > ".join(f"{b} {first[b] / len(matches):.2f}" for b in truth))
    # One match: rank 1 and 2 as the referee decided them (tiebreaks included).
    single = [[t["bot"] for t in sorted(r["teams"], key=lambda t: t["rank"])] for r in matches]
    by_map = defaultdict(list)
    for r in matches:
        by_map[str(r["seed"])].append(r)

    def rotations(maps):
        totals = defaultdict(float)
        for m in maps:
            for r in by_map[m]:
                for t in r["teams"]:
                    totals[t["bot"]] += t["score"]
        return ranking(totals)

    rng = random.Random(0)
    formats = {"1 match": single, "4 rotations, 1 map": [rotations([m]) for m in by_map]}
    for k in (3, 5):
        formats[f"4 rotations, {k} maps"] = [rotations(rng.sample(sorted(by_map), k)) for _ in range(2000)]
    print(f"{'format':<22}{'winner right':>14}{'top 2 right':>13}{'n':>7}")
    for name, orders in formats.items():
        win = sum(o[0] == truth[0] for o in orders) / len(orders)
        top2 = sum(set(o[:2]) == set(truth[:2]) for o in orders) / len(orders)
        print(f"{name:<22}{win:>14.2f}{top2:>13.2f}{len(orders):>7}")


if __name__ == "__main__":
    main()
