"""Exact screening without playing. The bot is a function of the frames it receives, so until a candidate
first acts differently from the control, its match would replay the control's frames exactly. Feeding the
control's logged frames (eval/match.py sets RMTC_LOG_FRAMES) to the candidate offline finds that tick; a
cell where it never comes is identical to the control and needs no match (`batch.py --screen`).

  python3 eval/replay.py CONTROL_RUN:BOT CANDIDATE_DIR     per cell: first tick the candidate differs, or -
"""
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor


def ours(row, bot):
    """Seat and log of `bot` in one results.jsonl row."""
    team = next(t["team"] for t in row["teams"] if t["bot"] == bot)
    return team, f"{row['out']}/bot_{team}.jsonl"


def first_divergence(row, bot, candidate):
    team, path = ours(row, bot)
    records = [json.loads(line) for line in open(path)]
    frames = [json.dumps(json.loads(r["raw"])) for r in records if r["kind"] == "frame"]
    if not frames:
        raise ValueError(f"{path} has no frames: play the control with the current eval/match.py")
    control = {r["tick"]: r["action"] for r in records if r["kind"] == "decision"}
    stdout = subprocess.run(["python3", "-m", "rmtc_bot", "replay", "--stage", "2", "--team", team, "--name", bot],
                            cwd=candidate, input="\n".join(frames) + "\n", capture_output=True, text=True,
                            check=True).stdout
    for result in map(json.loads, stdout.splitlines()):
        if result and control.get(result["tick"]) != result:
            return result["tick"]
    return None


def screen(spec, candidate, parallel=12):
    """{(seed, rotation): (control row, first divergence tick or None)} for every cell of the control run."""
    run, bot = spec.rsplit(":", 1)
    rows = [r for r in map(json.loads, open(f"{run}/results.jsonl")) if r.get("ok")]
    with ThreadPoolExecutor(parallel) as pool:
        ticks = pool.map(lambda r: first_divergence(r, bot, candidate), rows)
        return {(str(r["seed"]), r["rotation"]): (r, t) for r, t in zip(rows, ticks)}


if __name__ == "__main__":
    for (seed, rotation), (_, tick) in sorted(screen(sys.argv[1], sys.argv[2]).items()):
        print(f"{seed:>6} rot {rotation}: {'-' if tick is None else tick}")
