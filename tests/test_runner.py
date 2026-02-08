import pandas as pd
import pytest

from dqscan.runner import run_scan


def _rules_for_completeness_and_missing_rate() -> dict:
    return {
        "checks": [
            {
                "name": "basic",
                "rules": [
                    {
                        "id": "R1",
                        "type": "completeness",
                        "column": "a",
                        "min": 0.7,
                        "severity": "high",
                    },
                    {
                        "id": "R2",
                        "type": "missing_rate",
                        "column": "a",
                        "max": 0.4,
                        "severity": "low",
                    },
                ],
            }
        ]
    }


def test_completeness_and_missing_rate_values(tmp_path):
    df = pd.DataFrame({"a": [1, None, 3]})
    report = run_scan(
        df=df,
        rules_data=_rules_for_completeness_and_missing_rate(),
        input_path=tmp_path / "in.csv",
        rules_path=tmp_path / "rules.yaml",
        top_issues=10,
    )

    results = {r["id"]: r for r in report["results"]}
    completeness = results["R1"]
    missing_rate = results["R2"]

    assert completeness["actual"]["value"] == pytest.approx(2 / 3)
    assert missing_rate["actual"]["value"] == pytest.approx(1 / 3)


def test_missing_column_fails(tmp_path):
    df = pd.DataFrame({"a": [1, 2, 3]})
    rules_data = {
        "checks": [
            {
                "name": "basic",
                "rules": [
                    {
                        "id": "R_missing",
                        "type": "completeness",
                        "column": "missing_col",
                        "min": 1.0,
                    }
                ],
            }
        ]
    }

    report = run_scan(
        df=df,
        rules_data=rules_data,
        input_path=tmp_path / "in.csv",
        rules_path=tmp_path / "rules.yaml",
        top_issues=10,
    )

    result = report["results"][0]
    assert result["pass"] is False
    assert result["actual"]["value"] is None
    assert result["message"] == "missing column"


def test_type_numeric_parse_success(tmp_path):
    df = pd.DataFrame({"a": [1, "2", "abc", None]})
    rules_data = {
        "checks": [
            {
                "name": "types",
                "rules": [
                    {
                        "id": "R_type",
                        "type": "type",
                        "column": "a",
                        "expected": "numeric",
                        "min_parse_success": 0.5,
                    }
                ],
            }
        ]
    }

    report = run_scan(
        df=df,
        rules_data=rules_data,
        input_path=tmp_path / "in.csv",
        rules_path=tmp_path / "rules.yaml",
        top_issues=10,
    )

    result = report["results"][0]
    assert result["actual"]["value"] == pytest.approx(2 / 3)


def test_range_compliance(tmp_path):
    df = pd.DataFrame({"b": [5, 15, 25, None]})
    rules_data = {
        "checks": [
            {
                "name": "range",
                "rules": [
                    {
                        "id": "R_range",
                        "type": "range",
                        "column": "b",
                        "min_value": 10,
                        "max_value": 20,
                        "min_compliance": 0.5,
                    }
                ],
            }
        ]
    }

    report = run_scan(
        df=df,
        rules_data=rules_data,
        input_path=tmp_path / "in.csv",
        rules_path=tmp_path / "rules.yaml",
        top_issues=10,
    )

    result = report["results"][0]
    assert result["actual"]["value"] == pytest.approx(1 / 3)


def test_uniqueness_ratio(tmp_path):
    df = pd.DataFrame({"u": ["a", "a", "b", None]})
    rules_data = {
        "checks": [
            {
                "name": "uniq",
                "rules": [
                    {
                        "id": "R_unique",
                        "type": "uniqueness",
                        "column": "u",
                        "min": 0.5,
                    }
                ],
            }
        ]
    }

    report = run_scan(
        df=df,
        rules_data=rules_data,
        input_path=tmp_path / "in.csv",
        rules_path=tmp_path / "rules.yaml",
        top_issues=10,
    )

    result = report["results"][0]
    assert result["actual"]["value"] == pytest.approx(2 / 3)


def test_domain_compliance(tmp_path):
    df = pd.DataFrame({"c": ["KR", "US", "FR", None]})
    rules_data = {
        "checks": [
            {
                "name": "domain",
                "rules": [
                    {
                        "id": "R_domain",
                        "type": "domain",
                        "column": "c",
                        "allowed_values": ["KR", "US"],
                        "min_compliance": 0.5,
                    }
                ],
            }
        ]
    }

    report = run_scan(
        df=df,
        rules_data=rules_data,
        input_path=tmp_path / "in.csv",
        rules_path=tmp_path / "rules.yaml",
        top_issues=10,
    )

    result = report["results"][0]
    assert result["actual"]["value"] == pytest.approx(2 / 3)


def test_pattern_match_rate(tmp_path):
    df = pd.DataFrame({"e": ["a@b.com", "bad", None]})
    rules_data = {
        "checks": [
            {
                "name": "pattern",
                "rules": [
                    {
                        "id": "R_pattern",
                        "type": "pattern",
                        "column": "e",
                        "regex": r"^[^@]+@[^@]+\.[^@]+$",
                        "min_compliance": 0.5,
                    }
                ],
            }
        ]
    }

    report = run_scan(
        df=df,
        rules_data=rules_data,
        input_path=tmp_path / "in.csv",
        rules_path=tmp_path / "rules.yaml",
        top_issues=10,
    )

    result = report["results"][0]
    assert result["actual"]["value"] == pytest.approx(1 / 2)


def test_outlier_iqr_rate_and_samples(tmp_path):
    df = pd.DataFrame({"x": [1, 2, 3, 100]})
    rules_data = {
        "checks": [
            {
                "name": "outliers",
                "rules": [
                    {
                        "id": "R_out_iqr",
                        "type": "outlier",
                        "column": "x",
                        "method": "iqr",
                        "iqr_k": 1.5,
                        "max_rate": 0.2,
                        "sample_n": 2,
                    }
                ],
            }
        ]
    }

    report = run_scan(
        df=df,
        rules_data=rules_data,
        input_path=tmp_path / "in.csv",
        rules_path=tmp_path / "rules.yaml",
        top_issues=10,
    )

    result = report["results"][0]
    assert result["actual"]["value"] == pytest.approx(1 / 4)
    assert any(sample["row"] == 3 for sample in result.get("samples", []))


def test_outlier_mad_rate(tmp_path):
    df = pd.DataFrame({"x": [1, 2, 3, 4, 100]})
    rules_data = {
        "checks": [
            {
                "name": "outliers",
                "rules": [
                    {
                        "id": "R_out_mad",
                        "type": "outlier",
                        "column": "x",
                        "method": "mad",
                        "mad_threshold": 3.5,
                        "max_rate": 0.5,
                    }
                ],
            }
        ]
    }

    report = run_scan(
        df=df,
        rules_data=rules_data,
        input_path=tmp_path / "in.csv",
        rules_path=tmp_path / "rules.yaml",
        top_issues=10,
    )

    result = report["results"][0]
    assert result["actual"]["value"] == pytest.approx(1 / 5)


def test_outlier_mad_zero(tmp_path):
    df = pd.DataFrame({"x": [5, 5, 5]})
    rules_data = {
        "checks": [
            {
                "name": "outliers",
                "rules": [
                    {
                        "id": "R_out_mad_zero",
                        "type": "outlier",
                        "column": "x",
                        "method": "mad",
                        "mad_threshold": 3.5,
                        "max_rate": 0.5,
                    }
                ],
            }
        ]
    }

    report = run_scan(
        df=df,
        rules_data=rules_data,
        input_path=tmp_path / "in.csv",
        rules_path=tmp_path / "rules.yaml",
        top_issues=10,
    )

    result = report["results"][0]
    assert result["actual"]["value"] is None
    assert result["pass"] is False
    assert result["message"] == "mad is zero"


def test_predicate_compliance(tmp_path):
    df = pd.DataFrame({"a": [2, 1, 3, None], "b": [1, 2, 2, 5]})
    rules_data = {
        "checks": [
            {
                "name": "predicate",
                "rules": [
                    {
                        "id": "R_pred",
                        "type": "predicate",
                        "columns": ["a", "b"],
                        "operator": ">=",
                        "min_compliance": 0.5,
                    }
                ],
            }
        ]
    }

    report = run_scan(
        df=df,
        rules_data=rules_data,
        input_path=tmp_path / "in.csv",
        rules_path=tmp_path / "rules.yaml",
        top_issues=10,
    )

    result = report["results"][0]
    assert result["actual"]["value"] == pytest.approx(2 / 3)


def test_predicate_no_valid_rows(tmp_path):
    df = pd.DataFrame({"a": [None, None], "b": [None, None]})
    rules_data = {
        "checks": [
            {
                "name": "predicate",
                "rules": [
                    {
                        "id": "R_pred_empty",
                        "type": "predicate",
                        "columns": ["a", "b"],
                        "operator": ">=",
                        "min_compliance": 0.5,
                    }
                ],
            }
        ]
    }

    report = run_scan(
        df=df,
        rules_data=rules_data,
        input_path=tmp_path / "in.csv",
        rules_path=tmp_path / "rules.yaml",
        top_issues=10,
    )

    result = report["results"][0]
    assert result["actual"]["value"] is None
    assert result["pass"] is False
    assert result["message"] == "no valid rows"


def test_report_html_generated(tmp_path):
    from dqscan.report import write_html_report

    report = {
        "meta": {
            "input_path": "data/sample.csv",
            "rules_path": "configs/rules.yaml",
            "rows": 0,
            "cols": 0,
            "created_at": "2026-01-01T00:00:00Z",
        },
        "summary": {"pass": 0, "fail": 0, "top_issues": []},
        "results": [],
    }

    html_path = write_html_report(report, tmp_path)
    assert html_path.exists()
    assert "DQ Scan Report" in html_path.read_text(encoding="utf-8")
