"""Rules YAML loading and lightweight validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


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
