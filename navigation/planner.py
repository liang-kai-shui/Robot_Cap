"""A* over observed free cells plus bounded frontier exploration."""
import heapq
import math
from collections import deque
from dataclasses import dataclass
from navigation.grid import ObservedGrid
from navigation.observation import RangeObservation


@dataclass(frozen=True)
class NavigationGoal:
    x: float
    y: float
    tolerance: float = .08

    def __post_init__(self):
        if (not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(value) for value in (self.x, self.y, self.tolerance))
                or self.tolerance <= 0):
            raise ValueError("Invalid public navigation goal")

    def reached(self, pose):
        return math.hypot(pose.x - self.x, pose.y - self.y) <= self.tolerance


@dataclass(frozen=True)
class NavigationAction:
    operation: str
    value: float
    purpose: str
    target: tuple[float, float]


def astar(grid: ObservedGrid, start, goal):
    """Return a 4-connected route entirely inside observed free space."""
    if start not in grid.free or goal not in grid.free:
        return None
    distance = {start: 0}
    parent = {}
    queue = [(0, start)]
    while queue:
        _, cell = heapq.heappop(queue)
        if cell == goal:
            route = [cell]
            while cell != start:
                cell = parent[cell]
                route.append(cell)
            return route[::-1]
        for neighbor in grid.neighbors(cell):
            if neighbor not in grid.free:
                continue
            cost = distance[cell] + 1
            if cost < distance.get(neighbor, math.inf):
                distance[neighbor] = cost
                parent[neighbor] = cell
                heuristic = abs(neighbor[0] - goal[0]) + abs(neighbor[1] - goal[1])
                heapq.heappush(queue, (cost + heuristic, neighbor))
    return None


class LocalNavigator:
    """Persistent episode map and exploration state; consumes observations only."""
    def __init__(self, goal: NavigationGoal, origin, resolution=.25, margin=.15, max_step=1.5,
                 max_observation_age=2.0, heading_tolerance_deg=1e-6, distance_scale_bound=1.0):
        if not math.isfinite(max_step) or max_step <= 0:
            raise ValueError("Navigation step must be positive and finite")
        self.goal = goal
        if not math.isfinite(distance_scale_bound) or distance_scale_bound < 1:
            raise ValueError("Distance scale bound must be at least one")
        self.heading_tolerance_deg, self.distance_scale_bound = heading_tolerance_deg, distance_scale_bound
        self.grid = ObservedGrid(resolution, margin, origin, heading_tolerance_deg)
        self.max_step = max_step
        self.max_observation_age = max_observation_age
        self.replans = 0
        self.last_frontier = None
        self._last_cell = None

    def set_goal(self, goal: NavigationGoal):
        """Change the objective while retaining this episode's observations."""
        if goal != self.goal:
            self.goal = goal
            self.last_frontier = None

    def decide(self, observation: RangeObservation):
        self.grid.update(observation, self.max_observation_age)
        pose = observation.pose
        start = self.grid.cell(pose.x, pose.y)
        if start != self._last_cell:
            self.grid.visits[start] += 1
            self._last_cell = start
        if self.goal.reached(pose):
            return None
        self.replans += 1
        goal_cell = self.grid.cell(self.goal.x, self.goal.y)
        # Align and reobserve before any exact final approach, including off-grid goals.
        if start == goal_cell:
            action = self._toward((self.goal.x, self.goal.y), observation, "goal")
            if action is not None:
                return action
        route = astar(self.grid, start, goal_cell)
        if route and len(route) > 1:
            return self._route_action(route, observation, "route")

        # Reachable component is also used to score observation positions.
        distances = {start: 0}
        pending = deque([start])
        while pending:
            cell = pending.popleft()
            for neighbor in self.grid.neighbors(cell):
                if neighbor in self.grid.free and neighbor not in distances:
                    distances[neighbor] = distances[cell] + 1
                    pending.append(neighbor)
        candidates = []
        frontiers = list(self.grid.frontiers(distances))
        for cell, direction in frontiers:
            projected = self.grid.point(self.grid.neighbors(cell)[direction])
            score = (math.hypot(projected[0] - self.goal.x, projected[1] - self.goal.y)
                     + .2 * distances[cell] * self.grid.resolution
                     + .3 * self.grid.visits[cell])
            candidates.append((score, cell, direction))
        if not candidates:
            return None  # Observations exhausted; this does not prove global unreachability.
        # Keep an observation target until its view is sampled. Re-ranking while
        # travelling can otherwise bounce between nearby unobserved positions.
        if self.last_frontier in frontiers:
            target_cell, direction = self.last_frontier
        else:
            _, target_cell, direction = min(candidates)
        self.last_frontier = target_cell, direction
        if target_cell == start:
            angle = (direction * 90 - pose.heading + 180) % 360 - 180
            if abs(angle) <= self.heading_tolerance_deg:
                return None
            return NavigationAction("turn", round(angle, 9), "scan", self.grid.point(target_cell))
        route = astar(self.grid, start, target_cell)
        return self._route_action(route, observation, "explore")

    def _route_action(self, route, observation, purpose):
        # Merge only collinear free edges; unknown cells and corners remain boundaries.
        end = route[1]
        direction = (end[0] - route[0][0], end[1] - route[0][1])
        for before, after in zip(route[1:], route[2:]):
            if (after[0] - before[0], after[1] - before[1]) != direction:
                break
            end = after
        return self._toward(self.grid.point(end), observation, purpose)

    def _toward(self, target, observation, purpose):
        pose = observation.pose
        dx, dy = target[0] - pose.x, target[1] - pose.y
        bearing = math.degrees(math.atan2(dy, dx)) % 360
        angle = (bearing - pose.heading + 180) % 360 - 180
        if abs(angle) > self.heading_tolerance_deg:
            return NavigationAction("turn", round(angle, 9), purpose, target)
        allowed = max(0.0, observation.conservative_distance_m - self.grid.margin) / self.distance_scale_bound
        distance = min(math.hypot(dx, dy), self.max_step, allowed)
        if distance < 1e-6:
            return None
        return NavigationAction("move", round(distance, 9), purpose, target)
