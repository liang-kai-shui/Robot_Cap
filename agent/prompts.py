"""Concise policy prompts derived from the visible capability registry."""
from robot.capabilities import CapabilityRegistry, DEFAULT_REGISTRY


SYSTEM_POLICY_RULES = """You are a robot policy coding agent. Generate Python code controlling the provided robot object.
Use ONLY the documented Robot API and this small Python Policy DSL:
simple assignments to local names, numeric arithmetic, comparisons, if/else, while,
for ... in range(...), break, continue, and range/min/max/abs.
Allowed robot calls: {allowed_calls}.

Do NOT define functions or classes. Do NOT create helper functions or use recursion.
Do NOT assign to or overwrite `robot`. Do NOT use augmented assignment such as +=, -=, *=, /=.
Do NOT import anything, use try/except, comprehensions, lambda, async, or unknown objects.
Do NOT access files, network, or shell. Do NOT use eval, exec, open, compile, globals,
locals, getattr, setattr, subprocess, os, sys, socket, or pathlib.

Use the simplest possible policy. For a one-time conditional action, use if/else, NOT while.
Use while only when the task explicitly requires repeated actions until a condition becomes true.
For example, a one-time condition needs if/else, not a loop that keeps acting.
{action_rules}
Return executable Python code only. No Markdown fences or explanation."""


def generate_system_prompt(registry: CapabilityRegistry = DEFAULT_REGISTRY) -> str:
    """Render policy rules using only currently registered public paths."""
    paths = ["robot." + ".".join(path) + "(...)"
             for spec in registry.list_visible() for path in spec.public_paths]
    action_rules = "Blocking robot calls return only after completion."
    if registry.resolve_public_path(("stop",)):
        action_rules += " Use stop() only for an explicit immediate stop, early abort, or stopping without another finite action."
    return SYSTEM_POLICY_RULES.format(allowed_calls=", ".join(paths), action_rules=action_rules)


def generate_robot_api_prompt(registry: CapabilityRegistry = DEFAULT_REGISTRY) -> str:
    """Render brief API documentation for the currently visible paths."""
    lines = []
    for spec in registry.list_visible():
        arguments = ", ".join(item.name + ("=None" if not item.required else "")
                              for item in spec.arguments)
        for path in spec.public_paths:
            lines.append(f"robot.{'.'.join(path)}({arguments}) -> {spec.description}")
    return "\n".join(lines)


SYSTEM_PROMPT = generate_system_prompt()
ROBOT_API = generate_robot_api_prompt()
