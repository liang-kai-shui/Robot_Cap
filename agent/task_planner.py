"""Bounded task metadata generation, separate from executable Python policies."""
import json
import re
from dataclasses import asdict, dataclass
from agent.prompts import generate_robot_api_prompt
from runtime.errors import PolicyGenerationError


@dataclass(frozen=True)
class GoalPlan:
    targets: tuple[str, ...]
    stop_reason: str | None = None

    @classmethod
    def parse(cls, text, public_targets):
        text = text.strip()
        if "```" in text:
            blocks = re.findall(r"```(?:json)?[ \t]*\n(.*?)\n```", text, re.DOTALL)
            if len(blocks) != 1:
                raise PolicyGenerationError("Expected one unambiguous goal plan")
            text = blocks[0]
        try:
            data = json.loads(text, object_pairs_hook=_unique_object)
        except (ValueError, TypeError) as exc:
            raise PolicyGenerationError("Invalid goal plan JSON") from exc
        if isinstance(data, dict) and set(data) == {"stop_reason"}:
            reason = data["stop_reason"]
            if isinstance(reason, str) and 1 <= len(reason.strip()) <= 200:
                return cls((), reason.strip())
            raise PolicyGenerationError("Stop reason must be a nonempty string of at most 200 characters")
        if (not isinstance(data, dict) or set(data) != {"targets"}
                or not isinstance(data["targets"], list) or not 1 <= len(data["targets"]) <= 8
                or any(not isinstance(name, str) or name not in public_targets for name in data["targets"])):
            raise PolicyGenerationError("Goal plan must contain 1..8 explicitly public target IDs")
        return cls(tuple(data["targets"]))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


class TaskPlanner:
    def __init__(self, provider):
        self.provider = provider

    def request(self, instruction, public_targets, feedback, registry):
        context = {"public_targets": {name: asdict(goal) for name, goal in public_targets.items()},
                   "feedback": feedback}
        system = (
            'Interpret the user mission using only the public target catalogue. '
            'Return exactly JSON {"targets":["ID",...]} with the remaining requested targets in order. '
            'Include every requested visit and explicit return; preserve repeated visits. '
            'A completed prefix is already executed; omit it on replanning, but keep later requested revisits. '
            'Never invent target IDs or coordinates. Do not include unrequested catalogue targets. '
            'No code, explanations, additional keys or hidden map assumptions. Maximum 8 visits. '
            'If the mission cannot continue safely, return JSON {"stop_reason":"brief reason"} instead. '
            'Stopping is an abort, never a claim that the user mission is completed. '
            'Motion planning and stopped arrival are performed by the trusted navigation session. '
            'Feedback about exhausted local information does not prove global unreachability.')
        return self.provider.generate_policy(instruction, generate_robot_api_prompt(registry),
                                             json.dumps(context, ensure_ascii=False), system)
