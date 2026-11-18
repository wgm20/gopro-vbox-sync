"""Bounded numeric scene expressions, interpreted without executing scene code."""
from dataclasses import dataclass
import ast
import math
import re

from .gpmf import TelemetryError


MAX_EXPRESSION = 4096
MAX_SOURCE = 10_000
MAX_BINDINGS = 16
MAX_NODES = 256
MAX_DEPTH = 32
_BINDING = re.compile(r"ds:([A-Za-z_][A-Za-z0-9_]{0,63})\{([^{};]{1,256})\}")
_BINARY = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}
_COMPARISON = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==", ast.NotEq: "!="}


def _invalid():
    return TelemetryError("Unsupported or oversized scene maths expression")


def _numeric(value):
    if type(value) not in (int, float):
        return None
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _compile(node, names, depth=0):
    if depth > MAX_DEPTH:
        raise _invalid()
    if isinstance(node, ast.Constant):
        value = _numeric(node.value)
        if value is None:
            raise _invalid()
        return ("number", value)
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        names.add(node.id)
        return ("name", node.id)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        return ("binary", _BINARY[type(node.op)], _compile(node.left, names, depth + 1), _compile(node.right, names, depth + 1))
    if isinstance(node, ast.UnaryOp) and type(node.op) in (ast.UAdd, ast.USub):
        return ("unary", "+" if isinstance(node.op, ast.UAdd) else "-", _compile(node.operand, names, depth + 1))
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _COMPARISON:
        return ("compare", _COMPARISON[type(node.ops[0])], _compile(node.left, names, depth + 1), _compile(node.comparators[0], names, depth + 1))
    raise _invalid()


def _evaluate(node, values):
    kind = node[0]
    if kind == "number":
        return node[1]
    if kind == "name":
        return values[node[1]]
    if kind == "unary":
        value = _evaluate(node[2], values)
        return value if node[1] == "+" else -value
    left, right = _evaluate(node[2], values), _evaluate(node[3], values)
    operation = node[1]
    if kind == "compare":
        if operation == "<": result = left < right
        elif operation == "<=": result = left <= right
        elif operation == ">": result = left > right
        elif operation == ">=": result = left >= right
        elif operation == "==": result = left == right
        else: result = left != right
        return float(result)
    if operation == "+": result = left + right
    elif operation == "-": result = left - right
    elif operation == "*": result = left * right
    else: result = left / right
    if not math.isfinite(result):
        raise ArithmeticError("Non-finite scene maths result")
    return result


@dataclass(frozen=True, slots=True)
class SceneMath:
    tree: tuple
    bindings: tuple

    def sources(self):
        return tuple(source for _, source in self.bindings)

    def evaluate(self, resolve):
        values = {}
        for name, source in self.bindings:
            value = _numeric(resolve(source))
            if value is None:
                return None
            values[name] = value
        try:
            return _numeric(_evaluate(self.tree, values))
        except ArithmeticError:
            return None


def parse_scene_math(text, parse_leaf):
    """Parse one native maths.eqn with ordinary, non-nested datasource bindings."""
    if len(text) > MAX_SOURCE or not text.startswith("maths.eqn{") or not text.endswith("}"):
        raise _invalid()
    parts = text[len("maths.eqn{"):-1].split(";")
    expression, declarations = parts[0].strip(), parts[1:]
    if not expression or len(expression) > MAX_EXPRESSION or not 1 <= len(declarations) <= MAX_BINDINGS:
        raise _invalid()
    bindings = {}
    for declaration in declarations:
        match = _BINDING.fullmatch(declaration.strip())
        if match is None or match[1] in bindings:
            raise _invalid()
        # A leaf has only its channel and optional scaling; nested maths are excluded.
        if len(match[2].split()) > 2:
            raise _invalid()
        bindings[match[1]] = parse_leaf(match[2])
    try:
        parsed = ast.parse(expression, mode="eval")
        for count, _ in enumerate(ast.walk(parsed), 1):
            if count > MAX_NODES:
                raise _invalid()
        names = set()
        tree = _compile(parsed.body, names)
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise _invalid() from exc
    if names != bindings.keys():
        raise TelemetryError("Scene maths variables and datasource bindings do not match")
    return SceneMath(tree, tuple(bindings.items()))
