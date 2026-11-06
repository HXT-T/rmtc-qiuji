"""Watched stage-2 matches (run inside WSL; the desktop launcher calls this). Pick the map, our
seat, the opponents and the tick length; the referee's page opens in the Windows browser. Starting it
ends the previous deployment first. While it runs, type n (end this deployment and set up a new match) or q
(end) and Enter. The referee and bots stay up after a match: "部署新练习" on the page starts the next session
with them, and every finished session prints its result.

Our bot is the working tree of this repository (the current best version). Opponents are the
q1030 and v8 snapshots in ~/rmtc-eval/versions, other teams' programs in the opponent pool (ff, ls,
mi; eval/README.md), the best version itself, the official reference robot ('ref') or the
exploiter ('thief').
"""
import json
import os
import random
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batch import MAPS, SEATS  # noqa: E402
from maps import generate  # noqa: E402
from match import api, rank, run_match  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EVAL = Path.home() / "rmtc-eval"
PORT, DOMAIN = 8090, 60  # outside the batch slots (ports 8100+, domains 40+)


def ask(title, options, default=1):
    print(f"\n{title}")
    for i, (label, _) in enumerate(options, 1):
        print(f"  {i}. {label}")
    while True:
        text = input(f"编号 [回车 = {default}]: ").strip() or str(default)
        if text.isdigit() and 1 <= int(text) <= len(options):
            return options[int(text) - 1][1]
        print("  请输入列表里的编号")


def pick_opponents(options):
    print("\n对手（输入一个编号 = 三个席位相同；或用逗号分开三个编号，如 1,2,1）")
    for i, (label, _) in enumerate(options, 1):
        print(f"  {i}. {label}")
    while True:
        text = input("编号 [回车 = 1]: ").strip() or "1"
        picks = [p.strip() for p in text.split(",")]
        if len(picks) in (1, 3) and all(p.isdigit() and 1 <= int(p) <= len(options) for p in picks):
            return [options[int(p) - 1][1] for p in picks * (3 // len(picks))]
        print("  格式不对，再试一次")


def open_browser(url):
    # Wait until the referee serves its page, then hand the URL to Windows.
    for _ in range(80):
        try:
            if api(PORT, "/api/state", timeout=2).get("status", {}).get("ready"):
                subprocess.run(["explorer.exe", url], check=False)
                return
        except OSError:
            pass
        time.sleep(0.5)


def stop_previous():
    """End the last deployment: an earlier play.py and every referee/bot process it started (they carry its
    internal ROS domain in their environment), including ones orphaned when its window was closed."""
    mark = f"RMTC_INTERNAL_DOMAIN_ID={DOMAIN + 1}".encode()
    victims = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit() or int(d.name) in (os.getpid(), os.getppid()):
            continue
        try:
            if b"eval/play.py" in (d / "cmdline").read_bytes() or mark in (d / "environ").read_bytes().split(b"\0"):
                victims.append(int(d.name))
        except OSError:
            continue
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in victims:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        for _ in range(20):
            if not any(Path(f"/proc/{pid}").exists() for pid in victims):
                break
            time.sleep(0.25)
    if victims:
        print(f"已结束上一次部署（{len(victims)} 个进程）")


def report(state):
    teams = state["board"]["teams"]
    ranks = rank(teams)
    print(f"\n场次 {state['session_id']} 结束")
    print(f"{'名次':<4}{'席位':<4}{'程序':<10}{'得分':>7}{'资源':>6}{'伤害':>7}{'任务':>5}")
    for t in sorted(teams, key=lambda t: ranks[t["team"]]):
        print(f"{ranks[t['team']]:<6}{t['team']:<6}{t.get('team_name', ''):<12}{t.get('score', 0):>7.1f}"
              f"{t.get('resource_value', 0):>7.0f}{t.get('effective_damage', 0):>8.0f}{t.get('mission_completed', 0):>5}")
    print(HELP, flush=True)


HELP = "输入 n 回车 = 结束并部署新比赛；q 回车 = 结束。网页上的「部署新练习」也可以开下一场。"


def setup():
    seed = ask("地图", [(f"固定图 {n}", n) for n in range(1, 10)] + [("随机新图", "new"), ("随机图（指定种子）", "seed")])
    if seed == "new":
        seed = f"r{random.randrange(10 ** 6)}"
    elif seed == "seed":
        seed = "r" + input("种子（整数）: ").strip().lstrip("r")
    config = (generate(int(seed[1:])) if str(seed).startswith("r")
              else json.loads((MAPS / f"stage2_{seed}.json").read_text(encoding="utf-8")))
    seat = ask("我方席位", [(s, s) for s in SEATS])
    versions = {"best": str(ROOT)}
    labels = {"q1030": "q1030（比赛提交版 freeze-1030）", "v8": "v8（上一版最佳，没有后期等待）",
              "ff": "ff（faultfaust/rmtc，C++）", "ls": "ls（lishirun/rmtc-bot，C++）", "mi": "mi（Miraculy/RMTC_coding，C++）"}
    versions.update({name: str(EVAL / "versions" / name) for name in labels
                     if any((EVAL / "versions" / name / f).exists() for f in ("rmtc_bot", "run_bot.sh"))})
    choices = [(labels[name], name) for name in labels if name in versions] + [
        ("best（当前最佳，自己对自己）", "best"), ("ref（官方陪练车）", "ref"), ("thief（读真值抢资源的 exploiter）", "thief")]
    opponents = iter(pick_opponents(choices))
    lineup = {s: "best" if s == seat else next(opponents) for s in SEATS}
    tick_ms = ask("速度", [("正式 500 ms/Tick（约 4 分钟）", 500), ("快速 100 ms/Tick（约 1 分钟）", 100)])
    print(f"\n地图 {seed}，席位 " + "  ".join(f"{s}={b}" for s, b in lineup.items()))
    return config, lineup, versions, tick_ms


def play(config, lineup, versions, tick_ms):
    """One deployment; returns the command that ended it ('n' or 'q')."""
    out = EVAL / "runs" / "play" / datetime.now().strftime("%Y%m%d-%H%M%S")
    url = f"http://127.0.0.1:{PORT}"
    print(f"裁判页面 {url}（自动打开）；记录保存在 {out}\n{HELP}")
    threading.Thread(target=open_browser, args=(url,), daemon=True).start()
    stop, reported, result = threading.Event(), set(), {}

    def watch(state):
        if state.get("status", {}).get("finished") and state.get("session_id") not in reported:
            reported.add(state["session_id"])
            report(state)
        return stop.is_set()

    def run():
        result.update(run_match(config, lineup, versions, str(EVAL / "practice"), out, port=PORT, domain=DOMAIN,
                                tick_ms=tick_ms, target="best", serve=watch))
        if not stop.is_set():
            print(f"\n部署中止：{result.get('error', '?')}\n{HELP}", flush=True)

    worker = threading.Thread(target=run)
    worker.start()
    command = "q"
    try:
        while True:
            command = input().strip().lower()
            if command in ("n", "q"):
                break
            print(HELP)
    except EOFError:
        command = "q"
    finally:
        # Ending (also by closing the window or Ctrl+C) stops the referee and every bot.
        stop.set()
        print("正在结束本次部署…", flush=True)
        worker.join()
    return command


def main():
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: sys.exit(0))
    stop_previous()
    try:
        while play(*setup()) == "n":
            print("\n部署新比赛")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
