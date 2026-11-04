"""Run one practice-referee match with a given seat lineup (inside WSL / Ubuntu + ROS 2).

Lineup values are "ref" (the official reference robot_node), "thief" (the exploiter in
exploiters.py, aimed at the `target` bot) or a bot name from `versions`, mapped to a
directory that contains an `rmtc_bot` package."""
import json
import os
import signal
import subprocess
import time
import urllib.request
from pathlib import Path


def api(port, path, body=None, timeout=10):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read())


def kill(proc):
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=10)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def rank(teams):
    # Rules §3 order: score, missions, resource value, effective damage, fewer invalid actions.
    order = sorted(teams, key=lambda t: (-t.get("score", 0), -t.get("mission_completed", 0),
                                         -t.get("resource_value", 0), -t.get("effective_damage", 0),
                                         t.get("invalid_actions", 0)))
    return {t["team"]: i + 1 for i, t in enumerate(order)}


def read_env(path):
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return dict(line.strip().split("=", 1) for line in lines if "=" in line and not line.startswith("#"))


def run_match(config, lineup, versions, practice_root, out_dir, port, domain,
              tick_ms=100, max_ticks=500, timeout_margin=120, stage=2, bot_args=(), ai_mode="mock",
              target=None):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "config.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ, ROS_DOMAIN_ID=str(domain), RMTC_INTERNAL_DOMAIN_ID=str(domain + 1),
               ROS_LOCALHOST_ONLY="1", RMW_IMPLEMENTATION="rmw_fastrtps_cpp",
               ROS_LOG_DIR=str(out / "ros-logs"))
    if ai_mode == "real":
        # The referee proxies practice-X tokens to this service; the key never reaches the bots.
        llm = read_env(Path(__file__).with_name(".env"))
        env.update({k: llm[k] for k in ("PRACTICE_LLM_URL", "PRACTICE_LLM_KEY", "PRACTICE_MODEL")})
        bot_args = (*bot_args, "--model", llm["PRACTICE_MODEL"])
    refs = [seat for seat, bot in lineup.items() if bot == "ref"]
    # Exploiters attack the seat of the bot under test.
    target_seat = next((s for s, b in lineup.items() if b == target), None)
    procs = []
    result = {"lineup": lineup, "port": port, "domain": domain, "out": str(out)}
    try:
        referee_log = (out / "referee.log").open("w")
        procs.append(subprocess.Popen(
            ["python3", str(Path(practice_root) / "practice.py"), "--port", str(port), "--stage", str(stage),
             "--tick-ms", str(tick_ms), "--max-ticks", str(max_ticks), "--bots", ",".join(refs),
             "--config", str(out / "config.json"), "--data-dir", str(out / "referee"), "--ai-mode", ai_mode],
            cwd=practice_root, env=env, stdout=referee_log, stderr=subprocess.STDOUT, start_new_session=True))
        deadline = time.monotonic() + 40
        while True:
            try:
                state = api(port, "/api/state")
                if state.get("status", {}).get("ready"):
                    break
            except OSError:
                pass
            if procs[0].poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("referee did not become ready")
            time.sleep(0.5)
        result["session_id"] = state["session_id"]
        bot_env = {k: v for k, v in env.items() if not k.startswith("PRACTICE_LLM")}
        for seat, bot in lineup.items():
            if bot == "ref":
                continue
            log = (out / f"bot_{seat}.stderr").open("w")
            if bot == "thief":
                command = ["python3", str(Path(__file__).with_name("exploiters.py")),
                           "--team", seat, "--target", target_seat, "--port", str(port)]
                cwd = None
            else:
                command = ["python3", "-m", "rmtc_bot", "run", "--stage", str(stage), "--team", seat,
                           "--name", bot, "--ai-url", f"http://127.0.0.1:{port}/v1/chat/completions",
                           "--log", str(out / f"bot_{seat}.jsonl"), *bot_args]
                cwd = versions[bot]
            procs.append(subprocess.Popen(command, cwd=cwd, env=dict(bot_env, RMTC_TEAM_TOKEN="practice-" + seat),
                                          stdout=log, stderr=subprocess.STDOUT, start_new_session=True))
        time.sleep(8)  # rclpy start-up, DDS discovery, known-route planning (~2 s)
        api(port, "/api/resume", {})
        deadline = time.monotonic() + max_ticks * tick_ms / 1000 + timeout_margin
        while time.monotonic() < deadline:
            state = api(port, "/api/state")
            status = state.get("status", {})
            if state.get("fault") or status.get("fault"):
                raise RuntimeError("referee fault: " + str(state.get("fault") or status.get("fault")))
            if status.get("finished"):
                break
            time.sleep(1)
        else:
            raise RuntimeError("match timed out")
        teams = state["board"]["teams"]
        ranks = rank(teams)
        result["teams"] = [dict(t, bot=lineup[t["team"]], rank=ranks[t["team"]]) for t in teams]
        result["ticks"] = status.get("tick")
        result["ok"] = True
    except Exception as exc:  # recorded per match; the batch keeps going
        result["ok"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            api(port, "/api/stop", {}, timeout=20)
        except OSError:
            pass
        for proc in reversed(procs):
            kill(proc)
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result
