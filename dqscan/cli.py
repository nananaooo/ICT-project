"""CLI entry for dqscan."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .expander import expand_rules
from .report import write_html_report, write_report
from .runner import run_scan
from .schema import build_schema
from .rules import count_checks, extract_rule_ids, load_rules


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dqscan",
        description="Minimal CSV data quality scanner (skeleton).",
    )
    parser.add_argument("--input", required=True, help="Path to input CSV file")
    parser.add_argument("--rules", required=True, help="Path to rules YAML file")
    parser.add_argument("--out", required=True, help="Output directory for reports")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    rules_path = Path(args.rules)
    out_dir = Path(args.out)

    if not input_path.exists():
        parser.error(f"Input CSV not found: {input_path}")
    if not input_path.is_file():
        parser.error(f"Input path is not a file: {input_path}")

    rules_data = load_rules(rules_path)

    out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(input_path)
    schema = build_schema(df)
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
