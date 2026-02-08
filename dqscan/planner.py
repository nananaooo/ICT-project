"""Planning metrics required for rules."""

from __future__ import annotations

from typing import Any, Iterable

from .metrics import MetricRequest

SUPPORTED_RULE_TYPES = {
    "completeness",
    "missing_rate",
    "type",
    "range",
    "uniqueness",
    "domain",
    "pattern",
    "outlier",
    "predicate",
}


def _range_bounds(rule: dict[str, Any]) -> tuple[Any | None, Any | None, bool]:
    min_value = rule.get("min_value")
    max_value = rule.get("max_value")
    min_from_min_key = False

    if min_value is None and ("max_value" in rule or "max" in rule) and "min" in rule:
        min_value = rule.get("min")
        min_from_min_key = True
    if max_value is None and "max" in rule:
        max_value = rule.get("max")

    return min_value, max_value, min_from_min_key


def iter_rules(rules_data: dict[str, Any]) -> Iterable[dict[str, Any]]:
    checks = rules_data.get("checks", [])
    if not isinstance(checks, list):
        return []
    for check in checks:
        if not isinstance(check, dict):
            continue
        rules = check.get("rules", [])
        if not isinstance(rules, list):
            continue
        for rule in rules:
            if isinstance(rule, dict):
                yield rule


def build_metric_plan(rules_data: dict[str, Any]) -> list[MetricRequest]:
    requests: set[MetricRequest] = set()

    for rule in iter_rules(rules_data):
        rule_type = str(rule.get("type", "")).strip()
        if rule_type not in SUPPORTED_RULE_TYPES:
            continue
        column = rule.get("column")
        requests.add(MetricRequest.create("total_count"))
        if rule_type == "completeness":
            if column is not None:
                requests.add(MetricRequest.create("non_null_count", column=column))
        elif rule_type == "missing_rate":
            if column is not None:
                requests.add(MetricRequest.create("null_count", column=column))
        elif rule_type == "type":
            if column is None:
                continue
            requests.add(MetricRequest.create("non_null_count", column=column))
            expected = str(rule.get("expected", "numeric")).lower()
            if expected == "datetime":
                formats = rule.get("datetime_formats") or rule.get("formats")
                params = {"formats": formats} if formats else None
                requests.add(
                    MetricRequest.create(
                        "parse_success_datetime",
                        column=column,
                        params=params,
                    )
                )
            else:
                requests.add(MetricRequest.create("parse_success_numeric", column=column))
        elif rule_type == "range":
            if column is None:
                continue
            min_value, max_value, _ = _range_bounds(rule)
            if min_value is None or max_value is None:
                continue
            requests.add(
                MetricRequest.create(
                    "range_compliance",
                    column=column,
                    params={"min_value": min_value, "max_value": max_value},
                )
            )
        elif rule_type == "uniqueness":
            if column is None:
                continue
            requests.add(MetricRequest.create("uniqueness_ratio", column=column))
        elif rule_type == "domain":
            if column is None:
                continue
            allowed_values = rule.get("allowed_values") or rule.get("allowed")
            if allowed_values is None:
                continue
            requests.add(
                MetricRequest.create(
                    "domain_compliance",
                    column=column,
                    params={"allowed_values": allowed_values},
                )
            )
        elif rule_type == "pattern":
            if column is None:
                continue
            regex = rule.get("regex") or rule.get("pattern")
            if regex is None:
                continue
            requests.add(
                MetricRequest.create(
                    "pattern_match_rate",
                    column=column,
                    params={"regex": regex},
                )
            )
        elif rule_type == "outlier":
            if column is None:
                continue
            method = str(rule.get("method", "iqr")).lower()
            sample_n = rule.get("sample_n", 5)
            if method == "mad":
                mad_threshold = rule.get("mad_threshold", 3.5)
                requests.add(
                    MetricRequest.create(
                        "outlier_rate_mad",
                        column=column,
                        params={"mad_threshold": mad_threshold, "sample_n": sample_n},
                    )
                )
            else:
                iqr_k = rule.get("iqr_k", 1.5)
                requests.add(
                    MetricRequest.create(
                        "outlier_rate_iqr",
                        column=column,
                        params={"iqr_k": iqr_k, "sample_n": sample_n},
                    )
                )
        elif rule_type == "predicate":
            columns = rule.get("columns")
            operator = rule.get("operator")
            expression = rule.get("expression")
            if not columns and not expression:
                continue
            requests.add(
                MetricRequest.create(
                    "predicate_compliance",
                    column=None,
                    params={
                        "columns": columns,
                        "operator": operator,
                        "expression": expression,
                    },
                )
            )

    return list(requests)
