"""CLI entry for dqscan."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .expander import expand_rules
from .llm_rule_copilot import generate_rules_json, json_to_rules_yaml
from .report import write_html_report, write_report
from .runner import run_scan
from .schema import build_schema
from .rules import count_checks, extract_rule_ids, load_rules, validate_rules_data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dqscan",
        description="Minimal CSV data quality scanner (skeleton).",
    )
    parser.add_argument("--input", required=True, help="Path to input CSV file")
    parser.add_argument("--rules", help="(Disabled) Path to rules YAML file")
    parser.add_argument("--out", required=True, help="Output directory for reports")
    parser.add_argument(
        "--rules-from-nl",
        help="Natural language rules request (overrides --rules)",
    )
    parser.add_argument(
        "--rules-from-nl-file",
        help="Path to text file containing natural language rules request (overrides --rules)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    rules_path = Path(args.rules) if args.rules else None
    out_dir = Path(args.out)

    if not input_path.exists():
        parser.error(f"Input CSV not found: {input_path}")
    if not input_path.is_file():
        parser.error(f"Input path is not a file: {input_path}")
    if args.rules:
        parser.error("--rules is disabled. Use --rules-from-nl or --rules-from-nl-file.")
    if not args.rules_from_nl and not args.rules_from_nl_file:
        parser.error("--rules-from-nl or --rules-from-nl-file is required.")

    out_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir = out_dir.parent if out_dir.parent != out_dir else out_dir
    artifact_dir.mkdir(parents=True, exist_ok=True)

    nl_text = None
    if args.rules_from_nl_file:
        nl_path = Path(args.rules_from_nl_file)
        if not nl_path.exists():
            parser.error(f"Natural language file not found: {nl_path}")
        nl_text = nl_path.read_text(encoding="utf-8")
    if args.rules_from_nl:
        nl_text = args.rules_from_nl

    if nl_text and "EQ.csv" in nl_text and input_path.name.lower() != "eq.csv":
        eq_path = input_path.parent / "EQ.csv"
        if eq_path.exists():
            input_path = eq_path

    df = pd.read_csv(input_path)
    schema = build_schema(df)

    if nl_text:
        try:
            generated = generate_rules_json(nl_text)
        except ValueError as exc:
            parser.error(str(exc))
        json_path = artifact_dir / "rules.generated.json"
        json_path.write_text(json.dumps(generated, indent=2, ensure_ascii=True), encoding="utf-8")
        yaml_text = json_to_rules_yaml(generated)
        generated_path = artifact_dir / "rules.generated.yaml"
        generated_path.write_text(yaml_text, encoding="utf-8")
        rules_path = generated_path

    if rules_path is None:
        parser.error("Rules path was not provided.")
    rules_data = load_rules(rules_path)
    if nl_text:
        errors = validate_rules_data(rules_data, columns=set(df.columns))
        if errors:
            parser.error("Generated rules failed validation:\n- " + "\n- ".join(errors))

    expanded_rules_data, warnings = expand_rules(rules_data, schema)

    check_count = count_checks(expanded_rules_data)
    rule_ids = extract_rule_ids(expanded_rules_data)

    print("dqscan summary")
    print(f"- input: {input_path}")
    print(f"- rules: {rules_path}")
    print(f"- out: {out_dir}")
    print(f"- checks: {check_count}")
    print(f"- rules: {len(rule_ids)}")
    if rule_ids:
        print("- rule_ids: " + ", ".join(rule_ids))
    if warnings:
        print("- selector_warnings:")
        for msg in warnings:
            print(f"  - {msg}")

    top_issues = (
        expanded_rules_data.get("outputs", {}).get("top_issues", 10)
        if isinstance(expanded_rules_data.get("outputs"), dict)
        else 10
    )
    report = run_scan(
        df=df,
        rules_data=expanded_rules_data,
        input_path=input_path,
        rules_path=rules_path,
        top_issues=top_issues,
    )
    report_path = write_report(report, out_dir)
    html_path = write_html_report(report, out_dir)
    print(f"- report: {report_path}")
    print(f"- html: {html_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
