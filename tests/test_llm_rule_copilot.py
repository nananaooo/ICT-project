import json

import pytest

from dqscan.llm_rule_copilot import (
    default_schema_spec,
    generate_rules_json,
    json_to_rules_yaml,
    validate_rules_json,
)
from dqscan.rules import load_rules, validate_rules_data


def test_validate_rules_json_rejects_unsupported_type():
    obj = {
        "checks": [
            {
                "name": "bad",
                "rules": [
                    {"id": "R_bad", "type": "not_supported", "column": "a", "min": 1.0}
                ],
            }
        ]
    }
    ok, errors = validate_rules_json(obj, default_schema_spec())
    assert not ok
    assert any("not supported" in err for err in errors)


def test_generate_rules_json_self_repair(monkeypatch):
    responses = [
        "{bad json}",
        json.dumps(
            {
                "checks": [
                    {
                        "name": "ok",
                        "rules": [
                            {
                                "id": "R1",
                                "type": "completeness",
                                "column": "a",
                                "min": 1.0,
                            }
                        ],
                    }
                ]
            }
        ),
    ]
    calls = {"count": 0}

    def fake_call(messages, api_key, model):
        calls["count"] += 1
        return responses[calls["count"] - 1]

    obj = generate_rules_json(
        "make completeness rule",
        api_key="test-key",
        model="test-model",
        call_fn=fake_call,
        retries=1,
    )
    assert calls["count"] == 2
    assert obj["checks"][0]["rules"][0]["type"] == "completeness"


def test_json_to_yaml_and_loader(tmp_path):
    obj = {
        "checks": [
            {
                "name": "ok",
                "rules": [
                    {
                        "id": "R1",
                        "type": "missing_rate",
                        "column": "a",
                        "max": 0.1,
                    }
                ],
            }
        ]
    }
    yaml_text = json_to_rules_yaml(obj)
    path = tmp_path / "rules.generated.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    loaded = load_rules(path)
    errors = validate_rules_data(loaded, columns={"a"})
    assert not errors
