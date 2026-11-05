"""One watched stage-2 match (run inside WSL; the desktop launcher calls this). Pick the map, our
seat, the opponents and the tick length; the referee's page opens in the Windows browser.

Our bot is the working tree of this repository (the current best version). Opponents are the
q1030 and v8 snapshots in ~/rmtc-eval/versions, other teams' programs in the opponent pool (ff, ls,
mi; eval/README.md), the best version itself, the official reference robot ('ref') or the
exploiter ('thief').
"""
import json
import random
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batch import MAPS, SEATS  # noqa: E402
from maps import generate  # noqa: E402
from match import api, run_match  # noqa: E402

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


def main():
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
    out = EVAL / "runs" / "play" / datetime.now().strftime("%Y%m%d-%H%M%S")
    url = f"http://127.0.0.1:{PORT}"
    print(f"\n地图 {seed}，席位 " + "  ".join(f"{s}={b}" for s, b in lineup.items()))
    print(f"裁判页面 {url}（自动打开）；记录保存在 {out}")
    threading.Thread(target=open_browser, args=(url,), daemon=True).start()
    result = run_match(config, lineup, versions, str(EVAL / "practice"), out, port=PORT, domain=DOMAIN,
                       tick_ms=tick_ms, target="best")
    if not result.get("ok"):
        print("\n对局失败：" + result.get("error", "?"))
        return
    print(f"\n{'名次':<4}{'席位':<4}{'程序':<10}{'得分':>7}{'资源':>6}{'伤害':>7}{'任务':>5}")
    for t in sorted(result["teams"], key=lambda t: t["rank"]):
        print(f"{t['rank']:<6}{t['team']:<6}{t['bot']:<12}{t.get('score', 0):>7.1f}{t.get('resource_value', 0):>7.0f}"
              f"{t.get('effective_damage', 0):>8.0f}{t.get('mission_completed', 0):>5}")


if __name__ == "__main__":
    main()
