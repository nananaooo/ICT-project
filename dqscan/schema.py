"""Schema/profiler utilities."""

from __future__ import annotations

from typing import Any
import warnings

import pandas as pd

NUMERIC_SUCCESS_THRESHOLD = 0.9
DATETIME_SUCCESS_THRESHOLD = 0.9
CATEGORICAL_MAX_UNIQUE_RATIO = 0.05


def build_schema(df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    schema: dict[str, dict[str, Any]] = {}
    rows = int(df.shape[0])

    for col in df.columns:
        series = df[col]
        non_null = int(series.notna().sum())
        non_null_ratio = (non_null / rows) if rows > 0 else 0.0

        dtype_kind = "text"
        if non_null > 0:
            parsed_num = pd.to_numeric(series, errors="coerce")
            num_success = int(parsed_num.notna().sum()) / non_null
            if num_success >= NUMERIC_SUCCESS_THRESHOLD:
                dtype_kind = "numeric"
            else:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    parsed_dt = pd.to_datetime(series, errors="coerce")
                dt_success = int(parsed_dt.notna().sum()) / non_null
                if dt_success >= DATETIME_SUCCESS_THRESHOLD:
                    dtype_kind = "datetime"
                else:
                    unique_ratio = int(series.dropna().nunique()) / non_null
                    if unique_ratio <= CATEGORICAL_MAX_UNIQUE_RATIO:
                        dtype_kind = "categorical"

        schema[str(col)] = {
            "dtype_kind": dtype_kind,
            "non_null_ratio": non_null_ratio,
        }

    return schema
