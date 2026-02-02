"""Rules YAML loading and lightweight validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from .planner import SUPPORTED_RULE_TYPES


def load_rules(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Rules file not found: {path}")
    if not path.is_file():
        raise FileNotFoundError(f"Rules path is not a file: {path}")

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Failed to parse rules YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("Rules YAML must be a mapping at the top level")

    checks = data.get("checks")
    if checks is None:
        raise ValueError("Rules YAML missing required 'checks' section")
    if not isinstance(checks, list):
        raise ValueError("Rules YAML 'checks' must be a list")

    return data


def validate_rules_data(
    data: dict[str, Any],
    columns: set[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    checks = data.get("checks", [])
    if not isinstance(checks, list):
        return ["Rules YAML 'checks' must be a list."]

    for ci, check in enumerate(checks):
        if not isinstance(check, dict):
            errors.append(f"checks[{ci}] must be a mapping.")
            continue
        rules = check.get("rules", [])
        if not isinstance(rules, list):
            errors.append(f"checks[{ci}].rules must be a list.")
            continue
        for ri, rule in enumerate(rules):
            if not isinstance(rule, dict):
                errors.append(f"checks[{ci}].rules[{ri}] must be a mapping.")
                continue
            rule_id = rule.get("id")
            if not isinstance(rule_id, str):
                errors.append(f"checks[{ci}].rules[{ri}].id must be a string.")
            rtype = rule.get("type")
            if not isinstance(rtype, str):
                errors.append(f"checks[{ci}].rules[{ri}].type must be a string.")
            elif rtype not in SUPPORTED_RULE_TYPES:
                errors.append(f"checks[{ci}].rules[{ri}].type '{rtype}' is not supported.")

            if "column" in rule and columns is not None:
                col = rule.get("column")
                if isinstance(col, str) and col not in columns:
                    errors.append(
                        f"checks[{ci}].rules[{ri}].column '{col}' not found in input columns."
                    )
            if rtype == "predicate" and columns is not None:
                _validate_predicate_columns(rule, columns, ci, ri, errors)

    return errors


def _validate_predicate_columns(
    rule: dict[str, Any],
    columns: set[str],
    ci: int,
    ri: int,
    errors: list[str],
) -> None:
    cols = rule.get("columns")
    expr = rule.get("expression")
    if isinstance(cols, (list, tuple)) and len(cols) == 2:
        for col in cols:
            if isinstance(col, str) and col not in columns:
                errors.append(
                    f"checks[{ci}].rules[{ri}].columns '{col}' not found in input columns."
                )
        return
    if isinstance(expr, str):
        parsed = _parse_expression_columns(expr)
        if not parsed:
            errors.append(
                f"checks[{ci}].rules[{ri}].expression is invalid or unsupported."
            )
            return
        left, right = parsed
        for col in (left, right):
            if col not in columns:
                errors.append(
                    f"checks[{ci}].rules[{ri}].expression column '{col}' not found in input columns."
                )


def _parse_expression_columns(expr: str) -> tuple[str, str] | None:
    for op in (">=", "<=", "==", "!=", ">", "<"):
        if op in expr:
            left, right = expr.split(op, 1)
            left = left.strip()
            right = right.strip()
            if left and right:
                return left, right
            return None
    return None


def extract_rule_ids(data: dict[str, Any]) -> list[str]:
    checks = data.get("checks", [])
    rule_ids: list[str] = []

    if not isinstance(checks, list):
        return rule_ids

    for check in checks:
        if not isinstance(check, dict):
            continue
        rules = check.get("rules", [])
        if not isinstance(rules, list):
            continue
        for rule in rules:
            if isinstance(rule, dict) and "id" in rule:
                rule_ids.append(str(rule["id"]))

    return rule_ids


def count_checks(data: dict[str, Any]) -> int:
    checks = data.get("checks", [])
    return len(checks) if isinstance(checks, list) else 0
