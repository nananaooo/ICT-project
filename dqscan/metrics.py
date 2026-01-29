"""Metric requests and registry-backed metric execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

import pandas as pd


MetricFn = Callable[[pd.DataFrame, str | None, tuple[tuple[str, Any], ...]], Any]


@dataclass(frozen=True)
class MetricRequest:
    name: str
    column: str | None = None
    params: tuple[tuple[str, Any], ...] = ()

    @staticmethod
    def create(
        name: str,
        column: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> "MetricRequest":
        items = tuple(sorted((_normalize_param(k), _normalize_param(v)) for k, v in params.items())) if params else ()
        return MetricRequest(name=name, column=column, params=items)


def _total_count(df: pd.DataFrame, _column: str | None, _params: tuple[tuple[str, Any], ...]) -> int:
    return int(df.shape[0])


def _null_count(df: pd.DataFrame, column: str | None, _params: tuple[tuple[str, Any], ...]) -> int:
    if column is None:
        raise ValueError("null_count requires a column")
    return int(df[column].isna().sum())


def _non_null_count(df: pd.DataFrame, column: str | None, _params: tuple[tuple[str, Any], ...]) -> int:
    if column is None:
        raise ValueError("non_null_count requires a column")
    return int(df[column].notna().sum())


def _parse_success_numeric(
    df: pd.DataFrame,
    column: str | None,
    _params: tuple[tuple[str, Any], ...],
) -> float | None:
    if column is None:
        raise ValueError("parse_success_numeric requires a column")
    series = df[column]
    non_null = series.notna()
    denom = int(non_null.sum())
    if denom == 0:
        return None
    parsed = pd.to_numeric(series, errors="coerce")
    success = int(parsed.notna().sum())
    return success / denom


def _parse_success_datetime(
    df: pd.DataFrame,
    column: str | None,
    params: tuple[tuple[str, Any], ...],
) -> float | None:
    if column is None:
        raise ValueError("parse_success_datetime requires a column")
    series = df[column]
    non_null = series.notna()
    denom = int(non_null.sum())
    if denom == 0:
        return None

    params_dict = dict(params)
    formats = params_dict.get("formats")
    if formats:
        success_mask = pd.Series(False, index=series.index)
        for fmt in formats:
            parsed = pd.to_datetime(series, format=fmt, errors="coerce")
            success_mask |= parsed.notna()
        success = int((success_mask & non_null).sum())
        return success / denom

    parsed = pd.to_datetime(series, errors="coerce")
    success = int(parsed.notna().sum())
    return success / denom


def _range_compliance(
    df: pd.DataFrame,
    column: str | None,
    params: tuple[tuple[str, Any], ...],
) -> float | None:
    if column is None:
        raise ValueError("range_compliance requires a column")
    params_dict = dict(params)
    min_value = params_dict.get("min_value")
    max_value = params_dict.get("max_value")
    if min_value is None or max_value is None:
        raise ValueError("range_compliance requires min_value and max_value")

    series = pd.to_numeric(df[column], errors="coerce")
    valid_mask = series.notna()
    denom = int(valid_mask.sum())
    if denom == 0:
        return None
    ok_mask = (series >= float(min_value)) & (series <= float(max_value)) & valid_mask
    ok_count = int(ok_mask.sum())
    return ok_count / denom


def _uniqueness_ratio(
    df: pd.DataFrame,
    column: str | None,
    _params: tuple[tuple[str, Any], ...],
) -> float | None:
    if column is None:
        raise ValueError("uniqueness_ratio requires a column")
    series = df[column].dropna()
    denom = int(series.shape[0])
    if denom == 0:
        return None
    distinct = int(series.nunique())
    return distinct / denom


def _domain_compliance(
    df: pd.DataFrame,
    column: str | None,
    params: tuple[tuple[str, Any], ...],
) -> float | None:
    if column is None:
        raise ValueError("domain_compliance requires a column")
    params_dict = dict(params)
    allowed_values = params_dict.get("allowed_values")
    if allowed_values is None:
        raise ValueError("domain_compliance requires allowed_values")
    series = df[column].dropna()
    denom = int(series.shape[0])
    if denom == 0:
        return None
    allowed_set = set(allowed_values)
    ok_count = int(series.isin(allowed_set).sum())
    return ok_count / denom


def _pattern_match_rate(
    df: pd.DataFrame,
    column: str | None,
    params: tuple[tuple[str, Any], ...],
) -> float | None:
    if column is None:
        raise ValueError("pattern_match_rate requires a column")
    params_dict = dict(params)
    regex = params_dict.get("regex")
    if regex is None:
        raise ValueError("pattern_match_rate requires regex")
    series = df[column].dropna()
    denom = int(series.shape[0])
    if denom == 0:
        return None
    match_count = int(series.astype(str).str.match(regex).sum())
    return match_count / denom


def _outlier_rate_iqr(
    df: pd.DataFrame,
    column: str | None,
    params: tuple[tuple[str, Any], ...],
) -> dict[str, Any]:
    if column is None:
        raise ValueError("outlier_rate_iqr requires a column")
    params_dict = dict(params)
    iqr_k = float(params_dict.get("iqr_k", 1.5))
    sample_n = int(params_dict.get("sample_n", 5))

    series = pd.to_numeric(df[column], errors="coerce")
    valid_mask = series.notna()
    denom = int(valid_mask.sum())
    if denom == 0:
        return {"rate": None, "samples": [], "error": "no valid numeric values"}

    q1 = series[valid_mask].quantile(0.25)
    q3 = series[valid_mask].quantile(0.75)
    iqr = q3 - q1
    lower = q1 - iqr_k * iqr
    upper = q3 + iqr_k * iqr
    outlier_mask = valid_mask & ((series < lower) | (series > upper))
    outlier_series = series[outlier_mask]
    outlier_count = int(outlier_series.shape[0])
    rate = outlier_count / denom

    samples = [
        {"row": int(idx), "value": float(val)}
        for idx, val in outlier_series.head(sample_n).items()
    ]
    return {"rate": rate, "samples": samples, "error": None}


def _outlier_rate_mad(
    df: pd.DataFrame,
    column: str | None,
    params: tuple[tuple[str, Any], ...],
) -> dict[str, Any]:
    if column is None:
        raise ValueError("outlier_rate_mad requires a column")
    params_dict = dict(params)
    mad_threshold = float(params_dict.get("mad_threshold", 3.5))
    sample_n = int(params_dict.get("sample_n", 5))

    series = pd.to_numeric(df[column], errors="coerce")
    valid = series.dropna()
    denom = int(valid.shape[0])
    if denom == 0:
        return {"rate": None, "samples": [], "error": "no valid numeric values"}

    median = valid.median()
    mad = (valid - median).abs().median()
    if mad == 0:
        return {"rate": None, "samples": [], "error": "mad is zero"}

    robust_z = 0.6745 * (valid - median) / mad
    outlier_mask = robust_z.abs() > mad_threshold
    outlier_series = valid[outlier_mask]
    outlier_count = int(outlier_series.shape[0])
    rate = outlier_count / denom

    samples = [
        {"row": int(idx), "value": float(val)}
        for idx, val in outlier_series.head(sample_n).items()
    ]
    return {"rate": rate, "samples": samples, "error": None}


def _parse_predicate_params(params: tuple[tuple[str, Any], ...]) -> tuple[list[str] | None, str | None]:
    params_dict = dict(params)
    columns = params_dict.get("columns")
    operator = params_dict.get("operator")
    expression = params_dict.get("expression")

    if columns and operator:
        if isinstance(columns, (list, tuple)) and len(columns) == 2:
            return [str(columns[0]), str(columns[1])], str(operator)

    if expression and isinstance(expression, str):
        for op in (">=", "<=", "==", "!=", ">", "<"):
            if op in expression:
                left, right = expression.split(op, 1)
                return [left.strip(), right.strip()], op

    return None, None


def _predicate_compliance(
    df: pd.DataFrame,
    _column: str | None,
    params: tuple[tuple[str, Any], ...],
) -> float | None:
    columns, operator = _parse_predicate_params(params)
    if not columns or operator is None:
        raise ValueError("predicate_compliance requires columns and operator")
    if columns[0] not in df.columns or columns[1] not in df.columns:
        return None

    left = df[columns[0]]
    right = df[columns[1]]
    valid = left.notna() & right.notna()
    denom = int(valid.sum())
    if denom == 0:
        return None

    if operator == ">=":
        ok = left >= right
    elif operator == "<=":
        ok = left <= right
    elif operator == ">":
        ok = left > right
    elif operator == "<":
        ok = left < right
    elif operator == "==":
        ok = left == right
    elif operator == "!=":
        ok = left != right
    else:
        raise ValueError("unsupported operator")

    ok_count = int((ok & valid).sum())
    return ok_count / denom

METRIC_REGISTRY: dict[str, MetricFn] = {
    "total_count": _total_count,
    "null_count": _null_count,
    "non_null_count": _non_null_count,
    "parse_success_numeric": _parse_success_numeric,
    "parse_success_datetime": _parse_success_datetime,
    "range_compliance": _range_compliance,
    "uniqueness_ratio": _uniqueness_ratio,
    "domain_compliance": _domain_compliance,
    "pattern_match_rate": _pattern_match_rate,
    "outlier_rate_iqr": _outlier_rate_iqr,
    "outlier_rate_mad": _outlier_rate_mad,
    "predicate_compliance": _predicate_compliance,
}


def compute_metrics(
    df: pd.DataFrame,
    requests: Iterable[MetricRequest],
) -> dict[MetricRequest, Any]:
    cache: dict[MetricRequest, Any] = {}

    for request in requests:
        if request in cache:
            continue
        fn = METRIC_REGISTRY.get(request.name)
        if fn is None:
            raise ValueError(f"Unknown metric: {request.name}")
        cache[request] = fn(df, request.column, request.params)

    return cache


def get_metric(
    cache: dict[MetricRequest, Any],
    name: str,
    column: str | None = None,
    params: dict[str, Any] | None = None,
) -> Any:
    key = MetricRequest.create(name=name, column=column, params=params)
    return cache.get(key)


def _normalize_param(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((k, _normalize_param(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_normalize_param(v) for v in value)
    return value
