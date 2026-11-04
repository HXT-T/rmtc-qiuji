"""Who took which resource, and when, from the referee's per-tick frames (diagnosis).

  python3 eval/pickups.py ~/rmtc-eval/runs/v2/m4_r0 [more run dirs...]
"""
import glob
import json
import sys

for run in sys.argv[1:]:
    path = glob.glob(f"{run}/referee/*/frames.jsonl")[0]
    prev, picks, names = None, [], {}
    for line in open(path):
        f = json.loads(line)
        view = f["map_view"]
        res = {tuple(r["position"]): r["value"] for r in view["resources"]}
        pos = {r["team"]: tuple(r["position"]) for r in view["robots"]}
        names = {t["team"]: t["team_name"] for t in f["teams"]}
        if prev is not None:
            for p, v in prev.items():
                if p not in res:
                    who = [t for t, q in pos.items() if q == p]
                    picks.append((f["tick"], names[who[0]] if who else "?", p, v))
        prev = res
    print(run.split("/")[-1], "spawns", {names[r["team"]]: r["spawn"] for r in json.load(open(f"{run}/config.json"))["robots"]})
    for bot in sorted(set(names.values())):
        mine = [(t, p, v) for t, b, p, v in picks if b == bot]
        print(f"  {bot:<10} total {sum(v for *_, v in mine):>4}  " + " ".join(f"{t}:{p[0]},{p[1]}{'H' if v == 20 else ''}" for t, p, v in mine))
    print("  last pickup tick", max((t for t, *_ in picks), default=None))
