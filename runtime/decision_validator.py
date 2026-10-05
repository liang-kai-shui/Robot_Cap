"""The interactive mode executes one literal capability action per observation."""
import ast
from robot.capabilities import CapabilityRegistry
from runtime.errors import UnsafePolicyError
from runtime.validator import PolicyValidator, robot_path


def validate_decision_policy(policy: str, registry: CapabilityRegistry) -> None:
    """Retain the regular trust boundary and narrow only the interactive contract."""
    PolicyValidator(registry).validate(policy)
    tree = ast.parse(policy)
    if (len(tree.body) != 1 or not isinstance(tree.body[0], ast.Expr)
            or not isinstance(tree.body[0].value, ast.Call)):
        raise UnsafePolicyError("Interactive policy must contain exactly one direct action call")
    call = tree.body[0].value
    path = robot_path(call.func)
    item = registry.resolve_public_path(path) if path else None
    if item is None or item.spec.observation:
        raise UnsafePolicyError("Interactive policy must call a registered action, not an observation")
    for argument in [*call.args, *(keyword.value for keyword in call.keywords)]:
        if isinstance(argument, ast.Constant):
            continue
        if (isinstance(argument, ast.UnaryOp) and isinstance(argument.op, (ast.USub, ast.UAdd))
                and isinstance(argument.operand, ast.Constant)
                and isinstance(argument.operand.value, (int, float))
                and not isinstance(argument.operand.value, bool)):
            continue
        raise UnsafePolicyError("Interactive action arguments must be literal values")
