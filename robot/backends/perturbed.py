"""Opt-in, seeded static-world faults. Truth is exposed only to the test harness."""
import math
import random
import threading
import time
from dataclasses import asdict, dataclass, replace
from navigation.observation import RangeObservation
from robot.backends.virtual import VirtualBackend
from robot.state import Pose
from runtime.errors import RobotActionTimeoutError, RobotEmergencyStopError
from runtime.limits import RuntimeLimits


@dataclass(frozen=True)
class PerturbationProfile:
    range_error_m: float = 0.0
    dropout_probability: float = 0.0
    sample_age_seconds: float = 0.0
    move_error_fraction: float = 0.0
    turn_error_degrees: float = 0.0
    odometry_scale_error: float = 0.0
    initial_x_error_m: float = 0.0
    initial_y_error_m: float = 0.0
    initial_heading_error_degrees: float = 0.0
    action_delay_seconds: float = 0.0

    def __post_init__(self):
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
               for value in asdict(self).values()):
            raise ValueError("Perturbations must be finite numeric values")
        if (min(self.range_error_m, self.sample_age_seconds, self.turn_error_degrees,
                self.action_delay_seconds) < 0 or not 0 <= self.dropout_probability <= 1
                or not 0 <= self.move_error_fraction < 1 or not -.5 <= self.odometry_scale_error <= .5):
            raise ValueError("Invalid perturbation bounds")


PROFILES = {
    "ideal": PerturbationProfile(),
    "range-3cm": PerturbationProfile(range_error_m=.03),
    "dropout-5pct": PerturbationProfile(dropout_probability=.05),
    "age-250ms": PerturbationProfile(sample_age_seconds=.25),
    "stale-2500ms": PerturbationProfile(sample_age_seconds=2.5),
    "move-8pct": PerturbationProfile(move_error_fraction=.08),
    "turn-08deg": PerturbationProfile(turn_error_degrees=.8),
    "odometry-3pct": PerturbationProfile(odometry_scale_error=.03),
    "offset-20cm": PerturbationProfile(initial_x_error_m=.2),
    "delay-20ms": PerturbationProfile(action_delay_seconds=.02),
    "combined": PerturbationProfile(range_error_m=.03, dropout_probability=.02,
        move_error_fraction=.05, turn_error_degrees=.5, odometry_scale_error=.01),
}


class PerturbedVirtualBackend(VirtualBackend):
    """Reported pose is odometry; geometry uses an independent true pose.

    Noise is bounded uniform noise, not a calibrated hardware model. Sample age
    is timestamp backdating, while action delay is real cooperative waiting.
    No true state, fault RNG or world is registered as a worker capability.
    """
    def __init__(self, world, pose, limits=None, *, profile=PerturbationProfile(), seed=0):
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("Fault seed must be an integer")
        base = limits or RuntimeLimits()
        # Request limits remain enforced by Runtime; simulated actuator overshoot
        # must be permitted in the private physics layer to measure its effects.
        physics_limits = replace(base,
            max_move_distance=base.max_move_distance * (1 + profile.move_error_fraction),
            max_turn_angle=base.max_turn_angle + profile.turn_error_degrees)
        super().__init__(world, pose, physics_limits)
        self.profile, self.seed = profile, seed
        self._estimate = Pose(pose.x + profile.initial_x_error_m, pose.y + profile.initial_y_error_m,
                              (pose.heading + profile.initial_heading_error_degrees) % 360)
        self._streams = {name: random.Random(seed + offset)
                         for name, offset in (("range", 11), ("dropout", 23), ("move", 37), ("turn", 53))}
        self._lock = threading.RLock()
        self._interrupted = threading.Event()
        self.last_sample = None
        self.truth_events = []
        self._next_truth_command = 1
        self.sensor_samples = self.sensor_dropouts = self.cancellations = 0

    def truth_snapshot(self):
        """Private evaluation input; never registered as a capability."""
        return self.robot.snapshot()

    def snapshot(self):
        return replace(self.robot.snapshot(), pose=self._estimate)

    def get_pose(self, *, cancel_event=None, deadline=None):
        self.robot._record("GET_POSE")
        return self._estimate

    def get_state(self, *, cancel_event=None, deadline=None):
        self.robot._record("GET_STATE")
        return self.snapshot()

    def get_distance(self, *, cancel_event=None, deadline=None):
        self.sensor_samples += 1
        raw = super().get_distance(cancel_event=cancel_event, deadline=deadline)
        dropped = self._streams["dropout"].random() < self.profile.dropout_probability
        self.sensor_dropouts += dropped
        limit = 2.0
        hit = raw <= limit
        value = (min(limit, max(0.0, raw + self._streams["range"].uniform(
                    -self.profile.range_error_m, self.profile.range_error_m))) if hit else limit)
        self.last_sample = RangeObservation(self._estimate, value if not dropped else 0.0,
            limit, hit if not dropped else False, not dropped,
            time.time() - self.profile.sample_age_seconds, "sensors.get_distance",
            uncertainty_m=self.profile.range_error_m)
        # None represents a failed read and remains valid strict JSON in trace.
        return None if dropped else value

    def adapt_observation(self, reading):
        """Reuse metadata of the just-completed registered sensor sample."""
        if self.last_sample is None:
            raise ValueError("No completed sensor sample")
        return self.last_sample

    def _check_cancel(self, cancel_event, deadline):
        if self._interrupted.is_set():
            self.cancellations += 1
            raise RobotEmergencyStopError("Simulated action interrupted by emergency stop")
        if ((cancel_event is not None and cancel_event.is_set())
                or (deadline is not None and time.monotonic() >= deadline)):
            self.cancellations += 1
            raise RobotActionTimeoutError("Simulated action cancelled at its deadline")

    def _event(self, kind, operation, phase, command, state, command_id=None):
        self.truth_events.append({"timestamp": time.time(), "kind": kind, "capability_id": operation, "phase": phase,
            "command_id": command_id, "request": command, "state": asdict(state)})

    def _action(self, operation, requested, speed, cancel_event, deadline):
        with self._lock:
            self._interrupted.clear()
            self._check_cancel(cancel_event, deadline)
            before = self.truth_snapshot()
            command_id = self._next_truth_command
            self._next_truth_command += 1
            self._event("action", "motion." + operation, "started", {"requested": requested}, before, command_id)
        try:
            until = time.monotonic() + self.profile.action_delay_seconds
            while time.monotonic() < until:
                self._check_cancel(cancel_event, deadline)
                self._interrupted.wait(min(.005, max(0.0, until - time.monotonic())))
            with self._lock:
                self._check_cancel(cancel_event, deadline)
                if operation == "move":
                    actual = requested * (1 + self._streams["move"].uniform(
                        -self.profile.move_error_fraction, self.profile.move_error_fraction))
                    # The in-memory geometric commit is bounded and atomic under
                    # this short lock. Cancellation is checked immediately before it.
                    self.robot.move(actual, speed)
                    angle = math.radians(self._estimate.heading)
                    estimated_distance = actual * (1 + self.profile.odometry_scale_error)
                    self._estimate = Pose(self._estimate.x + estimated_distance * math.cos(angle),
                        self._estimate.y + estimated_distance * math.sin(angle), self._estimate.heading)
                else:
                    actual = requested + self._streams["turn"].uniform(
                        -self.profile.turn_error_degrees, self.profile.turn_error_degrees)
                    self.robot.turn(actual, speed)
                    self._estimate = replace(self._estimate, heading=(self._estimate.heading + actual) % 360)
                self._event("action", "motion." + operation, "completed",
                            {"requested": requested, "actual": actual}, self.truth_snapshot(), command_id)
        except Exception:
            self._event("action", "motion." + operation, "failed", {"requested": requested}, self.truth_snapshot(), command_id)
            raise

    def move(self, distance, speed=None, *, cancel_event=None, deadline=None):
        self._action("move", distance, speed, cancel_event, deadline)

    def turn(self, angle, speed=None, *, cancel_event=None, deadline=None):
        self._action("turn", angle, speed, cancel_event, deadline)

    def stop(self, *, cancel_event=None, deadline=None):
        super().stop(cancel_event=cancel_event, deadline=deadline)
        self._event("action", "motion.stop", "completed", {}, self.truth_snapshot())

    def emergency_stop(self):
        with self._lock:
            self._interrupted.set()
            super().emergency_stop()
            self._event("safety", "system.emergency_stop", "completed", {}, self.truth_snapshot())
