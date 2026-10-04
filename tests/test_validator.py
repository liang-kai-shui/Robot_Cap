import pytest
from runtime.errors import PolicySyntaxError, UnsafePolicyError
from runtime.validator import PolicyValidator


@pytest.mark.parametrize("policy", [
    "robot.move(1)\nrobot.stop()",
    "distance = robot.get_distance()\nif distance > 1:\n    robot.move(0.5)",
    "while robot.get_distance() > 1:\n    robot.move(0.1)",
    "for i in range(3):\n    robot.turn(90)",
    "x = robot.get_pose().x\nif x > 1: robot.stop()",
])
def test_allowed(policy):
    PolicyValidator().validate(policy)


@pytest.mark.parametrize("policy", [
    "import os", "from os import system", "open('x')", "eval('1')", "exec('pass')",
    "robot.__class__", "robot.fly()", "unknown()", "x = unknown", "robot = 1",
    "f = robot.move", "robot.get_pose().__class__", "[x for x in range(3)]",
    "def f(): pass", "try:\n    pass\nexcept:\n    pass",
])
def test_rejected(policy):
    with pytest.raises(UnsafePolicyError): PolicyValidator().validate(policy)


def test_syntax_error():
    with pytest.raises(PolicySyntaxError): PolicyValidator().validate("if:")


@pytest.mark.parametrize("policy", ["x = 2\nx -= 1", "x = 2\nx += 1"])
def test_augmented_assignment_remains_forbidden(policy):
    with pytest.raises(UnsafePolicyError, match="Forbidden syntax: AugAssign"):
        PolicyValidator().validate(policy)


def test_model_style_helper_function_remains_forbidden():
    policy = "def safe_move(distance):\n    robot.move(distance)\nsafe_move(1)"
    with pytest.raises(UnsafePolicyError, match="Forbidden syntax: FunctionDef"):
        PolicyValidator().validate(policy)
