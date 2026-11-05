"""Full-information variant of the bot (evaluation only; docs/赛制公平性.md 6, R3).

Runs the bot of the current directory unchanged, except that after every perception its map memory
is overwritten with the practice referee's truth (/api/state): every cell seen, all obstacles and
resources, and every alive rival's position as a fresh sighting. Its gap to the normal bot is the
price of the 9x9 vision. Started by a version's run_bot.sh: omniscient.py SEAT NAME OUT_DIR.
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.getcwd())
from rmtc_bot.__main__ import main  # noqa: E402
from rmtc_bot.planner import WorldMemory, kind  # noqa: E402

seat, name, out = sys.argv[1:4]
STATE = os.environ["RMTC_AI_URL"].split("/v1/")[0] + "/api/state"
observe = WorldMemory.update


def update(self, frame, events=None):
    observe(self, frame, events)
    with urllib.request.urlopen(STATE, timeout=1) as r:
        view = json.loads(r.read())["board"]["map_view"]
    cells = {(x, y) for x in range(frame.width) for y in range(frame.height)}
    self.seen |= cells
    self.observed_at.update(dict.fromkeys(cells, frame.tick))
    self.obstacles = {tuple(p) for p in view["obstacles"]}
    self.resources = {}
    for item in view["resources"]:
        p, value = tuple(item["position"]), item["value"]
        self.resources[p] = {"type": "resource_" + ("high" if kind(value) == "high_resources" else "normal"),
                             "pos": p, "value": value, "last_seen": frame.tick}
        self.ever_seen[p] = value
    self.enemy_sightings = {r["team"]: {"pos": tuple(r["position"]), "tick": frame.tick}
                            for r in view["robots"] if r["alive"] and r["team"] != seat}


WorldMemory.update = update
sys.exit(main(["run", "--stage", "2", "--team", seat, "--name", name, "--ai-url", os.environ["RMTC_AI_URL"],
               "--log", f"{out}/bot_{seat}.jsonl"]))
