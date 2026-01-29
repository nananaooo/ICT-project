"""Report generation utilities."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any


def write_report(report: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "report.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    return report_path


def write_html_report(report: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "report.html"
    html = _render_html(report)
    report_path.write_text(html, encoding="utf-8")
    return report_path


def _format_threshold(threshold: dict[str, Any]) -> str:
    parts = []
    if not isinstance(threshold, dict):
        return ""
    for key in ("min", "max", "min_value", "max_value", "method", "iqr_k", "mad_threshold"):
        if key in threshold and threshold[key] is not None:
            parts.append(f"{key}={threshold[key]}")
    if "columns" in threshold and threshold.get("columns"):
        cols = threshold.get("columns")
        if isinstance(cols, (list, tuple)) and len(cols) == 2:
            parts.append(f"columns={cols[0]}:{cols[1]}")
    if "operator" in threshold and threshold.get("operator"):
        parts.append(f"operator={threshold.get('operator')}")
    if "expression" in threshold and threshold.get("expression"):
        parts.append(f"expr={threshold.get('expression')}")
    if "allowed_values" in threshold and threshold.get("allowed_values") is not None:
        allowed = threshold.get("allowed_values")
        if isinstance(allowed, (list, tuple)):
            parts.append(f"allowed={len(allowed)} values")
        else:
            parts.append("allowed=1 value")
    if "regex" in threshold and threshold.get("regex"):
        parts.append("regex=...")
    return ", ".join(parts)


def _format_samples(samples: Any, limit: int = 5) -> str:
    if not samples:
        return ""
    out = []
    for sample in samples[:limit]:
        row = sample.get("row")
        value = sample.get("value")
        out.append(f"{row}:{value}")
    return ", ".join(out)


def _render_html(report: dict[str, Any]) -> str:
    meta = report.get("meta", {})
    summary = report.get("summary", {})
    results = report.get("results", [])

    top_issues = summary.get("top_issues", [])
    pass_count = summary.get("pass", 0)
    fail_count = summary.get("fail", 0)

    def esc(value: Any) -> str:
        return escape("" if value is None else str(value))

    head = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>DQ Scan Report</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 24px; color: #222; }
    h1 { margin-bottom: 8px; }
    h2 { margin-top: 24px; }
    .meta { font-size: 14px; color: #444; }
    table { border-collapse: collapse; width: 100%; margin-top: 8px; }
    th, td { border: 1px solid #ddd; padding: 8px; vertical-align: top; }
    th { background: #f5f5f5; text-align: left; }
    tr:nth-child(even) { background: #fafafa; }
    td { word-break: break-word; overflow-wrap: anywhere; }
    .pass { color: #0a7d23; font-weight: 600; }
    .fail { color: #b00020; font-weight: 600; }
  </style>
</head>
<body>
  <h1>DQ Scan Report</h1>
"""

    meta_html = f"""
  <div class="meta">
    <div><strong>Input:</strong> {esc(meta.get('input_path'))}</div>
    <div><strong>Rules:</strong> {esc(meta.get('rules_path'))}</div>
    <div><strong>Rows:</strong> {esc(meta.get('rows'))} | <strong>Cols:</strong> {esc(meta.get('cols'))}</div>
    <div><strong>Created:</strong> {esc(meta.get('created_at'))}</div>
  </div>
"""

    summary_html = f"""
  <h2>Summary</h2>
  <div>Pass: <span class="pass">{esc(pass_count)}</span> | Fail: <span class="fail">{esc(fail_count)}</span></div>
"""

    top_rows = []
    for issue in top_issues:
        top_rows.append(
            "<tr>"
            f"<td>{esc(issue.get('id'))}</td>"
            f"<td>{esc(issue.get('type'))}</td>"
            f"<td>{esc(issue.get('column') if issue.get('column') else '-')}</td>"
            f"<td>{esc(issue.get('severity'))}</td>"
            f"<td>{esc(issue.get('violation_rate'))}</td>"
            f"<td>{esc(issue.get('score'))}</td>"
            f"<td>{esc(issue.get('message'))}</td>"
            "</tr>"
        )
    top_table = """
  <h2>Top Issues</h2>
  <table>
    <thead>
      <tr>
        <th>id</th>
        <th>type</th>
        <th>column</th>
        <th>severity</th>
        <th>violation_rate</th>
        <th>score</th>
        <th>message</th>
      </tr>
    </thead>
    <tbody>
"""
    top_table += "\n".join(top_rows) if top_rows else "<tr><td colspan=\"7\">No issues</td></tr>"
    top_table += """
    </tbody>
  </table>
"""

    result_rows = []
    for r in results:
        threshold = _format_threshold(r.get("threshold", {}))
        samples = _format_samples(r.get("samples"))
        result_rows.append(
            "<tr>"
            f"<td>{esc(r.get('id'))}</td>"
            f"<td>{esc(r.get('type'))}</td>"
            f"<td>{esc(r.get('column') if r.get('column') else '-')}</td>"
            f"<td>{esc(r.get('pass'))}</td>"
            f"<td>{esc((r.get('actual') or {}).get('value'))}</td>"
            f"<td>{esc(threshold)}</td>"
            f"<td>{esc(r.get('violation_rate'))}</td>"
            f"<td>{esc(r.get('severity'))}</td>"
            f"<td>{esc(r.get('score'))}</td>"
            f"<td>{esc(r.get('message'))}</td>"
            f"<td>{esc(samples)}</td>"
            "</tr>"
        )

    results_table = """
  <h2>Full Results</h2>
  <table>
    <thead>
      <tr>
        <th>id</th>
        <th>type</th>
        <th>column</th>
        <th>pass</th>
        <th>actual.value</th>
        <th>threshold</th>
        <th>violation_rate</th>
        <th>severity</th>
        <th>score</th>
        <th>message</th>
        <th>samples</th>
      </tr>
    </thead>
    <tbody>
"""
    results_table += "\n".join(result_rows) if result_rows else "<tr><td colspan=\"11\">No results</td></tr>"
    results_table += """
    </tbody>
  </table>
"""

    tail = """
</body>
</html>
"""

    return head + meta_html + summary_html + top_table + results_table + tail
