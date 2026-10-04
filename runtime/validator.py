import ast
from runtime.errors import PolicySyntaxError, UnsafePolicyError


ROBOT_METHODS = {"move", "turn", "stop", "get_pose", "get_distance", "get_state"}
BUILTINS = {"range", "min", "max", "abs"}
DATA_FIELDS = {"x", "y", "heading", "pose", "stopped", "collision", "last_action", "action_count"}
BLOCKED_NAMES = {"os", "sys", "subprocess", "socket", "pathlib", "requests", "httpx", "shutil", "pickle", "marshal", "ctypes", "multiprocessing", "threading", "open", "eval", "exec", "compile", "globals", "locals", "vars", "dir", "getattr", "setattr", "delattr", "input", "help", "breakpoint", "__import__"}


class PolicyValidator(ast.NodeVisitor):
    """Small syntax and name whitelist. This is not a hostile-code sandbox."""

    def validate(self, policy: str) -> None:
        try:
            tree = ast.parse(policy, mode="exec")
        except SyntaxError as exc:
            raise PolicySyntaxError(str(exc)) from exc
        self.names = {"robot", *BUILTINS}
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
        self.visit(node.targets[0])

    def visit_For(self, node):
        if not isinstance(node.target, ast.Name):
            raise UnsafePolicyError("Only a simple loop variable is allowed")
        if not isinstance(node.iter, ast.Call) or not isinstance(node.iter.func, ast.Name) or node.iter.func.id != "range":
            raise UnsafePolicyError("For loops must iterate over range()")
        self.visit(node.iter)
        self.visit(node.target)
        for part in (*node.body, *node.orelse): self.visit(part)

    def visit_Call(self, node):
        if node.keywords:
            raise UnsafePolicyError("Keyword arguments are forbidden")
        if isinstance(node.func, ast.Name):
            if node.func.id not in BUILTINS:
                raise UnsafePolicyError(f"Forbidden function: {node.func.id}")
        elif isinstance(node.func, ast.Attribute):
            if not isinstance(node.func.value, ast.Name) or node.func.value.id != "robot" or node.func.attr not in ROBOT_METHODS:
                raise UnsafePolicyError("Only documented robot methods may be called")
        else:
            raise UnsafePolicyError("Indirect calls are forbidden")
        for arg in node.args: self.visit(arg)

    def visit_Attribute(self, node):
        if node.attr.startswith("__") or node.attr not in DATA_FIELDS:
            raise UnsafePolicyError(f"Forbidden attribute: {node.attr}")
        if isinstance(node.value, ast.Name):
            if node.value.id == "robot":
                raise UnsafePolicyError("Robot attributes are only callable API methods")
            self.visit(node.value)
        elif isinstance(node.value, (ast.Attribute, ast.Call)):
            self.visit(node.value)
        else:
            raise UnsafePolicyError("Invalid attribute access")
