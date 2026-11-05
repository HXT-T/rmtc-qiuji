"""How much of a match result is the program, and how much the map and seat (docs/赛制公平性.md 6, R1).

A batch run with seat rotations puts every bot on every seat of every map once, so the outcome
splits orthogonally into: bot + map x seat (spawn conditions) + bot x map + residual (seat-specific
fit and the interplay of the other three). Raw sums of squares overstate sources with many degrees
of freedom (noise alone fills them), so shares are of variance components: with M maps, each source's
mean square estimates residual variance + k * component, k being the observations per level.

  python3 eval/variance.py ~/rmtc-eval/runs/pool
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

MEASURES = {
    "score": lambda t, r: t.get("score", 0),
    "lead": lambda t, r: t.get("score", 0) - max(o.get("score", 0) for o in r["teams"] if o is not t),
    "first": lambda t, r: float(t["rank"] == 1),
    "top2": lambda t, r: float(t["rank"] <= 2),
}


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs)


def decompose(rows):
    """rows: (bot, map, seat, y). Returns sums of squares by source."""
    g = mean(y for *_, y in rows)
    groups = defaultdict(lambda: defaultdict(list))
    for b, m, s, y in rows:
        for key, k in (("b", b), ("m", m), ("ms", (m, s)), ("bm", (b, m))):
            groups[key][k].append(y)
    mu = {key: {k: mean(v) for k, v in d.items()} for key, d in groups.items()}
    ss = defaultdict(float)
    for b, m, s, y in rows:
        bot, cell = mu["b"][b] - g, mu["ms"][m, s] - g
        inter = mu["bm"][b, m] - mu["b"][b] - mu["m"][m] + g
        ss["bot"] += bot ** 2
        ss["map x seat"] += cell ** 2
        ss["bot x map"] += inter ** 2
        ss["residual"] += (y - g - bot - cell - inter) ** 2
    return ss


def main():
    run = Path(sys.argv[1]).expanduser()
    matches = [r for r in map(json.loads, (run / "results.jsonl").open()) if r.get("ok")]
    print(f"{len(matches)} matches")
    for name, f in MEASURES.items():
        rows = [(t["bot"], str(r["seed"]), t["team"], f(t, r)) for r in matches for t in r["teams"]]
        ss = decompose(rows)
        maps = len({m for _, m, _, _ in rows})
        df = {"bot": 3, "map x seat": 4 * maps - 1, "bot x map": 3 * (maps - 1), "residual": 9 * maps}
        per_level = {"bot": 4 * maps, "map x seat": 4, "bot x map": 4}
        noise = ss["residual"] / df["residual"]
        parts = {k: max(0.0, (ss[k] / df[k] - noise) / per_level[k]) for k in per_level} | {"residual": noise}
        total = sum(parts.values())
        print(f"\n{name}: " + "  ".join(f"{k} {v / total:.0%}" for k, v in parts.items()))
        by_bot = defaultdict(list)
        for b, _, _, y in rows:
            by_bot[b].append(y)
        for b, ys in sorted(by_bot.items(), key=lambda kv: -mean(kv[1])):
            m = mean(ys)
            se = (sum((y - m) ** 2 for y in ys) / (len(ys) - 1) / len(ys)) ** 0.5
            print(f"  {b:<6}{m:8.3f} ± {se:.3f}  (n {len(ys)})")


if __name__ == "__main__":
    main()
