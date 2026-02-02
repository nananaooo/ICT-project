# dqscan (skeleton)

Minimal CSV data quality scanner package for this project. This stage only parses CLI args, validates file paths, loads rules YAML, prints a short summary, and ensures the output directory exists.

## Setup (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

```powershell
python -m dqscan --input data/sample.csv --rules configs/rules.yaml --out reports
```

This generates `report.json` and `report.html` in the output directory. The HTML report includes a Top Issues table.

## Local Transformers + bitsandbytes (optional)

If you want to run the rule copilot locally (no Hugging Face API server), install:

```powershell
pip install transformers bitsandbytes accelerate sentencepiece
```

Then set these env vars:

```powershell
$env:TRANSFORMERS_MODEL="meta-llama/Llama-3.1-8B-Instruct"
$env:TRANSFORMERS_LOAD_IN_4BIT="true"
$env:TRANSFORMERS_CTX="4096"
```

Then run:

```powershell
python -m dqscan --input data/sample.csv --out reports --rules-from-nl-file rule_copilot_demo\user_input_NL
```

## Tests

```powershell
pytest -q
```

## Example output

```
dqscan summary
- input: data/sample.csv
- rules: configs/rules.yaml
- out: reports
- checks: 2
- rules: 11
- rule_ids: R1_completeness_user_id, R2_completeness_age, R3_uniqueness_user_id, R4_type_age_numeric, R5_type_signup_dt, R6_range_age, R7_pattern_email, R8_predicate_end_after_start, R9_outlier_income_mad, R10_outlier_amount_iqr, R11_domain_country
```

## Rule examples (type/range)

```yaml
- id: "R_type_age_numeric"
  type: "type"
  column: "age"
  expected: "numeric"
  min_parse_success: 0.99

- id: "R_range_age"
  type: "range"
  column: "age"
  min_value: 0
  max_value: 120
  min_compliance: 0.99
```

```yaml
- id: "R_uniqueness_user_id"
  type: "uniqueness"
  column: "user_id"
  min: 0.999

- id: "R_domain_country"
  type: "domain"
  column: "country"
  allowed_values: ["KR", "US", "JP", "CN"]
  min_compliance: 0.98

- id: "R_pattern_email"
  type: "pattern"
  column: "email"
  regex: "^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$"
  min_compliance: 0.98

- id: "R_outlier_amount_iqr"
  type: "outlier"
  column: "amount"
  method: "iqr"
  iqr_k: 1.5
  max_rate: 0.02

- id: "R_outlier_income_mad"
  type: "outlier"
  column: "income"
  method: "mad"
  mad_threshold: 3.5
  max_rate: 0.02

- id: "R_predicate_time_order"
  type: "predicate"
  columns: ["start_time", "end_time"]
  operator: "<="
  min_compliance: 0.99
```

## Selector rules

Selector-based rules are expanded at runtime into per-column rules based on the inferred schema. If a selector matches 0 columns, the rule is skipped and a warning is printed.

```yaml
- id: "R_missing_numeric_all"
  type: "missing_rate"
  selector:
    dtype: ["numeric"]
    exclude: ["user_id"]
  max: 0.05

- id: "R_outlier_prefixed"
  type: "outlier"
  selector:
    name_regex: "^(ck_|LDHH_)"
    dtype: ["numeric"]
  method: "iqr"
  iqr_k: 1.5
  max_rate: 0.02
```
