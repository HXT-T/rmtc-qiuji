"""Receding-horizon reward routing on memory built only from public frames."""
import heapq
import math
from collections import Counter
from dataclasses import dataclass
from .known_map import solve
from .protocol import DIRECTIONS, OPPOSITE


def add(a, b):
    return a[0] + b[0], a[1] + b[1]


def manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def in_line(a, b, reach):
    """An ordinary attack from a reaches b: same row or column, 1 to `reach` cells."""
    return (a[0] == b[0] or a[1] == b[1]) and 1 <= manhattan(a, b) <= reach


def between(a, c, b):
    """c lies on a shortest path from a to b, nearer to a than b is: it shields b from a."""
    return manhattan(a, c) < manhattan(a, b) and manhattan(a, c) + manhattan(c, b) == manhattan(a, b)


# P(a rival that could reach a resource no later than us takes it), by what we know of the rival
# (visible now / seen before / never seen) and its slack (our arrival - its earliest arrival: < 3, < 8, more).
# The only numbers estimated from data: maximum likelihood on referee truth, 300 mixed-pool matches (docs 9.7).
TAKE = ((0.231, 0.326, 0.393), (0.004, 0.007, 0.128), (0.002, 0.024, 0.052))
# The same, for a rival whose view held the resource where we saw it: it knows the resource (docs 9.12).
KNOWN = (0.509, 0.575, 0.645)


def kind(value):
    """Mission category of a resource by its value."""
    return "high_resources" if value >= 20 else "normal_resources"


class WorldMemory:
    def __init__(self):
        self.seen = set()
        self.observed_at = {}
        self.enemy_sightings = {}
        self.obstacles = set()
        self.obstacle_first_seen = {}
        self.obstacle_last_seen = {}
        self.resources = {}
        self.visits = Counter()
        self.fail_until = {}
        self.collected = Counter()
        self.last_score = 0.0
        self.last_tick = -1
        self.distance_cache = (None, {})  # terrain key -> per-source Dijkstra maps
        self.spawn = None  # own first position: rival spawns are hypothesised from it
        self.ever_seen = {}  # every resource position observed this match -> value
        self.last_sighting = {}  # rival team -> (position, tick) of its latest sighting, kept all match

    def update(self, frame, events=None):
        if events:
            for event in events.events.values():
                if event.kind == "split" and frame.tick >= event.end:
                    # A line first observed during this temporary event is no longer
                    # known to be blocked after expiry. Reobserve before asserting it.
                    for p in list(self.obstacles):
                        if ((p[0] == event.point[0] or p[1] == event.point[1])
                                and event.start <= self.obstacle_first_seen.get(p, -1) < event.end
                                and self.obstacle_last_seen.get(p, event.end) < event.end):
                            self._forget(p)
        if not frame.own["alive"]:
            return
        current_value = frame.score.get("resource_value", 0)
        # Subtype accounting is only inferred when the previously observed resource
        # matches both current location and this frame's actual score increase.
        previous = self.resources.get(frame.position)
        if previous and self.last_tick == frame.tick - 1 and current_value > self.last_score:
            if abs(current_value - self.last_score - previous["value"]) < 1e-6:
                self.collected[kind(previous["value"])] += 1
        self.last_score, self.last_tick = current_value, frame.tick
        px, py = frame.position
        visible = {(x, y) for y in range(max(0, py - frame.radius), min(frame.height, py + frame.radius + 1))
                   for x in range(max(0, px - frame.radius), min(frame.width, px + frame.radius + 1))}
        self.seen.update(visible)
        self.observed_at.update({p: frame.tick for p in visible})
        robots = {e["team"]: e for e in frame.entities if e["type"] == "robot"}
        # Last positions are investigation hypotheses, never current combat truth.
        self.enemy_sightings = {
            team: sighting for team, sighting in self.enemy_sightings.items()
            if frame.tick - sighting["tick"] <= 30
            and not (sighting["pos"] in visible and team not in robots)}
        for team, robot in robots.items():
            self.enemy_sightings[team] = {"pos": robot["pos"], "tick": frame.tick}
            if team != frame.own.get("team"):
                self.last_sighting[team] = robot["pos"], frame.tick
        present_obstacles = {e["pos"] for e in frame.entities if e["type"] == "obstacle"}
        for p in visible - present_obstacles:
            self._forget(p)
        for p in visible:
            self.resources.pop(p, None)
        for entity in frame.entities:
            if entity["type"] == "obstacle":
                self.obstacles.add(entity["pos"])
                self.obstacle_first_seen.setdefault(entity["pos"], frame.tick)
                self.obstacle_last_seen[entity["pos"]] = frame.tick
            elif entity["type"].startswith("resource_"):
                self.resources[entity["pos"]] = dict(entity, last_seen=frame.tick)
                self.ever_seen[entity["pos"]] = entity["value"]
        self.visits[frame.position] += 1
        if self.spawn is None:
            self.spawn = frame.position
        self.fail_until = {p: until for p, until in self.fail_until.items() if until > frame.tick}

    def _forget(self, p):
        self.obstacles.discard(p)
        self.obstacle_first_seen.pop(p, None)
        self.obstacle_last_seen.pop(p, None)


@dataclass
class Route:
    direction: str = ""
    goal: tuple = ()
    reason: str = "wait"
    utility: float = 0.0
    path: tuple = ()


class Planner:
    def __init__(self, config):
        self.config = config
        self.memory = WorldMemory()
        self.patrol_goal = None
        self.exact = (None, [])  # stage 1: (remaining resources, their optimal order)
        self.goal = None  # last tick's sequence target, if the route came from the goal sequence

    def resource_gain(self, frame, value, planned_value=0):
        if self.config.stage == 1:
            return value * 0.2
        left = max(0, self.config.resource_cap - frame.score.get("resource_value", 0) - planned_value)
        scored = min(value, left)
        # A small residual reflects the resource tiebreak, not uncapped points.
        return scored * 0.25 + (value - scored) * 0.012

    def distances(self, start, frame, events, occupied=(), avoid_robots=False):
        """Dijkstra: unknown cells are traversable hypotheses with a small penalty."""
        memory = self.memory
        # Only split lines block and only explosions/splits add hazard; skip the
        # per-edge event scan when no such event is known (identical result).
        timed = any(e.kind in ("split", "explosion") for e in events.events.values())
        key = None
        if not timed and not occupied and not avoid_robots:
            # Without timed events the map depends only on remembered terrain.
            # `seen` only grows within one memory, so its size identifies it.
            key = (frame.width, frame.height, frozenset(memory.obstacles), len(memory.seen),
                   frozenset(memory.fail_until))
            if memory.distance_cache[0] != key:
                memory.distance_cache = (key, {})
            hit = memory.distance_cache[1].get(start)
            if hit:
                return hit
        costs, steps, parent = {start: 0.0}, {start: 0}, {}
        heap = [(0.0, start)]
        # Hot loop: neighbours are precomputed per map size in DIRECTIONS order and lookups
        # are bound locally. Arithmetic and push order match the plain version exactly.
        neighbours = self._neighbours(frame.width, frame.height)
        obstacles, seen, fail_until = memory.obstacles, memory.seen, memory.fail_until
        pop, push, best, inf, tick = heapq.heappop, heapq.heappush, costs.get, math.inf, frame.tick
        while heap:
            cost, p = pop(heap)
            if cost > costs[p] + 1e-9:
                continue
            distance = steps[p] + 1
            for q in neighbours[p]:
                # Planning hypotheses use a time estimate, not hidden world state.
                if q in obstacles or (timed and events.blocked(q, tick + distance - 1)):
                    continue
                if p == start and q in fail_until:
                    continue
                # A robot holds its cell this tick only: it blocks our next step, while further on it is a detour
                # cost, not a wall (a rival crossing a corridor must not erase the goals behind it; docs 9.12 F-d).
                if avoid_robots and q in occupied and p == start:
                    continue
                extra = 0.15 if q not in seen else 0.0
                if timed:
                    extra += events.hazard(q, tick + distance, False) * 0.12
                extra += 5.0 if q in occupied else 0.0
                candidate = cost + 1.0 + extra
                if candidate + 1e-9 < best(q, inf):
                    costs[q], steps[q], parent[q] = candidate, distance, p
                    push(heap, (candidate, q))
        if key is not None:
            memory.distance_cache[1][start] = costs, steps, parent
        return costs, steps, parent

    def _neighbours(self, width, height):
        if getattr(self, "_neighbour_size", None) != (width, height):
            self._neighbour_size = (width, height)
            self._neighbour_table = {
                (x, y): [q for q in (add((x, y), d) for d in DIRECTIONS.values())
                         if 0 <= q[0] < width and 0 <= q[1] < height]
                for y in range(height) for x in range(width)}
        return self._neighbour_table

    def _mission_bonus(self, frame, value, counts):
        done = set(frame.score.get("mission_completed_ids", []))
        bonus, category = 0.0, kind(value)
        for mission in frame.missions:
            if mission["id"] in done:
                continue
            if mission["type"] == category:
                left = max(1, mission["target"] - self.memory.collected[category])
                if counts[category] <= left:
                    bonus += 5.0 / left
            elif mission["type"] == "resource_value":
                left = max(0, mission["target"] - frame.score.get("resource_value", 0))
                if left:
                    before = max(0, counts["planned_value"] - value)
                    bonus += 5 * min(value, max(0, left - before)) / left
        return bonus

    def route(self, frame, events, history, enemies):
        c, memory = self.config, self.memory
        last, self.goal = self.goal, None
        occupied = {e["pos"] for e in enemies}
        costs, steps, parent = self.distances(frame.position, frame, events, occupied, True)
        remaining = max(0, c.horizon - frame.tick)
        if not remaining:
            return Route()
        if c.stage == 2:
            # Escape pre-move hazards before activation, allowing route slack.
            for event in sorted(events.events.values(), key=lambda e: e.start):
                if event.start <= frame.tick:
                    continue
                if event.kind == "explosion":
                    affected = event.contains(frame.position)
                    safe = lambda p: not event.contains(p)
                elif event.kind == "split":
                    affected = ((frame.position[0] == event.point[0] or frame.position[1] == event.point[1])
                                and frame.position != events.spawn)
                    safe = lambda p: (p[0] != event.point[0] and p[1] != event.point[1]) or p == events.spawn
                else:
                    continue
                if affected:
                    exits = [p for p in steps if safe(p)]
                    if exits:
                        target = min(exits, key=lambda p: (steps[p], costs[p], p))
                        slack = event.start - frame.tick - steps[target]
                        if slack <= 2:
                            return self._route_to(target, parent, frame, events, history, "evacuate", 5.0)
        goals = []
        arrivals = self._arrivals(frame, events, enemies) if c.stage == 2 else []
        for p, resource in memory.resources.items():
            if p not in steps or steps[p] > remaining:
                continue
            # Stage 1 has no rivals: a seen resource stays until we collect it.
            staleness = 1.0 if c.stage == 1 else max(0.45, 1 - (frame.tick - resource["last_seen"]) * 0.006)
            # Prioritize narrowly winnable races against visible rivals before safe, uncontested pickups.
            rival = min((manhattan(e["pos"], p) for e in enemies), default=1000)
            urgency = 1.45 if c.stage == 2 and steps[p] <= rival + 1 <= steps[p] + 4 else 1.0
            goals.append({"pos": p, "base": self.resource_gain(frame, resource["value"]) * staleness,
                          "kind": "resource", "value": resource["value"], "expiry": c.horizon,
                          "staleness": staleness, "urgency": urgency,
                          "survive": lambda arrival, p=p: self._keep(p, arrival, arrivals)})
        done = set(frame.score.get("mission_completed_ids", []))
        if c.stage == 2:
            for p, reward, expiry, label in events.opportunities(frame, history):
                if p in steps and frame.tick + steps[p] < expiry:
                    goals.append({"pos": p, "base": reward, "kind": label, "value": 0, "expiry": expiry})
        if c.explore_weight and self.explore_value(frame, 1):
            # Stage 2 weights unseen cells by the chance rivals have not swept them first.
            frontier = []
            unseen = self._unseen_counter(frame, self._territory(frame, events, steps) if c.stage == 2 else None)
            for p in steps:
                if 0 < steps[p] <= remaining:
                    gain = unseen(p)
                    if gain:
                        frontier.append((gain / (costs[p] + 2.5) ** 0.85, gain, p))
            for _, gain, p in sorted(frontier, reverse=True)[:2]:
                goals.append({"pos": p, "base": self.explore_value(frame, gain),
                              "kind": "explore", "value": 0, "expiry": c.horizon})
        if c.stage == 2:
            # The reach mission keeps its value until the match ends and no rival can take it, while known and
            # unseen resources go to rivals: going there now trades a perishable reward for a durable one. So it
            # waits until nothing perishable is left, or until there is only just time to walk there (docs 9.14).
            # Passing over the cell on the way still completes it.
            perishable = any(g["kind"] in ("resource", "explore") for g in goals)
            for mission in frame.missions:
                p = mission.get("position")
                if (mission["type"] == "reach" and mission["id"] not in done and p in steps
                        and (not perishable or remaining <= 2 * steps[p])):
                    goals.append({"pos": p, "base": 5.5, "kind": "mission", "value": 0, "expiry": c.horizon})

        # Candidate truncation is based on reachable value/distance, not input ordering.
        def initial_reward(g):
            counts = Counter({kind(g["value"]): 1, "planned_value": g["value"]})
            survive = g["survive"](steps[g["pos"]]) if "survive" in g else 1.0
            return (g["base"] * survive + (self._mission_bonus(frame, g["value"], counts) if c.stage == 2 and g["value"] else 0)) * g.get("urgency", 1)
        goals.sort(key=lambda g: (-initial_reward(g) / (steps[g["pos"]] + 2), g["pos"], g["kind"]))
        goals = goals[:20]
        pair = {frame.position: (costs, steps)}
        for g in goals:
            if g["pos"] not in pair:
                gc, gs, _ = self.distances(g["pos"], frame, events)
                pair[g["pos"]] = gc, gs
        known = [g["pos"] for g in goals if g["kind"] == "resource"]
        if c.stage == 1 and known and len(memory.ever_seen) >= c.resource_count and len(known) <= 12:
            # Every published resource has been seen, so the rest is deterministic: the exact
            # optimal order (bitmask DP), recomputed only when the remaining set changes.
            if self.exact[0] != frozenset(known):
                points = [frame.position] + known
                d = [[pair[a][1].get(b, 10 ** 9) for b in points] for a in points]
                _, _, order = solve(d, [memory.resources[p]["value"] for p in known], remaining)
                self.exact = frozenset(known), [known[i] for i in order]
            if self.exact[1]:
                return self._route_to(self.exact[1][0], parent, frame, events, history, "resource_exact", 1.0)
        if c.stage == 1 and goals:
            # Minimize a tour of all known resources when the time budget permits.
            # This avoids value-ratio detours while retaining reward search near expiry.
            best_tour = None
            nearest_distance = min(steps[g["pos"]] for g in goals)
            for first in range(len(goals)):
                if steps[goals[first]["pos"]] != nearest_distance:
                    continue
                order, unused = [first], set(range(len(goals))) - {first}
                while unused:
                    current = goals[order[-1]]["pos"]
                    possible = [i for i in unused if goals[i]["pos"] in pair[current][1]]
                    if not possible:
                        break
                    nxt = min(possible, key=lambda i: (pair[current][1][goals[i]["pos"]], i))
                    order.append(nxt)
                    unused.remove(nxt)
                if unused:
                    continue
                def length(route):
                    result, current = 0, frame.position
                    for i in route:
                        target = goals[i]["pos"]
                        if target not in pair[current][1]:
                            return math.inf
                        result += max(1, pair[current][1][target])
                        current = target
                    return result
                # Bounded 2-opt pass removes open-tour crossings.
                for a in range(1, len(order) - 1):
                    for b in range(a + 1, len(order)):
                        candidate = order[:a] + list(reversed(order[a:b + 1])) + order[b + 1:]
                        if length(candidate) < length(order):
                            order = candidate
                total = length(order)
                rank = (total, steps[goals[order[0]]["pos"]], order[0])
                if total <= remaining and (best_tour is None or rank < best_tour[0]):
                    best_tour = rank, order
            if best_tour:
                target = goals[best_tour[1][0]]["pos"]
                return self._route_to(target, parent, frame, events, history, "resource_tour", 1.0)
        # Beam search over short resource/mission tours. Discount later rewards;
        # use distance-efficiency to avoid consuming the entire match on one detour.
        beam = [(0.0, frame.position, 0, 0.0, -1, Counter(), 0)]
        best = (0.0, -1)
        for depth in range(min(c.beam_depth, len(goals))):
            expanded = []
            for _, current, used, reward, first, counts, elapsed in beam:
                distances, lengths = pair[current]
                for i, g in enumerate(goals):
                    if used & (1 << i) or g["pos"] not in lengths:
                        continue
                    length = lengths[g["pos"]]
                    arrival = elapsed + max(1, length)
                    if arrival > remaining or frame.tick + arrival > g["expiry"]:
                        continue
                    new_counts, bonus, base_reward = counts.copy(), 0.0, g["base"]
                    if g["value"] and c.stage == 2:
                        new_counts[kind(g["value"])] += 1
                        new_counts["planned_value"] += g["value"]
                        bonus = self._mission_bonus(frame, g["value"], new_counts)
                        base_reward = (self.resource_gain(frame, g["value"], counts["planned_value"])
                                       * g["staleness"] * g["survive"](arrival))
                    urgency = g.get("urgency", 1) if depth == 0 else 1.0
                    new_reward = reward + (base_reward + bonus) * urgency * (0.91 ** depth)
                    efficiency = new_reward / (arrival + 3.5) ** 0.78
                    first_index = i if first == -1 else first
                    candidate = (efficiency, g["pos"], used | (1 << i), new_reward,
                                 first_index, new_counts, arrival)
                    expanded.append(candidate)
                    if efficiency > best[0]:
                        best = efficiency, first_index
            if not expanded:
                break
            expanded.sort(key=lambda s: (-s[0], s[4], s[2]))
            beam = expanded[:c.beam_width]
        if best[1] >= 0:
            g = goals[best[1]]
            route = self._route_to(g["pos"], parent, frame, events, history, g["kind"], best[0])
            # A step back to where we stood last tick undoes that step: strictly worse than going on with either
            # plan. If last tick's target is still a candidate, keep going there (docs 9.12 F-b).
            back = history.get(frame.tick - 1, {}).get("position")
            h = next((h for h in goals if h["pos"] == last), None)
            if back != frame.position and route.path[1:2] == (back,) and h and h is not g:
                kept = self._route_to(h["pos"], parent, frame, events, history, h["kind"], best[0])
                route = kept if kept.direction else route
            self.goal = route.goal
            return route

        # Once collection/mission opportunities are exhausted, approach visible
        # opponents before exploring empty corners. Only visible enemies influence
        # immediate attack, defense, occupancy and damage calculations.
        if c.stage == 2 and enemies:
            engagements = []
            for enemy in enemies:
                target = enemy["pos"]
                engagements += [(costs[p], enemy["team"], p) for p in steps if in_line(p, target, c.attack_range)]
            if engagements:
                _, team, target = min(engagements)
                enemy = next(e for e in enemies if e["team"] == team)
                # Two robots that each step to line up with the other cross every tick. Attacks resolve
                # before moves, so while a moving enemy can step into our line we wait for it (on an even or
                # better exchange), on half the ticks so a rival using the same rule cannot mirror us. Only
                # below the damage cap: past it our damage scores nothing while the enemy's still may.
                me = frame.position
                lines_up = any(in_line(q, me, c.attack_range) and q not in memory.obstacles
                               for q in (add(enemy["pos"], d) for d in DIRECTIONS.values()))
                if (enemy.get("moved") and lines_up and target != me and frame.own["hp"] >= enemy["hp"]
                        and frame.score.get("effective_damage", 0) < c.damage_cap
                        and not enemy.get("protected", False) and hash((frame.tick, ord(c.team[0]))) % 2 == 0):
                    target = me
                return self._route_to(target, parent, frame, events, history, "engage", 1.2)
        if c.stage == 2:
            sightings = [(costs[s["pos"]] + (frame.tick - s["tick"]) * 0.25,
                          team, s["pos"])
                         for team, s in memory.enemy_sightings.items()
                         if s["pos"] in steps and 0 < steps[s["pos"]] <= remaining]
            if sightings:
                _, _, target = min(sightings)
                return self._route_to(target, parent, frame, events, history, "investigate", 0.9)

        # Exploration frontier: maximize new visible cells per travel cost.
        # Revisits are penalized, so coverage continues after local resources vanish.
        frontier = []
        unseen = self._unseen_counter(frame)
        for p in sorted(steps):
            if steps[p] > remaining or steps[p] == 0:
                continue
            gain = unseen(p)
            if gain:
                utility = gain / (costs[p] + 2.5) ** 0.85 / (1 + memory.visits[p] * 0.4)
                frontier.append((utility, p))
        if frontier:
            utility, target = max(frontier, key=lambda pair: (pair[0], -pair[1][1], -pair[1][0]))
            return self._route_to(target, parent, frame, events, history, "explore", utility * 0.15)
        if c.stage == 2:
            # Revisit old observation areas after coverage is complete. This is a
            # search policy, not access to unseen enemies or their spawn positions.
            target = self.patrol_goal
            if (target not in steps or steps.get(target, 0) == 0
                    or memory.observed_at.get(target, -1) == frame.tick):
                candidates = [p for p in steps if 0 < steps[p] <= remaining
                              and memory.observed_at.get(p, -1) < frame.tick]
                target = max(candidates, key=lambda p: (
                    (frame.tick - memory.observed_at.get(p, -1)) / (costs[p] + 4) ** 0.7,
                    -memory.visits[p], p), default=None)
                self.patrol_goal = target
            if target is not None:
                return self._route_to(target, parent, frame, events, history, "patrol", 0.8)
        return Route()

    def explore_value(self, frame, unseen):
        """Expected points behind `unseen` cells: the published resource total not yet seen
        this match, spread over the cells not yet seen. Stage 2 fades it out by Tick 150 as
        rivals empty the pool; alone in stage 1, by the end of the match as time to collect
        what is found runs out."""
        c, m = self.config, self.memory
        left = max(0.0, c.resource_total - sum(m.ever_seen.values()))
        cells = max(1, frame.width * frame.height - len(m.seen))
        fade = max(0.0, 1 - frame.tick / (150 if c.stage == 2 else c.horizon))
        per_value = 0.25 if c.stage == 2 else self.resource_gain(frame, 1.0)
        return c.explore_weight * unseen * left / cells * per_value * fade

    def _arrivals(self, frame, events, enemies):
        """Per rival: (what we know of it, earliest ticks from now it could stand on a cell), by logic alone.
        Visible: its path steps. Seen before: steps from its last sighting less the ticks since (it moves at
        most one cell a tick), or from its spawn if it may have died since. Never seen: steps from the nearest
        hypothesised spawn less the tick (images of our spawn, 2 cells of slack for their inexactness)."""
        spawn = self._rival_reach(frame, events)
        home = lambda p: max(0, min((rv.get(p, 99) for rv in spawn), default=99) - frame.tick - 2)
        visible = {e["team"]: e["pos"] for e in enemies}
        sees = lambda q: lambda p: max(abs(p[0] - q[0]), abs(p[1] - q[1])) <= frame.radius
        out = []
        for team in sorted(set("ABCD") - {frame.own.get("team")}):
            if team in visible:
                d = self.distances(visible[team], frame, events)[1]
                out.append((0, lambda p, d=d: d.get(p, 99), sees(visible[team])))
            elif team in self.memory.last_sighting:
                pos, tick = self.memory.last_sighting[team]
                d, age = self.distances(pos, frame, events)[1], frame.tick - tick
                out.append((1, lambda p, d=d, age=age: max(0, min(d.get(p, 99) - age, home(p))), sees(pos)))
            else:
                out.append((2, home, lambda p: False))
        return out

    @staticmethod
    def _keep(p, arrival, arrivals):
        """Chance no rival takes the resource at p before we arrive in `arrival` ticks: a rival that cannot
        get there first never does; one that can, takes it at its measured rate, higher if its view held p."""
        keep = 1.0
        for state, earliest, saw in arrivals:
            slack = arrival - earliest(p)
            if slack >= 0:
                keep *= 1 - (KNOWN if saw(p) else TAKE[state])[(slack >= 3) + (slack >= 8)]
        return keep

    def _rival_reach(self, frame, events):
        """Step maps of the hypothesised rivals: at the rotations of our own spawn about the
        map centre (90/180/270 degrees on square maps, else 180), sweeping out from Tick 0."""
        w, h, (x, y) = frame.width, frame.height, self.memory.spawn or frame.position
        images = [(w - 1 - y, x), (w - 1 - x, h - 1 - y), (y, h - 1 - x)] if w == h else [(w - 1 - x, h - 1 - y)]
        return [self.distances(q, frame, events)[1] for q in set(images) - {(x, y)}
                if q not in self.memory.obstacles]

    @staticmethod
    def _clear(p, t, rivals):
        """Chance that no hypothesised rival has reached cell p by tick t: rival j covers
        it with probability sigmoid((t - t_j) / 3), t_j its steps from its spawn."""
        chance = 1.0
        for rival in rivals:
            if p in rival:
                chance *= 1 - 1 / (1 + math.exp(max(-50.0, min(50.0, (rival[p] - t) / 3))))
        return chance

    def _territory(self, frame, events, steps):
        """Chance that each cell is still unvisited by rivals when we can first get there."""
        rivals = self._rival_reach(frame, events)
        return {p: self._clear(p, frame.tick + d, rivals) for p, d in steps.items()}

    def _unseen_counter(self, frame, weight=None):
        """Unseen cells (each counted by `weight`, default 1; unreachable cells by 0 when a
        weight map is given) in the vision window around p, via a 2D prefix sum built once
        per call (O(1) per query instead of rescanning the (2r+1)^2 window)."""
        w, h, r, seen = frame.width, frame.height, frame.radius, self.memory.seen
        table = [[0] * (w + 1) for _ in range(h + 1)]
        for y in range(h):
            row, above, run = table[y + 1], table[y], 0
            for x in range(w):
                if (x, y) not in seen:
                    run += 1 if weight is None else weight.get((x, y), 0.0)
                row[x + 1] = above[x + 1] + run
        def count(p):
            x0, x1 = max(0, p[0] - r), min(w, p[0] + r + 1)
            y0, y1 = max(0, p[1] - r), min(h, p[1] + r + 1)
            return table[y1][x1] - table[y0][x1] - table[y1][x0] + table[y0][x0]
        return count

    def _route_to(self, target, parent, frame, events, history, reason, utility):
        if target == frame.position:
            if reason == "heal":
                for direction, delta in DIRECTIONS.items():
                    p = add(target, delta)
                    if (0 <= p[0] < frame.width and 0 <= p[1] < frame.height
                            and p not in self.memory.obstacles and not events.blocked(p, frame.tick)):
                        sent = OPPOSITE[direction] if events.inverted(frame, history) else direction
                        return Route(sent, target, "heal_reenter", utility, (target, p))
            return Route("", target, reason, utility, (target,))
        path, p = [target], target
        while p != frame.position:
            if p not in parent:
                return Route()
            p = parent[p]
            path.append(p)
        path.reverse()
        delta = path[1][0] - path[0][0], path[1][1] - path[0][1]
        direction = next(k for k, v in DIRECTIONS.items() if v == delta)
        if events.inverted(frame, history):
            direction = OPPOSITE[direction]
        return Route(direction, target, reason, utility, tuple(path))
