"""Run planned metrics and evaluate rules."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .metrics import compute_metrics, get_metric
from .planner import SUPPORTED_RULE_TYPES, build_metric_plan, iter_rules

SEVERITY_WEIGHTS = {
    "critical": 100,
    "high": 30,
    "medium": 10,
    "low": 3,
}

TOP_ISSUES_ROW_SAMPLE_LIMIT = 200


def _get_threshold(rule: dict[str, Any], primary: str, fallback: str) -> float | None:
    if primary in rule:
        return rule.get(primary)
    if fallback in rule:
        return rule.get(fallback)
    return None


def _severity(rule: dict[str, Any]) -> str:
    return str(rule.get("severity", "medium")).lower()


def _severity_weight(severity: str) -> int:
    return SEVERITY_WEIGHTS.get(severity, SEVERITY_WEIGHTS["medium"])


def _result_template(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": rule.get("id"),
        "type": rule.get("type"),
        "column": rule.get("column"),
        "threshold": {"min": None, "max": None},
        "actual": {"value": None},
        "pass": False,
        "violation_rate": None,
        "severity": _severity(rule),
        "score": None,
        "message": "",
    }


def _mask_to_rows(mask: pd.Series, limit: int) -> list[int]:
    if mask is None:
        return []
    idx = mask.to_numpy().nonzero()[0]
    if limit <= 0:
        return [int(i) + 1 for i in idx]
    return [int(i) + 1 for i in idx[:limit]]


def _parse_predicate_params(
    columns: Any,
    operator: Any,
    expression: Any,
) -> tuple[list[str] | None, str | None]:
    if columns and operator:
        if isinstance(columns, (list, tuple)) and len(columns) == 2:
            return [str(columns[0]), str(columns[1])], str(operator)

    if expression and isinstance(expression, str):
        for op in (">=", "<=", "==", "!=", ">", "<"):
            if op in expression:
                left, right = expression.split(op, 1)
                return [left.strip(), right.strip()], op

    return None, None


def _collect_violation_rows(
    df: pd.DataFrame,
    rule: dict[str, Any],
    limit: int,
) -> list[int]:
    rule_type = str(rule.get("type", "")).strip()
    column = rule.get("column")

    if rule_type in {"completeness", "missing_rate"}:
        if not column or column not in df.columns:
            return []
        return _mask_to_rows(df[column].isna(), limit)

    if rule_type == "type":
        if not column or column not in df.columns:
            return []
        expected = str(rule.get("expected", "numeric")).lower()
        series = df[column]
        non_null = series.notna()
        if expected == "datetime":
            formats = rule.get("datetime_formats") or rule.get("formats")
            if formats:
                success_mask = pd.Series(False, index=series.index)
                for fmt in formats:
                    parsed = pd.to_datetime(series, format=fmt, errors="coerce")
                    success_mask |= parsed.notna()
            else:
                parsed = pd.to_datetime(series, errors="coerce")
                success_mask = parsed.notna()
        else:
            parsed = pd.to_numeric(series, errors="coerce")
            success_mask = parsed.notna()
        invalid = non_null & ~success_mask
        return _mask_to_rows(invalid, limit)

    if rule_type == "range":
        if not column or column not in df.columns:
            return []
        min_value, max_value, _ = _range_bounds(rule)
        if min_value is None or max_value is None:
            return []
        series = pd.to_numeric(df[column], errors="coerce")
        valid = series.notna()
        invalid = valid & ((series < float(min_value)) | (series > float(max_value)))
        return _mask_to_rows(invalid, limit)

    if rule_type == "uniqueness":
        if not column or column not in df.columns:
            return []
        series = df[column]
        non_null = series.notna()
        dup_mask = series.duplicated(keep=False) & non_null
        return _mask_to_rows(dup_mask, limit)

    if rule_type == "domain":
        if not column or column not in df.columns:
            return []
        allowed = rule.get("allowed_values") or rule.get("allowed")
        if allowed is None:
            return []
        allowed_set = set(allowed) if isinstance(allowed, (list, tuple, set)) else {allowed}
        series = df[column]
        non_null = series.notna()
        invalid = non_null & ~series.isin(allowed_set)
        return _mask_to_rows(invalid, limit)

    if rule_type == "pattern":
        if not column or column not in df.columns:
            return []
        regex = rule.get("regex") or rule.get("pattern")
        if not regex:
            return []
        series = df[column]
        non_null = series.notna()
        match = series.astype(str).str.match(regex)
        invalid = non_null & ~match
        return _mask_to_rows(invalid, limit)

    if rule_type == "outlier":
        if not column or column not in df.columns:
            return []
        method = str(rule.get("method", "iqr")).lower()
        series = pd.to_numeric(df[column], errors="coerce")
        valid = series.notna()
        if valid.sum() == 0:
            return []
        if method == "mad":
            mad_threshold = float(rule.get("mad_threshold", 3.5))
            valid_series = series[valid]
            median = valid_series.median()
            mad = (valid_series - median).abs().median()
            if mad == 0:
                return []
            robust_z = 0.6745 * (valid_series - median) / mad
            outlier_mask = robust_z.abs() > mad_threshold
            mask = pd.Series(False, index=series.index)
            mask.loc[valid_series.index] = outlier_mask
            return _mask_to_rows(mask, limit)

        iqr_k = float(rule.get("iqr_k", 1.5))
        valid_series = series[valid]
        q1 = valid_series.quantile(0.25)
        q3 = valid_series.quantile(0.75)
        iqr = q3 - q1
        lower = q1 - iqr_k * iqr
        upper = q3 + iqr_k * iqr
        outlier_mask = valid & ((series < lower) | (series > upper))
        return _mask_to_rows(outlier_mask, limit)

    if rule_type == "predicate":
        columns = rule.get("columns")
        operator = rule.get("operator")
        expression = rule.get("expression")
        parsed_cols, parsed_op = _parse_predicate_params(columns, operator, expression)
        if not parsed_cols or parsed_op is None:
            return []
        left_col, right_col = parsed_cols
        if left_col not in df.columns or right_col not in df.columns:
            return []
        left = df[left_col]
        right = df[right_col]
        valid = left.notna() & right.notna()
        if parsed_op == ">=":
            ok = left >= right
        elif parsed_op == "<=":
            ok = left <= right
        elif parsed_op == ">":
            ok = left > right
        elif parsed_op == "<":
            ok = left < right
        elif parsed_op == "==":
            ok = left == right
        elif parsed_op == "!=":
            ok = left != right
        else:
            return []
        invalid = valid & ~ok
        return _mask_to_rows(invalid, limit)

    return []


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


def _evaluate_completeness(
    df: pd.DataFrame,
    rule: dict[str, Any],
    cache: dict[Any, Any],
    total_count: int,
) -> dict[str, Any]:
    result = _result_template(rule)
    column = rule.get("column")
    min_threshold = _get_threshold(rule, "min", "min_threshold")
    result["threshold"]["min"] = min_threshold

    if total_count == 0:
        result["message"] = "empty dataset"
        return result
    if not column or column not in df.columns:
        result["message"] = "missing column"
        return result
    if min_threshold is None:
        result["message"] = "missing threshold"
        return result

    non_null_count = get_metric(cache, "non_null_count", column=column)
    if non_null_count is None:
        result["message"] = "metric unavailable"
        return result

    completeness = non_null_count / total_count
    violation_rate = 1.0 - completeness
    passed = completeness >= float(min_threshold)

    result["actual"]["value"] = completeness
    result["pass"] = passed
    result["violation_rate"] = violation_rate
    weight = _severity_weight(result["severity"])
    result["score"] = weight * violation_rate
    result["message"] = "" if passed else "below minimum completeness"
    return result


def _evaluate_missing_rate(
    df: pd.DataFrame,
    rule: dict[str, Any],
    cache: dict[Any, Any],
    total_count: int,
) -> dict[str, Any]:
    result = _result_template(rule)
    column = rule.get("column")
    max_threshold = _get_threshold(rule, "max", "max_threshold")
    result["threshold"]["max"] = max_threshold

    if total_count == 0:
        result["message"] = "empty dataset"
        return result
    if not column or column not in df.columns:
        result["message"] = "missing column"
        return result
    if max_threshold is None:
        result["message"] = "missing threshold"
        return result

    null_count = get_metric(cache, "null_count", column=column)
    if null_count is None:
        result["message"] = "metric unavailable"
        return result

    missing_rate = null_count / total_count
    violation_rate = missing_rate
    passed = missing_rate <= float(max_threshold)

    result["actual"]["value"] = missing_rate
    result["pass"] = passed
    result["violation_rate"] = violation_rate
    weight = _severity_weight(result["severity"])
    result["score"] = weight * violation_rate
    result["message"] = "" if passed else "above maximum missing rate"
    return result


def _evaluate_type(
    df: pd.DataFrame,
    rule: dict[str, Any],
    cache: dict[Any, Any],
) -> dict[str, Any]:
    result = _result_template(rule)
    column = rule.get("column")
    expected = str(rule.get("expected", "numeric")).lower()
    min_threshold = _get_threshold(rule, "min_parse_success", "min_threshold")
    if min_threshold is None and "min" in rule:
        min_threshold = rule.get("min")
    result["threshold"]["min"] = min_threshold

    if not column or column not in df.columns:
        result["message"] = "missing column"
        return result
    if min_threshold is None:
        result["message"] = "missing threshold"
        return result

    non_null_count = get_metric(cache, "non_null_count", column=column)
    if non_null_count is None:
        result["message"] = "metric unavailable"
        return result
    if non_null_count == 0:
        result["message"] = "no non-null values"
        return result

    if expected == "datetime":
        formats = rule.get("datetime_formats") or rule.get("formats")
        params = {"formats": formats} if formats else None
        parse_success = get_metric(cache, "parse_success_datetime", column=column, params=params)
    else:
        parse_success = get_metric(cache, "parse_success_numeric", column=column)

    if parse_success is None:
        result["message"] = "metric unavailable"
        return result

    violation_rate = 1.0 - parse_success
    passed = parse_success >= float(min_threshold)

    result["actual"]["value"] = parse_success
    result["pass"] = passed
    result["violation_rate"] = violation_rate
    weight = _severity_weight(result["severity"])
    result["score"] = weight * violation_rate
    result["message"] = "" if passed else "below minimum parse success"
    return result


def _evaluate_range(
    df: pd.DataFrame,
    rule: dict[str, Any],
    cache: dict[Any, Any],
) -> dict[str, Any]:
    result = _result_template(rule)
    column = rule.get("column")
    min_value, max_value, min_from_min_key = _range_bounds(rule)
    min_compliance = rule.get("min_compliance")
    if min_compliance is None:
        min_compliance = rule.get("min_threshold")
    if min_compliance is None and not min_from_min_key and "min" in rule:
        min_compliance = rule.get("min")

    result["threshold"]["min"] = min_compliance
    result["threshold"]["min_value"] = min_value
    result["threshold"]["max_value"] = max_value

    if not column or column not in df.columns:
        result["message"] = "missing column"
        return result
    if min_value is None or max_value is None:
        result["message"] = "missing range bounds"
        return result
    if min_compliance is None:
        result["message"] = "missing threshold"
        return result

    compliance = get_metric(
        cache,
        "range_compliance",
        column=column,
        params={"min_value": min_value, "max_value": max_value},
    )
    if compliance is None:
        result["message"] = "no valid numeric values"
        return result

    violation_rate = 1.0 - compliance
    passed = compliance >= float(min_compliance)

    result["actual"]["value"] = compliance
    result["pass"] = passed
    result["violation_rate"] = violation_rate
    weight = _severity_weight(result["severity"])
    result["score"] = weight * violation_rate
    result["message"] = "" if passed else "below minimum compliance"
    return result


def _evaluate_uniqueness(
    df: pd.DataFrame,
    rule: dict[str, Any],
    cache: dict[Any, Any],
) -> dict[str, Any]:
    result = _result_template(rule)
    column = rule.get("column")
    min_threshold = _get_threshold(rule, "min", "min_threshold")
    result["threshold"]["min"] = min_threshold

    if not column or column not in df.columns:
        result["message"] = "missing column"
        return result
    if min_threshold is None:
        result["message"] = "missing threshold"
        return result

    ratio = get_metric(cache, "uniqueness_ratio", column=column)
    if ratio is None:
        result["message"] = "no non-null values"
        return result

    violation_rate = 1.0 - ratio
    passed = ratio >= float(min_threshold)

    result["actual"]["value"] = ratio
    result["pass"] = passed
    result["violation_rate"] = violation_rate
    weight = _severity_weight(result["severity"])
    result["score"] = weight * violation_rate
    result["message"] = "" if passed else "below minimum uniqueness"
    return result


def _evaluate_domain(
    df: pd.DataFrame,
    rule: dict[str, Any],
    cache: dict[Any, Any],
) -> dict[str, Any]:
    result = _result_template(rule)
    column = rule.get("column")
    allowed_values = rule.get("allowed_values") or rule.get("allowed")
    min_compliance = rule.get("min_compliance")
    if min_compliance is None:
        min_compliance = rule.get("min")

    result["threshold"]["min"] = min_compliance
    result["threshold"]["allowed_values"] = allowed_values

    if not column or column not in df.columns:
        result["message"] = "missing column"
        return result
    if allowed_values is None:
        result["message"] = "missing allowed_values"
        return result
    if min_compliance is None:
        result["message"] = "missing threshold"
        return result

    compliance = get_metric(
        cache,
        "domain_compliance",
        column=column,
        params={"allowed_values": allowed_values},
    )
    if compliance is None:
        result["message"] = "no non-null values"
        return result

    violation_rate = 1.0 - compliance
    passed = compliance >= float(min_compliance)

    result["actual"]["value"] = compliance
    result["pass"] = passed
    result["violation_rate"] = violation_rate
    weight = _severity_weight(result["severity"])
    result["score"] = weight * violation_rate
    result["message"] = "" if passed else "below minimum compliance"
    return result


def _evaluate_pattern(
    df: pd.DataFrame,
    rule: dict[str, Any],
    cache: dict[Any, Any],
) -> dict[str, Any]:
    result = _result_template(rule)
    column = rule.get("column")
    regex = rule.get("regex") or rule.get("pattern")
    min_compliance = rule.get("min_compliance")
    if min_compliance is None:
        min_compliance = rule.get("min")

    result["threshold"]["min"] = min_compliance
    result["threshold"]["regex"] = regex

    if not column or column not in df.columns:
        result["message"] = "missing column"
        return result
    if regex is None:
        result["message"] = "missing regex"
        return result
    if min_compliance is None:
        result["message"] = "missing threshold"
        return result

    rate = get_metric(
        cache,
        "pattern_match_rate",
        column=column,
        params={"regex": regex},
    )
    if rate is None:
        result["message"] = "no non-null values"
        return result

    violation_rate = 1.0 - rate
    passed = rate >= float(min_compliance)

    result["actual"]["value"] = rate
    result["pass"] = passed
    result["violation_rate"] = violation_rate
    weight = _severity_weight(result["severity"])
    result["score"] = weight * violation_rate
    result["message"] = "" if passed else "below minimum compliance"
    return result


def _evaluate_outlier(
    df: pd.DataFrame,
    rule: dict[str, Any],
    cache: dict[Any, Any],
) -> dict[str, Any]:
    result = _result_template(rule)
    column = rule.get("column")
    method = str(rule.get("method", "iqr")).lower()
    max_rate = rule.get("max_rate")
    if max_rate is None:
        max_rate = rule.get("max")
    sample_n = int(rule.get("sample_n", 5))

    result["threshold"]["max"] = max_rate
    result["threshold"]["method"] = method
    result["threshold"]["sample_n"] = sample_n

    if not column or column not in df.columns:
        result["message"] = "missing column"
        return result
    if max_rate is None:
        result["message"] = "missing threshold"
        return result

    if method == "mad":
        mad_threshold = rule.get("mad_threshold", 3.5)
        result["threshold"]["mad_threshold"] = mad_threshold
        metric = get_metric(
            cache,
            "outlier_rate_mad",
            column=column,
            params={"mad_threshold": mad_threshold, "sample_n": sample_n},
        )
    else:
        iqr_k = rule.get("iqr_k", 1.5)
        result["threshold"]["iqr_k"] = iqr_k
        metric = get_metric(
            cache,
            "outlier_rate_iqr",
            column=column,
            params={"iqr_k": iqr_k, "sample_n": sample_n},
        )

    if metric is None:
        result["message"] = "metric unavailable"
        return result

    rate = metric.get("rate")
    samples = metric.get("samples", [])
    error = metric.get("error")
    if error:
        result["message"] = error
        result["samples"] = samples
        return result

    violation_rate = rate
    passed = rate <= float(max_rate)

    result["actual"]["value"] = rate
    result["pass"] = passed
    result["violation_rate"] = violation_rate
    weight = _severity_weight(result["severity"])
    result["score"] = weight * violation_rate
    result["samples"] = samples
    result["message"] = "" if passed else "above maximum outlier rate"
    return result


def _evaluate_predicate(
    df: pd.DataFrame,
    rule: dict[str, Any],
    cache: dict[Any, Any],
) -> dict[str, Any]:
    result = _result_template(rule)
    columns = rule.get("columns")
    operator = rule.get("operator")
    expression = rule.get("expression")
    min_compliance = rule.get("min_compliance")
    if min_compliance is None:
        min_compliance = rule.get("min")

    result["threshold"]["min"] = min_compliance
    result["threshold"]["columns"] = columns
    result["threshold"]["operator"] = operator
    result["threshold"]["expression"] = expression

    if not columns and not expression:
        result["message"] = "missing columns"
        return result
    if columns:
        if not isinstance(columns, (list, tuple)) or len(columns) != 2:
            result["message"] = "invalid columns"
            return result
        if columns[0] not in df.columns or columns[1] not in df.columns:
            result["message"] = "missing column"
            return result
    elif expression:
        parsed = None
        for op in (">=", "<=", "==", "!=", ">", "<"):
            if op in expression:
                left, right = expression.split(op, 1)
                parsed = (left.strip(), right.strip())
                break
        if not parsed:
            result["message"] = "invalid expression"
            return result
        if parsed[0] not in df.columns or parsed[1] not in df.columns:
            result["message"] = "missing column"
            return result
    if min_compliance is None:
        result["message"] = "missing threshold"
        return result

    compliance = get_metric(
        cache,
        "predicate_compliance",
        params={"columns": columns, "operator": operator, "expression": expression},
    )
    if compliance is None:
        result["message"] = "no valid rows"
        return result

    violation_rate = 1.0 - compliance
    passed = compliance >= float(min_compliance)

    result["actual"]["value"] = compliance
    result["pass"] = passed
    result["violation_rate"] = violation_rate
    weight = _severity_weight(result["severity"])
    result["score"] = weight * violation_rate
    result["message"] = "" if passed else "below minimum compliance"
    return result


def run_scan(
    df: pd.DataFrame,
    rules_data: dict[str, Any],
    input_path: Path,
    rules_path: Path,
    top_issues: int | None = None,
) -> dict[str, Any]:
    plan = build_metric_plan(rules_data)

    available_requests = [
        req
        for req in plan
        if req.column is None or req.column in df.columns
    ]
    cache = compute_metrics(df, available_requests)

    total_count = get_metric(cache, "total_count")
    if total_count is None:
        total_count = int(df.shape[0])

    results: list[dict[str, Any]] = []
    rule_by_id: dict[str, dict[str, Any]] = {}
    for rule in iter_rules(rules_data):
        rule_type = str(rule.get("type", "")).strip()
        if rule_type not in SUPPORTED_RULE_TYPES:
            continue
        rule_id = rule.get("id")
        if isinstance(rule_id, str):
            rule_by_id[rule_id] = rule
        if rule_type == "completeness":
            result = _evaluate_completeness(df, rule, cache, total_count)
        elif rule_type == "missing_rate":
            result = _evaluate_missing_rate(df, rule, cache, total_count)
        elif rule_type == "type":
            result = _evaluate_type(df, rule, cache)
        elif rule_type == "range":
            result = _evaluate_range(df, rule, cache)
        elif rule_type == "uniqueness":
            result = _evaluate_uniqueness(df, rule, cache)
        elif rule_type == "domain":
            result = _evaluate_domain(df, rule, cache)
        elif rule_type == "pattern":
            result = _evaluate_pattern(df, rule, cache)
        elif rule_type == "outlier":
            result = _evaluate_outlier(df, rule, cache)
        elif rule_type == "predicate":
            result = _evaluate_predicate(df, rule, cache)
        else:
            continue

        if not result.get("pass", False):
            result["violation_rows"] = _collect_violation_rows(df, rule, 0)
        results.append(result)

    fail_results = [r for r in results if not r.get("pass", False)]
    fail_results_sorted = sorted(
        fail_results,
        key=lambda r: (r.get("score") is None, -(r.get("score") or 0.0)),
    )
    if top_issues is None or int(top_issues) <= 0:
        slice_count = len(fail_results_sorted)
    else:
        slice_count = int(top_issues)
    top_issues_list = [
        {
            "id": r.get("id"),
            "type": r.get("type"),
            "column": r.get("column"),
            "severity": r.get("severity"),
            "violation_rate": r.get("violation_rate"),
            "score": r.get("score"),
            "message": r.get("message"),
            "violation_rows": _collect_violation_rows(
                df,
                rule_by_id.get(str(r.get("id")), {}),
                TOP_ISSUES_ROW_SAMPLE_LIMIT,
            ),
        }
        for r in fail_results_sorted[:slice_count]
    ]

    report = {
        "meta": {
            "input_path": str(input_path),
            "rules_path": str(rules_path),
            "rows": int(df.shape[0]),
            "cols": int(df.shape[1]),
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        "summary": {
            "pass": len([r for r in results if r.get("pass")]),
            "fail": len([r for r in results if not r.get("pass")]),
            "top_issues": top_issues_list,
        },
        "results": results,
    }

    return report
