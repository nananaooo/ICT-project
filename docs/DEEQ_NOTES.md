# DEEQ_NOTES.md
> Based on: Schelter et al., "Automating Large-Scale Data Quality Verification" (PVLDB 2018)  
> Goal: Deequ 스타일(룰=제약, 메트릭 기반 판정)의 핵심을 우리 프로젝트(소규모 CSV 품질 진단)로 축소 적용하기 위한 노트

---

## 1. 논문이 제안하는 핵심 한 줄
데이터 품질 검증을 **유닛테스트처럼** 만들자:  
**제약(Constraint)을 선언적으로 정의 → 필요한 메트릭(통계량)을 계산 → 제약을 통과/실패로 판정 → 리포트로 남김**

---

## 2. 주요 개념/용어
- **Check**
  - 여러 개의 제약(Constraints)을 묶은 테스트 그룹(예: core-quality)
- **Constraint (제약/룰)**
  - 데이터가 만족해야 할 조건 (완전성, 유일성, 범위 준수 등)
- **Metric (메트릭/통계)**
  - 제약을 판정하기 위해 계산되는 값(Completeness, Distinctness, Quantiles 등)
- **Analyzer**
  - 특정 메트릭을 계산하는 로직(집계/통계 계산기)
- **Runner**
  - 어떤 제약이 어떤 메트릭을 필요로 하는지 결정하고 실행하는 실행기
- **Verification Result**
  - 제약별 통과/실패 + 실제 값(actual) + 임계값(threshold)

---

## 3. 왜 “제약 → 메트릭” 구조가 중요한가?
- 제약을 직접 검사하기보다, **먼저 메트릭을 계산**하면:
  - 결과가 수치로 남아 **설명 가능**(왜 실패했는지)
  - 여러 제약이 같은 메트릭을 공유하므로 **중복 계산을 줄일 수 있음**
  - 운영 환경에서는 메트릭을 저장하여 **추세/변화 감지**로 확장 가능

---

## 4. 논문 아키텍처(개념 흐름)
1) 사용자가 **Checks/Constraints**를 선언적으로 작성  
2) 시스템이 제약을 분석해 **필요 메트릭 목록**을 결정  
3) Runner가 Analyzer들을 호출해 메트릭 계산  
4) 메트릭 값을 바탕으로 제약을 판정(통과/실패)  
5) 결과 리포트 출력 + (옵션) 메트릭 히스토리 저장

> 논문은 Spark 기반으로 “한 번의 데이터 스캔에 여러 집계를 묶는” 최적화(scan-sharing)도 강조함.

---

## 5. 논문에서 주로 다루는 제약 유형(대표)
(우리 프로젝트에서는 “최소 구현”만 채택)
- **Completeness**: 결측이 얼마나 없는가
- **Uniqueness / Distinctness**: 유일성/중복 여부
- **Compliance**: 특정 조건(예: 범위, 패턴)을 만족하는 비율
- **Type consistency**: 타입/파싱 일관성
- (확장) Entropy/Mutual Information 등 정보이론 메트릭도 언급됨

---

## 6. 우리 프로젝트에 맞춘 “축소판 Deequ” 적용
### 6.1 우리가 구현할 최소 룰(6종 + outlier)
- completeness / missing_rate
- uniqueness
- type(parse_success)
- range(compliance)
- pattern(regex compliance)
- predicate(row-wise compliance)
- outlier(IQR 또는 MAD 기반 outlier_rate)

### 6.2 “분모(denominator) 정책”을 미리 고정 (중요)
- `total_count`: 전체 행 수
- `non_null_count(col)`: 해당 컬럼이 non-null인 행 수
- `valid_row_count(expr)`: predicate에 필요한 컬럼들이 모두 non-null인 행 수

권장:
- completeness/missing_rate → `total_count` 기준
- uniqueness/range/pattern/outlier → `non_null_count` 기준
- predicate(row) → `valid_row_count` 기준

---

## 7. 룰을 메트릭으로 바꾸는 방식(핵심 설계)
제약은 결국 다음 형태로 환원된다:
- `metric >= min_threshold`
- `metric <= max_threshold`

예시:
- `completeness(age) = non_null_count(age)/total_count`
- `range_compliance(age) = count(0<=age<=120)/non_null_count(age)`
- `pattern_match(email) = regex_match_count/non_null_count(email)`
- `outlier_rate(amount) = outlier_count/non_null_count(amount)`

---

## 8. 구현 시 권장 모듈 구조(소규모 버전)
- `configs/rules.yaml` : 체크/룰 선언(사용자 입력)
- `src/rules.py` : YAML 파싱 + 룰 스키마 검증
- `src/metrics.py` : 메트릭 계산 함수(통계 엔진)
- `src/runner.py` : 룰→필요 메트릭 추출→계산→판정
- `src/report.py` : report.json / report.html 생성
- `src/cli.py` : `python -m dqscan ...` 진입점

---

## 9. Edge cases(현장에서 꼭 터지는 것들)
- 컬럼이 전부 null인 경우: `non_null_count=0`
  - range/pattern/outlier/uniqueness 분모 0 처리 정책 필요
  - 권장: actual을 `None`으로 두고 룰 실패 처리 + 사유 기록
- MAD=0 (모든 값이 동일)인 경우:
  - outlier_rate를 0으로 둘지, 계산 불가로 둘지 정책 필요
  - 권장: MAD=0이면 outlier_rate=0 (변동 없음)로 두되, 참고 메시지 남김
- 숫자 파싱 실패가 많은 경우:
  - type 룰에서 parse_success가 낮게 나옴
  - range/outlier 계산은 “파싱 성공한 값”만 대상으로 수행 권장
- uniqueness에서 null 처리:
  - null을 distinct에 포함할지 제외할지 정책 필요
  - 권장: non-null만 대상으로 uniqueness 계산

---

## 10. 논문 기능 중 이번 프로젝트에서 제외(스코프 관리)
- Spark 분산 실행 및 scan-sharing 최적화(우리는 pandas 단일 머신)
- Append-only 증분 메트릭 업데이트(stateful incremental)
- 메트릭 시계열 기반 이상 탐지(추후 옵션)
- constraint 자동 추천(추후 간단 휴리스틱으로 옵션 가능)

---

## 11. 우리 프로젝트 “Definition of Done” (Deequ 컨셉 충족 기준)
- rules.yaml에 선언한 룰을 읽어서
- 필요한 통계를 계산하고
- 룰별 pass/fail + actual 값을 report.json으로 남기며
- 사람이 읽을 수 있는 report.html 요약을 만든다

(추가) 샘플 CSV + 최소 테스트 3개(결측/유일성/범위)로 재현 가능해야 함
