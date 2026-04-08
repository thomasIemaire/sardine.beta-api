"""
Évaluation sécurisée d'expressions et résolution de templates.
- Dot notation : data.user.name
- Templates : "Bonjour {{data.user.name}}"
- Évaluation AST safe (pas d'exec/eval brute)
"""

import ast
import operator as op
import re
from typing import Any


# ─── Helpers dot notation ────────────────────────────────────────


def get_value(obj: Any, path: str) -> Any:
    parts = path.split(".")
    current = obj
    for part in parts:
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def set_value(obj: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    current = obj
    for part in parts[:-1]:
        if part not in current or not isinstance(current.get(part), dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


def delete_value(obj: dict, path: str) -> bool:
    parts = path.split(".")
    current = obj
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    if isinstance(current, dict) and parts[-1] in current:
        del current[parts[-1]]
        return True
    return False


# ─── Évaluateur AST sécurisé ─────────────────────────────────────

SAFE_OPS = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.Mod: op.mod,
    ast.Eq: op.eq,
    ast.NotEq: op.ne,
    ast.Lt: op.lt,
    ast.LtE: op.le,
    ast.Gt: op.gt,
    ast.GtE: op.ge,
    ast.Is: op.is_,
    ast.IsNot: op.is_not,
    ast.USub: op.neg,
    ast.Not: op.not_,
}


class _SafeEvaluator(ast.NodeVisitor):
    def __init__(self, namespace: dict):
        self.namespace = namespace

    def visit_Expression(self, node):
        return self.visit(node.body)

    def visit_Constant(self, node):
        return node.value

    def visit_Name(self, node):
        if node.id in self.namespace:
            return self.namespace[node.id]
        if node.id == "True":
            return True
        if node.id == "False":
            return False
        if node.id == "None":
            return None
        if node.id == "len":
            return len
        return None

    def visit_Attribute(self, node):
        obj = self.visit(node.value)
        if isinstance(obj, dict):
            return obj.get(node.attr)
        if isinstance(obj, list) and node.attr == "length":
            return len(obj)
        return getattr(obj, node.attr, None)

    def visit_Subscript(self, node):
        obj = self.visit(node.value)
        key = self.visit(node.slice)
        if isinstance(obj, (dict, list)):
            try:
                return obj[key]
            except (KeyError, IndexError, TypeError):
                return None
        return None

    def visit_BoolOp(self, node):
        if isinstance(node.op, ast.And):
            result = True
            for v in node.values:
                result = self.visit(v)
                if not result:
                    return result
            return result
        elif isinstance(node.op, ast.Or):
            result = False
            for v in node.values:
                result = self.visit(v)
                if result:
                    return result
            return result

    def visit_UnaryOp(self, node):
        operand = self.visit(node.operand)
        fn = SAFE_OPS.get(type(node.op))
        if fn:
            return fn(operand)
        raise ValueError(f"Opérateur non supporté: {type(node.op).__name__}")

    def visit_BinOp(self, node):
        left = self.visit(node.left)
        right = self.visit(node.right)
        fn = SAFE_OPS.get(type(node.op))
        if fn:
            return fn(left, right)
        raise ValueError(f"Opérateur non supporté: {type(node.op).__name__}")

    def visit_Compare(self, node):
        left = self.visit(node.left)
        for cmp_op, comparator in zip(node.ops, node.comparators, strict=False):
            right = self.visit(comparator)
            if isinstance(cmp_op, ast.In):
                if right is None:
                    return False
                result = left in right
            elif isinstance(cmp_op, ast.NotIn):
                if right is None:
                    return True
                result = left not in right
            else:
                fn = SAFE_OPS.get(type(cmp_op))
                if not fn:
                    raise ValueError(f"Opérateur non supporté: {type(cmp_op).__name__}")
                result = fn(left, right)
            if not result:
                return False
            left = right
        return True

    def visit_IfExp(self, node):
        test = self.visit(node.test)
        return self.visit(node.body) if test else self.visit(node.orelse)

    def visit_Call(self, node):
        func = self.visit(node.func)
        if func is len:
            args = [self.visit(a) for a in node.args]
            return len(args[0]) if args else 0
        raise ValueError("Les appels de fonction ne sont pas autorisés")

    def visit_List(self, node):
        return [self.visit(e) for e in node.elts]

    def visit_Dict(self, node):
        return {self.visit(k): self.visit(v) for k, v in zip(node.keys, node.values, strict=False)}

    def visit_Tuple(self, node):
        return tuple(self.visit(e) for e in node.elts)

    def generic_visit(self, node):
        raise ValueError(f"Expression non supportée: {type(node).__name__}")


def evaluate(expression: str, context) -> Any:
    """Évalue une expression de manière sécurisée contre un ExecutionContext."""
    namespace = {
        "data": context.data,
        "variables": context.variables,
        "metadata": context.metadata,
    }
    try:
        # Compatibilité JS : === et !==
        expr = expression.replace("===", "==").replace("!==", "!=")
        tree = ast.parse(expr, mode="eval")
        evaluator = _SafeEvaluator(namespace)
        return evaluator.visit(tree)
    except Exception as e:
        raise ValueError(f"Erreur d'évaluation de l'expression '{expression}': {e}")


# ─── Résolution de templates ─────────────────────────────────────

_TEMPLATE_RE = re.compile(r"\{\{(.+?)\}\}")


def resolve_template(template: str, context) -> str:
    """Remplace les placeholders {{expression}} par leur valeur évaluée."""
    def replace_match(match):
        expr = match.group(1).strip()
        try:
            val = evaluate(expr, context)
            return str(val) if val is not None else ""
        except Exception:
            return match.group(0)

    return _TEMPLATE_RE.sub(replace_match, template)
