from agent.prompts import SYSTEM_PROMPT


def test_prompt_describes_restricted_policy_contract():
    prompt = SYSTEM_PROMPT.lower()
    for concept in ("function", "helper function", "class", "recursion", "import",
                    "try/except", "comprehensions", "lambda", "robot", "overwrite",
                    "augmented assignment", "+=", "-=", "for ... in range",
                    "robot.move", "robot.get_distance"):
        assert concept in prompt


def test_prompt_distinguishes_one_time_if_from_repeated_while():
    prompt = SYSTEM_PROMPT.lower()
    assert "one-time conditional" in prompt
    assert "if/else, not while" in prompt
    assert "while only when" in prompt
    assert "repeated actions" in prompt
