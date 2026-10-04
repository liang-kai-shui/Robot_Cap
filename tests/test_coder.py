import pytest
from agent.coder import extract_policy
from runtime.errors import PolicyGenerationError


def test_extract_single_fence_with_explanation():
    assert extract_policy("Policy:\n```python\nrobot.stop()\n```\nDone.") == "robot.stop()"


def test_reject_ambiguous_fences():
    with pytest.raises(PolicyGenerationError):
        extract_policy("```python\nrobot.stop()\n```\n```python\nrobot.move(1)\n```")
