"""Minimal web UI for DQ scan rule copilot."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from flask import Flask, abort, jsonify, render_template_string, request, send_file, url_for
from werkzeug.utils import secure_filename

from .expander import expand_rules
from .llm_rule_copilot import generate_rules_json, json_to_rules_yaml
from .report import write_html_report, write_report
from .runner import run_scan
from .schema import build_schema
from .rules import (
    add_missing_rules_for_domain,
    count_checks,
    extract_rule_ids,
    load_rules,
    prune_rules_for_columns,
    validate_rules_data,
)

APP = Flask(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = PROJECT_ROOT / "data" / "EQ.csv"
REPORTS_ROOT = PROJECT_ROOT / "reports" / "web_ui"

_RUNS: dict[str, dict[str, Any]] = {}
_RUN_LOCK = threading.Lock()

INDEX_HTML = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>DQ Rule Copilot</title>
    <style>
      body { font-family: Arial, sans-serif; background: #f6f7fb; margin: 0; padding: 32px; }
      .card { background: #fff; max-width: 900px; margin: 0 auto; padding: 24px; border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.08); }
      h1 { margin-top: 0; }
      label { display: block; font-weight: 600; margin-top: 16px; }
      textarea { width: 100%; min-height: 140px; font-family: inherit; font-size: 14px; padding: 12px; border: 1px solid #cfd6e4; border-radius: 6px; }
      input[type=file] { margin-top: 8px; }
      .hint { color: #5a6375; font-size: 13px; margin-top: 6px; }
      .actions { margin-top: 20px; }
      button { background: #2456f2; color: #fff; border: none; padding: 10px 18px; border-radius: 6px; font-size: 14px; cursor: pointer; }
      button:hover { background: #1f46c7; }
    </style>
  </head>
  <body>
    <div class="card">
      <h1>DQ Rule Copilot</h1>
      <form method="post" action="{{ url_for('run_scan_web') }}" enctype="multipart/form-data">
        <label for="nl_text">Natural language rules request</label>
        <textarea id="nl_text" name="nl_text" required>{{ sample_text }}</textarea>
        <label for="dataset">Dataset (CSV)</label>
        <input id="dataset" type="file" name="dataset" accept=".csv" />
        <div class="actions">
          <button type="submit">Run scan</button>
        </div>
      </form>
    </div>
  </body>
</html>
"""

PROGRESS_HTML = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>DQ Rule Copilot - Running</title>
    <style>
      body { font-family: Arial, sans-serif; background: #f6f7fb; margin: 0; padding: 32px; }
      .card { background: #fff; max-width: 780px; margin: 0 auto; padding: 24px; border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.08); }
      h1 { margin-top: 0; }
      .status { margin-top: 16px; font-size: 16px; }
      .spinner {
        width: 28px; height: 28px; border: 4px solid #dce2f3; border-top-color: #2456f2;
        border-radius: 50%; animation: spin 0.9s linear infinite; display: inline-block; vertical-align: middle;
      }
      @keyframes spin { to { transform: rotate(360deg); } }
      .muted { color: #5a6375; font-size: 13px; margin-top: 8px; }
    </style>
  </head>
  <body>
    <div class="card">
      <h1>Running scan...</h1>
      <div class="status">
        <span class="spinner"></span>
        <span id="status-text">Queued</span>
      </div>
      <div class="muted">This page will update automatically.</div>
    </div>
    <script>
      const runId = "{{ run_id }}";
      async function poll() {
        try {
          const res = await fetch(`/status/${runId}`);
          if (!res.ok) {
            document.getElementById("status-text").textContent = "Status unavailable";
            return;
          }
          const data = await res.json();
          const state = data.state || "running";
          const step = data.step || "Working...";
          document.getElementById("status-text").textContent = step;
          if (state === "done") {
            window.location.href = `/result/${runId}`;
            return;
          }
          if (state === "error") {
            window.location.href = `/result/${runId}`;
            return;
          }
        } catch (err) {
          document.getElementById("status-text").textContent = "Waiting for server...";
        }
      }
      setInterval(poll, 1200);
      poll();
    </script>
  </body>
</html>
"""

RESULT_HTML = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>DQ Rule Copilot - Result</title>
    <style>
      body { font-family: Arial, sans-serif; background: #f6f7fb; margin: 0; padding: 32px; }
      .card { background: #fff; max-width: 980px; margin: 0 auto; padding: 24px; border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.08); }
      h1 { margin-top: 0; }
      .meta { color: #4a5362; font-size: 14px; }
      .meta div { margin-top: 4px; }
      ul { padding-left: 18px; }
      a { color: #2456f2; }
      .warn { color: #a04a00; }
      .links a { display: inline-block; margin-right: 12px; margin-top: 6px; }
    </style>
  </head>
  <body>
    <div class="card">
      <h1>Run complete</h1>
      <div class="meta">
        <div><strong>Input:</strong> {{ input_path }}</div>
        <div><strong>Rules:</strong> {{ rules_path }}</div>
        <div><strong>Output:</strong> {{ out_dir }}</div>
      </div>
      <h2>Summary</h2>
      <ul>
        <li>Checks: {{ check_count }}</li>
        <li>Rules: {{ rule_count }}</li>
      </ul>
      {% if warnings %}
        <ul>
          {% for msg in warnings %}
            {% if "Added missing_rate rule for column" not in msg %}
              <li>{{ msg }}</li>
            {% endif %}
          {% endfor %}
        </ul>
      {% endif %}
      <h2>Reports</h2>
      <div class="links">
        <a href="{{ url_for('get_report', run_id=run_id, filename='report.html') }}" target="_blank">Open HTML report</a>
        <a href="{{ url_for('get_report', run_id=run_id, filename='report.json') }}">Download JSON report</a>
        <a href="{{ url_for('get_report', run_id=run_id, filename='rules.generated.yaml') }}">Download rules YAML</a>
        <a href="{{ url_for('get_report', run_id=run_id, filename='rules.generated.json') }}">Download rules JSON</a>
      </div>
      <p><a href="{{ url_for('index') }}">Run another scan</a></p>
    </div>
  </body>
</html>
"""

ERROR_HTML = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>DQ Rule Copilot - Error</title>
    <style>
      body { font-family: Arial, sans-serif; background: #f6f7fb; margin: 0; padding: 32px; }
      .card { background: #fff; max-width: 900px; margin: 0 auto; padding: 24px; border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.08); }
      h1 { margin-top: 0; color: #b00020; }
      pre { white-space: pre-wrap; background: #f2f3f7; padding: 12px; border-radius: 6px; }
      a { color: #2456f2; }
    </style>
  </head>
  <body>
    <div class="card">
      <h1>Run failed</h1>
      <pre>{{ error_message }}</pre>
      <p><a href="{{ url_for('index') }}">Back to form</a></p>
    </div>
  </body>
</html>
"""


def _default_dataset_path() -> Path:
    if DEFAULT_DATASET.exists():
        return DEFAULT_DATASET
    fallback = DEFAULT_DATASET.with_name("eq.csv")
    if fallback.exists():
        return fallback
    raise FileNotFoundError("Default dataset not found. Expected data/EQ.csv.")


def _new_run_dir() -> Path:
    REPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = REPORTS_ROOT / stamp
    if not run_dir.exists():
        return run_dir
    counter = 1
    while True:
        candidate = REPORTS_ROOT / f"{stamp}_{counter:02d}"
        if not candidate.exists():
            return candidate
        counter += 1


def _save_upload(file_storage: Any, run_dir: Path) -> Path | None:
    if not file_storage or not file_storage.filename:
        return None
    filename = secure_filename(file_storage.filename)
    if not filename:
        return None
    dest = run_dir / filename
    file_storage.save(dest)
    return dest


def _safe_send(base_dir: Path, filename: str):
    target = (base_dir / filename).resolve()
    base = base_dir.resolve()
    if not str(target).startswith(str(base)):
        abort(404)
    if not target.exists() or not target.is_file():
        abort(404)
    return send_file(target)


def _set_run_status(run_id: str, **updates: Any) -> None:
    with _RUN_LOCK:
        entry = _RUNS.get(run_id, {})
        entry.update(updates)
        _RUNS[run_id] = entry


def _get_run_status(run_id: str) -> dict[str, Any]:
    with _RUN_LOCK:
        return dict(_RUNS.get(run_id, {}))


def _run_scan_job(run_id: str, run_dir: Path, dataset_path: Path, nl_text: str) -> None:
    try:
        _set_run_status(run_id, state="running", step="Reading CSV")
        df = pd.read_csv(dataset_path)
        schema = build_schema(df)

        _set_run_status(run_id, step="Generating rules")
        generated = generate_rules_json(nl_text)

        json_path = run_dir / "rules.generated.json"
        json_path.write_text(
            json.dumps(generated, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
        yaml_text = json_to_rules_yaml(generated)
        yaml_path = run_dir / "rules.generated.yaml"
        yaml_path.write_text(yaml_text, encoding="utf-8")

        _set_run_status(run_id, step="Validating rules")
        rules_data = load_rules(yaml_path)
        rules_data, pruned_warnings = prune_rules_for_columns(
            rules_data, columns=set(df.columns)
        )
        rules_data, auto_warnings = add_missing_rules_for_domain(
            rules_data, max_missing_rate=0.0
        )
        errors = validate_rules_data(rules_data, columns=set(df.columns))
        if errors:
            raise ValueError("Generated rules failed validation:\n- " + "\n- ".join(errors))

        _set_run_status(run_id, step="Expanding selectors")
        expanded_rules_data, warnings = expand_rules(rules_data, schema)
        if pruned_warnings:
            warnings.extend(pruned_warnings)
        if auto_warnings:
            warnings.extend(auto_warnings)
        check_count = count_checks(expanded_rules_data)
        rule_ids = extract_rule_ids(expanded_rules_data)

        _set_run_status(run_id, step="Running scan")
        top_issues = (
            expanded_rules_data.get("outputs", {}).get("top_issues", 10)
            if isinstance(expanded_rules_data.get("outputs"), dict)
            else 10
        )
        report = run_scan(
            df=df,
            rules_data=expanded_rules_data,
            input_path=dataset_path,
            rules_path=yaml_path,
            top_issues=top_issues,
        )

        _set_run_status(run_id, step="Writing reports")
        write_report(report, run_dir)
        write_html_report(report, run_dir)

        try:
            input_label = str(dataset_path.relative_to(PROJECT_ROOT))
        except ValueError:
            input_label = str(dataset_path)

        _set_run_status(
            run_id,
            state="done",
            step="Done",
            input_path=input_label,
            rules_path=str(yaml_path.relative_to(PROJECT_ROOT)),
            out_dir=str(run_dir.relative_to(PROJECT_ROOT)),
            check_count=check_count,
            rule_count=len(rule_ids),
            warnings=warnings,
        )
    except Exception as exc:
        _set_run_status(run_id, state="error", step="Failed", error_message=str(exc))


@APP.route("/", methods=["GET"])
def index():
    sample_text = "Create rules: age must be between 0 and 120, min compliance 0.99."
    try:
        default_dataset = _default_dataset_path()
        default_label = str(default_dataset.relative_to(PROJECT_ROOT))
    except FileNotFoundError:
        default_label = "data/EQ.csv (missing)"
    return render_template_string(
        INDEX_HTML,
        sample_text=sample_text,
        default_dataset=default_label,
    )


@APP.route("/run", methods=["POST"])
def run_scan_web():
    nl_text = (request.form.get("nl_text") or "").strip()
    if not nl_text:
        return render_template_string(ERROR_HTML, error_message="Natural language request is required.")

    run_dir = _new_run_dir()
    run_dir.mkdir(parents=True, exist_ok=True)
    run_id = run_dir.name

    dataset_file = request.files.get("dataset")
    dataset_path = _save_upload(dataset_file, run_dir)
    if dataset_path is None:
        try:
            dataset_path = _default_dataset_path()
        except FileNotFoundError as exc:
            return render_template_string(ERROR_HTML, error_message=str(exc))

    _set_run_status(run_id, state="running", step="Queued")
    worker = threading.Thread(
        target=_run_scan_job,
        args=(run_id, run_dir, dataset_path, nl_text),
        daemon=True,
    )
    worker.start()

    return render_template_string(PROGRESS_HTML, run_id=run_id)


@APP.route("/status/<run_id>", methods=["GET"])
def run_status(run_id: str):
    status = _get_run_status(run_id)
    if not status:
        return jsonify({"state": "error", "step": "Unknown run"}), 404
    return jsonify(status)


@APP.route("/result/<run_id>", methods=["GET"])
def run_result(run_id: str):
    status = _get_run_status(run_id)
    if not status:
        return render_template_string(ERROR_HTML, error_message="Run not found.")
    if status.get("state") == "error":
        return render_template_string(ERROR_HTML, error_message=status.get("error_message", "Run failed."))
    if status.get("state") != "done":
        return render_template_string(PROGRESS_HTML, run_id=run_id)
    return render_template_string(
        RESULT_HTML,
        run_id=run_id,
        input_path=status.get("input_path"),
        rules_path=status.get("rules_path"),
        out_dir=status.get("out_dir"),
        check_count=status.get("check_count"),
        rule_count=status.get("rule_count"),
        warnings=status.get("warnings"),
    )


@APP.route("/reports/<run_id>/<path:filename>", methods=["GET"])
def get_report(run_id: str, filename: str):
    run_dir = REPORTS_ROOT / run_id
    return _safe_send(run_dir, filename)


def main() -> int:
    APP.run(host="127.0.0.1", port=5000, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
