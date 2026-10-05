"""Sparse observed grid. No simulator, backend, or hidden world access."""
import math
from collections import Counter
from navigation.observation import RangeObservation

Cell = tuple[int, int]
DIRECTIONS = ((1, 0), (0, 1), (-1, 0), (0, -1))


class ObservedGrid:
    def __init__(self, resolution=.25, margin=.15, origin=(0.0, 0.0)):
        if (not math.isfinite(resolution) or resolution <= 0 or
                not math.isfinite(margin) or margin < 0 or
                not all(math.isfinite(value) for value in origin)):
            raise ValueError("Invalid grid settings")
        self.resolution, self.margin, self.origin = resolution, margin, origin
        self.free: set[Cell] = set()
        self.occupied: set[Cell] = set()
        self.scanned: set[tuple[Cell, int]] = set()
        self.visits: Counter = Counter()
        self.revision = 0

    def cell(self, x, y):
        return tuple(math.floor((value - offset) / self.resolution + .5)
                     for value, offset in zip((x, y), self.origin))

    def point(self, cell):
        return tuple(offset + index * self.resolution for index, offset in zip(cell, self.origin))

    @staticmethod
    def neighbors(cell):
        return [(cell[0] + dx, cell[1] + dy) for dx, dy in DIRECTIONS]

    def state(self, cell):
        return "occupied" if cell in self.occupied else "free" if cell in self.free else "unknown"

    def update(self, observation: RangeObservation, max_age_seconds=2.0):
        observation.require_fresh(max_age_seconds)
        pose = observation.pose
        start = self.cell(pose.x, pose.y)
        before = self.free.copy(), self.occupied.copy(), self.scanned.copy()
        self.free.add(start)
        self.occupied.discard(start)
        angle = math.radians(pose.heading)
        dx, dy = math.cos(angle), math.sin(angle)
        # Range already includes VirtualWorld.clearance. Do not inflate it again.
        safe_length = max(0.0, observation.distance_m - self.margin)
        samples = max(1, math.ceil(safe_length / (self.resolution / 4)))
        for index in range(samples + 1):
            distance = safe_length * index / samples
            cell = self.cell(pose.x + distance * dx, pose.y + distance * dy)
            # A rounded cell center beyond the measured safe interval is not known free.
            x, y = self.point(cell)
            along = (x - pose.x) * dx + (y - pose.y) * dy
            if -1e-8 <= along <= safe_length + 1e-8 and cell not in self.occupied:
                self.free.add(cell)
        if observation.hit:
            endpoint = self.cell(pose.x + observation.distance_m * dx,
                                 pose.y + observation.distance_m * dy)
            if endpoint != start:
                self.occupied.add(endpoint)
                self.free.discard(endpoint)
        direction = round(pose.heading / 90) % 4
        if abs((pose.heading - direction * 90 + 180) % 360 - 180) < 1e-6:
            self.scanned.add((start, direction))
        if before != (self.free, self.occupied, self.scanned):
            self.revision += 1

    def frontiers(self, reachable):
        for cell in sorted(reachable):
            for direction, neighbor in enumerate(self.neighbors(cell)):
                if self.state(neighbor) == "unknown" and (cell, direction) not in self.scanned:
                    yield cell, direction

    def to_dict(self):
        return {"resolution_m": self.resolution, "origin": self.origin,
                "space": "configuration", "revision": self.revision,
                "free_cells": sorted(self.free), "occupied_cells": sorted(self.occupied),
                "scanned_views": [(cell, direction) for cell, direction in sorted(self.scanned)]}
