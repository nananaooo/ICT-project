"""Rule expansion based on schema selectors."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any


def expand_rules(
    rules_data: dict[str, Any],
    schema: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    checks = rules_data.get("checks", [])
    if not isinstance(checks, list):
        return rules_data, warnings

    expanded_checks: list[dict[str, Any]] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        rules = check.get("rules", [])
        if not isinstance(rules, list):
            expanded_checks.append(check)
            continue

        expanded_rules: list[dict[str, Any]] = []
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            if rule.get("column") is not None:
                expanded_rules.append(rule)
                continue

            selector = rule.get("selector")
            if not selector:
                expanded_rules.append(rule)
                continue

            selected = _select_columns(selector, schema)
            if not selected:
                rule_id = rule.get("id", "<no id>")
                warnings.append(f"selector matched 0 columns for rule {rule_id}")
                continue

            for col in selected:
                clone = deepcopy(rule)
                clone.pop("selector", None)
                clone["column"] = col
                if clone.get("id"):
                    clone["id"] = f"{clone['id']}__col={col}"
                else:
                    clone["id"] = f"rule__col={col}"
                expanded_rules.append(clone)

        expanded_check = dict(check)
        expanded_check["rules"] = expanded_rules
        expanded_checks.append(expanded_check)

    expanded = dict(rules_data)
    expanded["checks"] = expanded_checks
    return expanded, warnings


def _select_columns(selector: dict[str, Any], schema: dict[str, dict[str, Any]]) -> list[str]:
    dtype_filter = selector.get("dtype")
    include = selector.get("include") or []
    exclude = selector.get("exclude") or []
    name_regex = selector.get("name_regex")

    if isinstance(dtype_filter, str):
        dtype_set = {dtype_filter}
    elif isinstance(dtype_filter, (list, tuple)):
        dtype_set = {str(d) for d in dtype_filter}
    else:
        dtype_set = set()

    columns = list(schema.keys())
    if dtype_set:
        columns = [c for c in columns if schema.get(c, {}).get("dtype_kind") in dtype_set]

    columns = set(columns)
    columns.update(include if isinstance(include, (list, tuple)) else [])
    if exclude:
        columns.difference_update(exclude if isinstance(exclude, (list, tuple)) else [])

    if name_regex:
        pattern = re.compile(str(name_regex))
        columns = {c for c in columns if pattern.search(c)}

    return sorted(columns)
