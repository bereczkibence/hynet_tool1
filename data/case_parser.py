"""Read literal case dictionaries without importing or executing Python files."""
from __future__ import annotations

import ast
import operator
import numpy as np

MAX_CASE_BYTES = 5 * 1024 * 1024


def _numeric_constant(node):
    """Allow bounded literal arithmetic in auxiliary constants, never calls."""
    if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return _numeric_constant(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
    operations = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow}
    if isinstance(node, ast.BinOp) and type(node.op) in operations:
        left, right = _numeric_constant(node.left), _numeric_constant(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 16:
            raise ValueError("constant exponent exceeds 16")
        return operations[type(node.op)](left, right)
    raise ValueError("not literal numeric arithmetic")


def read_case_text(text: str, *, filename: str = "case.py", function: str | None = None) -> dict:
    if len(text.encode("utf-8")) > MAX_CASE_BYTES:
        raise ValueError(f"{filename}: case exceeds 5 MiB.")
    try:
        tree = ast.parse(text.lstrip("\ufeff"), filename=filename)
    except (SyntaxError, RecursionError) as exc:
        raise ValueError(f"{filename}: invalid Python case: {exc}") from exc
    numpy_names, array_names, dtype_names = set(), set(), set()
    functions, aliases = {}, {}

    def fail(node, message="unsupported executable statement"):
        raise ValueError(f"{filename}:{getattr(node, 'lineno', 1)}: {message}; only data-only case definitions are supported.")

    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, ast.Import) and all(a.name == "numpy" for a in node.names):
            numpy_names.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module == "numpy" and not node.level:
            for alias in node.names:
                if alias.name == "array":
                    array_names.add(alias.asname or alias.name)
                elif alias.name in {"float64", "float32", "int64", "int32"}:
                    dtype_names.add(alias.asname or alias.name)
                else:
                    fail(node, "unsupported NumPy import")
        elif isinstance(node, ast.FunctionDef):
            if node.name in functions:
                fail(node, "duplicate case function")
            functions[node.name] = node
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Name):
            aliases[node.targets[0].id] = node.value.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                _numeric_constant(node.value)
            except (ValueError, ArithmeticError):
                fail(node, "only literal numeric module constants are supported")
        elif isinstance(node, ast.If) and ast.dump(node.test) == ast.dump(ast.parse('__name__ == "__main__"', mode="eval").body):
            # Reporting examples are never evaluated.
            continue
        else:
            fail(node)
    if function:
        seen = set()
        while function in aliases:
            if function in seen:
                raise ValueError(f"{filename}: cyclic function aliases.")
            seen.add(function)
            function = aliases[function]
        if function not in functions:
            raise ValueError(f"{filename}: case function {function!r} not found.")
    elif len(functions) == 1:
        function = next(iter(functions))
    else:
        raise ValueError(f"{filename}: expected one case function; found {len(functions)}. Specify a function explicitly.")
    node = functions[function]
    if node.decorator_list or node.args.args or node.args.posonlyargs or node.args.kwonlyargs or node.args.vararg or node.args.kwarg:
        fail(node, "case functions must be undecorated and take no arguments")
    variables = {}

    def value(expr):
        if isinstance(expr, ast.Name) and expr.id in variables:
            return variables[expr.id]
        if isinstance(expr, ast.Call):
            func = expr.func
            is_array = (isinstance(func, ast.Name) and func.id in array_names) or (
                isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id in numpy_names and func.attr == "array")
            if not is_array or len(expr.args) != 1:
                fail(expr, "only literal NumPy arrays are allowed")
            for keyword in expr.keywords:
                dtype = keyword.value
                allowed = isinstance(dtype, ast.Name) and dtype.id in dtype_names | {"float", "int"}
                allowed |= isinstance(dtype, ast.Attribute) and isinstance(dtype.value, ast.Name) and dtype.value.id in numpy_names and dtype.attr in {"float64", "float32", "int64", "int32"}
                if keyword.arg != "dtype" or not allowed:
                    fail(expr, "unsupported array keyword")
            try:
                return np.array(ast.literal_eval(expr.args[0]), dtype=float)
            except (ValueError, TypeError, RecursionError) as exc:
                fail(expr, f"array must contain literal rectangular numeric data ({exc})")
        try:
            return ast.literal_eval(expr)
        except (ValueError, TypeError, RecursionError):
            fail(expr, "computed expressions are not supported")

    result = None
    for statement in node.body:
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
            continue
        if result is not None:
            fail(statement, "statements after the case return are not supported")
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            resolved = value(statement.value)
            if isinstance(target, ast.Name):
                variables[target.id] = resolved
            elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                container = variables.get(target.value.id)
                key = value(target.slice)
                if not isinstance(container, dict) or not isinstance(key, str):
                    fail(statement, "only dictionary field assignments are supported")
                container[key] = resolved
            else:
                fail(statement)
        elif isinstance(statement, ast.Return):
            result = value(statement.value)
            if not isinstance(result, dict):
                fail(statement, "case must return a dictionary")
        else:
            fail(statement)
    if result is None:
        raise ValueError(f"{filename}: case function does not return a dictionary.")
    return result
