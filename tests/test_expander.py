import pandas as pd

from dqscan.expander import expand_rules
from dqscan.schema import build_schema


def test_selector_numeric_expands():
    df = pd.DataFrame({
        "num1": [1, 2, 3],
        "num2": [4.0, 5.0, 6.0],
        "txt": ["a", "b", "c"],
    })
    schema = build_schema(df)
    rules_data = {
        "checks": [
            {
                "name": "sel",
                "rules": [
                    {
                        "id": "R_missing_numeric",
                        "type": "missing_rate",
                        "selector": {"dtype": "numeric"},
                        "max": 0.1,
                    }
                ],
            }
        ]
    }

    expanded, warnings = expand_rules(rules_data, schema)
    assert not warnings
    rules = expanded["checks"][0]["rules"]
    ids = {r["id"] for r in rules}
    assert len(rules) == 2
    assert "R_missing_numeric__col=num1" in ids
    assert "R_missing_numeric__col=num2" in ids


def test_selector_exclude_and_regex():
    df = pd.DataFrame({
        "ck_score": [1, 2, 3],
        "ck_total": [10, 20, 30],
        "other": [5, 6, 7],
    })
    schema = build_schema(df)
    rules_data = {
        "checks": [
            {
                "name": "sel",
                "rules": [
                    {
                        "id": "R_outlier_ck",
                        "type": "outlier",
                        "selector": {
                            "dtype": ["numeric"],
                            "exclude": ["ck_total"],
                            "name_regex": "^ck_",
                        },
                        "method": "iqr",
                        "max_rate": 0.1,
                    }
                ],
            }
        ]
    }

    expanded, _ = expand_rules(rules_data, schema)
    rules = expanded["checks"][0]["rules"]
    assert len(rules) == 1
    assert rules[0]["column"] == "ck_score"
