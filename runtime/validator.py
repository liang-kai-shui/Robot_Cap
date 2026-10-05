"""Small AST whitelist driven by the currently registered public API."""
import ast
from robot.capabilities import CapabilityRegistry, DEFAULT_REGISTRY
from runtime.errors import PolicySyntaxError, UnsafePolicyError


BUILTINS = {"range", "min", "max", "abs"}
BLOCKED_NAMES = {"os", "sys", "subprocess", "socket", "pathlib", "requests", "httpx", "shutil", "pickle", "marshal", "ctypes", "multiprocessing", "threading", "open", "eval", "exec", "compile", "globals", "locals", "vars", "dir", "getattr", "setattr", "delattr", "input", "help", "breakpoint", "__import__"}


def robot_path(node: ast.AST) -> tuple[str, ...] | None:
    """Extract a complete robot attribute path, without accepting other roots."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    return tuple(reversed(parts)) if isinstance(node, ast.Name) and node.id == "robot" else None


class PolicyValidator(ast.NodeVisitor):
    """Whitelist syntax, registered calls, and schema-declared result fields."""

    def __init__(self, registry: CapabilityRegistry | None = None):
        self.registry = registry if registry is not None else DEFAULT_REGISTRY

    def validate(self, policy: str) -> None:
        """Raise a policy error if code exceeds the current registry or DSL."""
        try:
            tree = ast.parse(policy, mode="exec")
        except SyntaxError as exc:
            raise PolicySyntaxError(str(exc)) from exc
        self.names = {"robot", *BUILTINS}
        self.value_fields: dict[str, dict | None] = {}
        self.visit(tree)

    def generic_visit(self, node):
        allowed = (ast.Module, ast.Expr, ast.Assign, ast.Name, ast.Constant, ast.Call,
                   ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.If, ast.While,
                   ast.For, ast.Break, ast.Continue, ast.Pass, ast.Load, ast.Store,
                   ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
                   ast.UAdd, ast.USub, ast.Not, ast.And, ast.Or, ast.Eq, ast.NotEq,
                   ast.Lt, ast.LtE, ast.Gt, ast.GtE)
        if not isinstance(node, allowed):
            raise UnsafePolicyError(f"Forbidden syntax: {type(node).__name__}")
        super().generic_visit(node)

    def visit_Name(self, node):
        if node.id.startswith("__") or node.id in BLOCKED_NAMES:
            raise UnsafePolicyError(f"Forbidden name: {node.id}")
        if isinstance(node.ctx, ast.Store):
            if node.id in {"robot", *BUILTINS}:
                raise UnsafePolicyError(f"Cannot overwrite: {node.id}")
            self.names.add(node.id)
        elif node.id not in self.names:
            raise UnsafePolicyError(f"Unknown name: {node.id}")

    def visit_Assign(self, node):
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            raise UnsafePolicyError("Only assignment to a simple local name is allowed")
        self.visit(node.value)
        target = node.targets[0]
        self.visit(target)
        self.value_fields[target.id] = self._fields(node.value)

    def visit_For(self, node):
        if not isinstance(node.target, ast.Name):
            raise UnsafePolicyError("Only a simple loop variable is allowed")
        if not isinstance(node.iter, ast.Call) or not isinstance(node.iter.func, ast.Name) or node.iter.func.id != "range":
            raise UnsafePolicyError("For loops must iterate over range()")
        self.visit(node.iter)
        self.visit(node.target)
        for part in (*node.body, *node.orelse):
            self.visit(part)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name):
            if node.func.id not in BUILTINS or node.keywords:
                raise UnsafePolicyError("Only allowed builtins without keywords may be called")
        else:
            path = robot_path(node.func)
            item = self.registry.resolve_public_path(path) if path else None
            if item is None:
                raise UnsafePolicyError("Only registered robot capability paths may be called")
            if any(keyword.arg is None for keyword in node.keywords):
                raise UnsafePolicyError("Keyword expansion is forbidden")
            names = [keyword.arg for keyword in node.keywords]
            if len(names) != len(set(names)):
                raise UnsafePolicyError("Duplicate keyword argument")
            try:
                item.spec.bind(tuple(node.args), {keyword.arg: keyword.value for keyword in node.keywords})
            except TypeError as exc:
                raise UnsafePolicyError(str(exc)) from exc
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def _fields(self, node):
        if isinstance(node, ast.Name):
            return self.value_fields.get(node.id)
        if isinstance(node, ast.Call):
            path = robot_path(node.func)
            item = self.registry.resolve_public_path(path) if path else None
            return item.spec.return_fields if item else None
        if isinstance(node, ast.Attribute):
            parent = self._fields(node.value)
            return parent.get(node.attr) if isinstance(parent, dict) else None
        return None

    def visit_Attribute(self, node):
        if node.attr.startswith("_") or robot_path(node) is not None:
            raise UnsafePolicyError("Robot attributes are only callable registered paths")
        parent = self._fields(node.value)
        if not isinstance(parent, dict) or node.attr not in parent:
            raise UnsafePolicyError(f"Forbidden result field: {node.attr}")
        self.visit(node.value)
