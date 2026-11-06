"""Cell-by-cell comparison of a candidate batch with one control batch, or with the per-cell mean of
several placebo batches (neutral perturbations of the control). Batches are nearly deterministic, so
the control's own result on a fixed map set is one lucky or unlucky draw; a placebo mean removes that
bias. Variants that only change the late game share the control's opening, so pair those with the
control itself. `--split-cap` also splits the cells by whether the control's damage reached 500.
`--cause REASON` (one control): batches are not fully deterministic, so a cell whose own play first
differs from the control's before the candidate's first REASON decision is run-to-run noise; it
splits the cells into noise, caused by the variant, and identical. Several map sets pool with `+`
(their seeds differ). `mde` is the smallest true effect detected with 80% power at 5% (2.8 SE).
`drop` counts our actions the referee executed as a wait because they came too late (receipts): a
batch with drops depends on machine load, so rerun it with a longer --tick-ms.

  python3 eval/paired.py RUN_DIR:BOT BASE_RUN:BOT [BASE_RUN:BOT ...] [+ RUN_DIR:BOT BASE_RUN:BOT ...]
                         [--split-cap] [--cause REASON]
"""
import json
import math
import sys

KEYS = ("win", "score", "lead", "res", "dmg", "mis", "drop")
CAUSE = sys.argv[sys.argv.index("--cause") + 1] if "--cause" in sys.argv else None


def load(spec):
    run, bot = spec.rsplit(":", 1)
    out = {}
    for r in map(json.loads, open(f"{run}/results.jsonl")):
        if r.get("ok"):
            me = next(t for t in r["teams"] if t["bot"] == bot)
            best = max((t for t in r["teams"] if t is not me), key=lambda t: t["score"])
            log = [json.loads(x) for x in open(f"{r['out']}/bot_{me['team']}.jsonl")]
            done = {x["last_action"]["tick"]: x["last_action"]["action"] for x in log if x["kind"] == "receipt"}
            decisions = {x["tick"]: x for x in log if x["kind"] == "decision"}
            out[str(r["seed"]), r["rotation"]] = dict(
                win=me.get("first", me["rank"] == 1), score=me["score"], lead=me["score"] - best["score"],
                res=me["resource_value"], dmg=me["effective_damage"], mis=me["mission_completed"],
                drop=sum(d["action"]["action"] != "wait" and done.get(t) == "wait" for t, d in decisions.items()),
                log=decisions)
    return out


def report(cand, bases, cells):
    print(f"{len(cells)} cells, baseline = mean of {len(bases)} batch(es)")
    for key in KEYS:
        base = [sum(b[c][key] for b in bases) / len(bases) for c in cells]
        diff = [cand[c][key] - m for c, m in zip(cells, base)]
        mean = sum(diff) / len(diff)
        se = math.sqrt(sum((d - mean) ** 2 for d in diff) / max(1, len(diff) - 1) / len(diff))
        print(f"  {key:5} base {sum(base) / len(base):8.2f}  cand {sum(cand[c][key] for c in cells) / len(cells):8.2f}"
              f"  diff {mean:+8.2f} +- {se:.2f}  mde {2.8 * se:.2f}")
    flips = [(cand[c]["win"] > b) - (cand[c]["win"] < b) for c, b in
             ((c, sum(x[c]["win"] for x in bases) / len(bases)) for c in cells)]
    print(f"  wins gained {sum(f > 0 for f in flips)}, lost {sum(f < 0 for f in flips)} (against the baseline mean)")


args = [a for a in sys.argv[1:] if ":" in a or a == "+"]
cand, bases = {}, None
for group in " ".join(args).split(" + "):
    specs = group.split()
    cand.update(load(specs[0]))
    bases = [load(s) for s in specs[1:]] if bases is None else [b | load(s) for b, s in zip(bases, specs[1:])]
cells = sorted(set(cand).intersection(*bases))
report(cand, bases, cells)
if "--split-cap" in sys.argv:
    for name, keep in (("control damage < 500", True), ("control damage >= 500", False)):
        part = [c for c in cells if (sum(b[c]["dmg"] for b in bases) / len(bases) < 500) == keep]
        if part:
            print(f"\n{name}:")
            report(cand, bases, part)
if CAUSE:
    play = lambda r: (r["action"].get("action"), r["action"].get("direction"), r["position"])
    groups = {}
    for c in cells:
        a, b = bases[0][c]["log"], cand[c]["log"]
        first = next((t for t in sorted(a) if t in b and play(a[t]) != play(b[t])), None)
        start = min((t for t in b if b[t]["reason"] == CAUSE), default=None)
        name = "identical" if first is None else "noise" if start is None or first < start else f"caused by {CAUSE}"
        groups.setdefault(name, []).append(c)
    for name, part in sorted(groups.items()):
        print(f"\n{name}:")
        report(cand, bases, part)
