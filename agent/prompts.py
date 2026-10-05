from robot.capabilities import CAPABILITIES


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
For example, "if the path is short, stop; otherwise move once" needs an if/else,
not a loop that keeps moving. Call robot.stop() when the task is complete.
Return executable Python code only. No Markdown fences or explanation."""

SYSTEM_PROMPT = SYSTEM_POLICY_RULES.format(
    allowed_calls=", ".join(f"robot.{item.name}(...)" for item in CAPABILITIES.values()))


def generate_robot_api_prompt(registry=CAPABILITIES) -> str:
    """Render concise legacy API documentation from the capability registry."""
    lines = []
    for item in registry.values():
        arguments = ", ".join(name + ("=None" if i >= item.required else "")
                              for i, name in enumerate(item.arguments))
        lines.append(f"robot.{item.name}({arguments}) -> {item.description}")
    lines.append("move max 2m/action; turn max 180 degrees/action; heading 0=+X, 90=+Y.")
    return "\n".join(lines)


ROBOT_API = generate_robot_api_prompt()
