"""Low-frequency task planning and a paired primitive-control comparison."""
import time
from dataclasses import asdict
from agent.coder import AgentCoder, extract_policy
from agent.task_planner import GoalPlan, TaskPlanner
from navigation.planner import NavigationGoal
from navigation.runner import NavigationRunner, NavigationSessionError


class MissionRunner:
    """Task parsing is shared by model modes; all actions share one trusted session."""
    def __init__(self, limits=None, max_actions=80, max_observations=160, max_plan_requests=4,
                 max_action_requests=None, **navigation_options):
        self.navigation = NavigationRunner(limits, max_actions, max_observations, **navigation_options)
        self.max_plan_requests = max_plan_requests
        self.max_action_requests = (self.navigation.limits.max_actions
                                    if max_action_requests is None else max_action_requests)
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 1
               for value in (self.max_plan_requests, self.max_action_requests)):
            raise ValueError("Model request budgets must be positive integers")

    def run(self, task, public_targets, *, strategy="hybrid", provider=None,
            reference_order=None, **session_options):
        if strategy not in ("reference", "hybrid", "direct"):
            raise ValueError("Unknown mission strategy")
        if (not public_targets or len(public_targets) > 32
                or any(not isinstance(name, str) or not name or not isinstance(goal, NavigationGoal)
                       for name, goal in public_targets.items())):
            raise ValueError("Mission requires a bounded public target catalogue")
        if strategy != "reference" and provider is None:
            raise ValueError("A model provider is required")
        session = self.navigation.session(task, next(iter(public_targets.values())), **session_options)
        requests, plans, completed_ids, completed_goals, pending = [], [], [], [], []
        counts = {"task-plan": 0, "action": 0}
        planner = TaskPlanner(provider)
        coder = AgentCoder(provider)

        def request(kind, trigger, operation):
            limit = self.max_plan_requests if kind == "task-plan" else self.max_action_requests
            if counts[kind] >= limit:
                raise NavigationSessionError("ModelRequestBudgetExceeded", f"{kind} request budget exhausted")
            counts[kind] += 1
            started = time.perf_counter()
            record = {"kind": kind, "trigger": trigger, "model": None, "response": None,
                      "input_tokens": None, "output_tokens": None, "ttft_ms": None, "error_type": None}
            try:
                response = operation()
                record.update(model=response.model, response=response.text, input_tokens=response.input_tokens,
                              output_tokens=response.output_tokens, ttft_ms=response.ttft_ms)
                return response
            except Exception as exc:
                record["error_type"] = type(exc).__name__
                record["error_message"] = str(exc)
                raise
            finally:
                record["total_ms"] = (time.perf_counter() - started) * 1000
                requests.append(record)

        def make_plan(trigger):
            feedback = {**session.public_context(), "completed_targets": list(completed_ids),
                        "trigger": trigger, "execution_error": session.error_type}
            response = request("task-plan", trigger,
                lambda: planner.request(task.instruction, public_targets, feedback, session.runtime.registry))
            record = {"trigger": trigger, "completed_prefix": list(completed_ids), "accepted": False}
            plans.append(record)
            try:
                plan = GoalPlan.parse(response.text, public_targets)
            except Exception as exc:
                record["error_type"] = type(exc).__name__
                requests[-1]["error_type"] = type(exc).__name__
                raise
            record.update(accepted=True, targets=list(plan.targets))
            if plan.stop_reason is not None:
                record["stop_reason"] = plan.stop_reason
                raise NavigationSessionError("MissionStopped", plan.stop_reason)
            return list(plan.targets)

        try:
            session.initialize()
            # No motion continues while the model is reasoning or waiting on transport.
            session.runtime.emergency_stop()
            if strategy == "reference":
                if not reference_order or any(name not in public_targets for name in reference_order):
                    raise ValueError("Reference mode requires an explicit oracle target order")
                pending = list(reference_order)
                plans.append({"trigger": "reference-oracle", "accepted": True, "targets": list(pending)})
            else:
                pending = make_plan("mission-start")
            while pending:
                name = pending[0]
                goal = public_targets[name]
                factory = None
                if strategy == "direct":
                    def factory(observation):
                        observation["current_goal"] = asdict(goal)
                        observation["completed_targets"] = list(completed_ids)
                        instruction = (task.instruction + "\nCurrent trusted subgoal: " + str(asdict(goal))
                                       + "; completed targets: " + str(completed_ids))
                        # Preserve per-request usage before parsing/validating the Python response.
                        response = request("action", "next-primitive", lambda:
                            coder.request_from_observation(instruction, observation, session.runtime.registry))
                        return extract_policy(response.text)
                status = session.advance(goal, factory)
                if status == "reached":
                    completed_ids.append(name)
                    completed_goals.append(goal)
                    pending.pop(0)
                elif status == "failed":
                    if strategy != "reference" and session.error_type in (
                            "NavigationStalled", "NavigationInformationExhausted"):
                        pending = make_plan(session.error_type)
                        session.stagnant = 0
                        session.error_type = session.error_message = None
                    else:
                        break
        except Exception as exc:
            session.fail(exc.code if isinstance(exc, NavigationSessionError) else type(exc).__name__, str(exc))
        goals = completed_goals + [public_targets[name] for name in pending]
        result = session.result(goals, len(completed_goals))
        result.plans, result.model_requests, result.completed_targets = plans, requests, completed_ids
        result.metrics.update(strategy=strategy, llm_calls=len(requests), plan_calls=counts["task-plan"],
            action_calls=counts["action"], plan_request_budget=self.max_plan_requests,
            action_request_budget=self.max_action_requests, llm_ms=sum(item["total_ms"] for item in requests),
            input_tokens=(sum(item["input_tokens"] or 0 for item in requests)
                          if any(item["input_tokens"] is not None for item in requests) else None),
            output_tokens=(sum(item["output_tokens"] or 0 for item in requests)
                           if any(item["output_tokens"] is not None for item in requests) else None),
            usage_responses=sum(item["input_tokens"] is not None for item in requests))
        return result
