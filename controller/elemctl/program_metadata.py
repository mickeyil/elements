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
