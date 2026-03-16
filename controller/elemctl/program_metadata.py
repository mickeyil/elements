"""Helpers for extracting lightweight metadata from program source."""

from __future__ import annotations

import ast
import math


def extract_metadata(source: str, filename: str) -> tuple[float, float]:
    """Extract BEAT and DURATION numeric literals from DSL source."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as e:
        raise ValueError(f"syntax error: {e}") from e

    beat = None
    duration = None

    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if target.id not in ('BEAT', 'DURATION'):
            continue
        if not isinstance(node.value, ast.Constant):
            raise ValueError(f"{target.id} must be a numeric literal")
        value = node.value.value
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(
                f"{target.id} must be a numeric literal, got {type(value).__name__}"
            )
        if isinstance(value, float) and (math.isinf(value) or math.isnan(value)):
            raise ValueError(f"{target.id} must be finite")
        if value <= 0:
            raise ValueError(f"{target.id} must be positive, got {value}")
        if target.id == 'BEAT':
            if beat is not None:
                raise ValueError("duplicate BEAT assignment")
            beat = float(value)
        else:
            if duration is not None:
                raise ValueError("duplicate DURATION assignment")
            duration = float(value)

    if beat is None:
        raise ValueError("missing BEAT")
    if duration is None:
        raise ValueError("missing DURATION")

    return beat, duration


def extract_strips(source: str, filename: str = '<string>') -> list[str] | None:
    """Extract literal strip names from DSL source.

    Returns:
    - list[str] in first-seen order when all encountered strip() calls use
      literal first arguments
    - None when no strip() calls are found, or when a strip() call is too
      dynamic to summarize safely
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as e:
        raise ValueError(f"syntax error: {e}") from e

    direct_names = {'strip'}
    module_aliases = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == 'elements.dsl':
            for alias in node.names:
                if alias.name == 'strip':
                    direct_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == 'elements.dsl':
                    module_aliases.add(alias.asname or alias.name.rsplit('.', 1)[-1])

    found: list[str] = []
    seen: set[str] = set()
    saw_strip_call = False

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_strip_call = (
            isinstance(func, ast.Name)
            and func.id in direct_names
        ) or (
            isinstance(func, ast.Attribute)
            and func.attr == 'strip'
            and isinstance(func.value, ast.Name)
            and func.value.id in module_aliases
        )
        if not is_strip_call:
            continue
        saw_strip_call = True

        if not node.args:
            return None
        arg0 = node.args[0]
        if not isinstance(arg0, ast.Constant) or not isinstance(arg0.value, str) or not arg0.value:
            return None
        if arg0.value not in seen:
            seen.add(arg0.value)
            found.append(arg0.value)

    if not saw_strip_call:
        return None
    return found
