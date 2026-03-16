#!/bin/bash
# 评测脚本：运行 Maven + JaCoCo，输出统一覆盖率契约到 results/
# 用法: ./eval.sh [--subset datasets/train.json] [--output results/coverage_report.json]

export PATH="/opt/homebrew/opt/openjdk/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SUBSET_FILE=""
OUTPUT_FILE="$SCRIPT_DIR/results/coverage_report.json"

# 解析参数
while [[ $# -gt 0 ]]; do
    case $1 in
        --subset)  SUBSET_FILE="$2"; shift 2 ;;
        --output)
            # 转换为绝对路径（避免 cd 后相对路径失效）
            if [[ "$2" = /* ]]; then
                OUTPUT_FILE="$2"
            else
                OUTPUT_FILE="$SCRIPT_DIR/$2"
            fi
            shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

PROJECT_DIR="$SCRIPT_DIR/$(python3 -c "import json; print(json.load(open('$SCRIPT_DIR/project.json'))['maven_dir'])")"
MVN_FLAGS=$(python3 -c "import json; print(json.load(open('$SCRIPT_DIR/project.json'))['maven_flags'])")
JACOCO_XML="$PROJECT_DIR/target/site/jacoco/jacoco.xml"
RESULTS_DIR="$(dirname "$OUTPUT_FILE")"

mkdir -p "$RESULTS_DIR"
cd "$PROJECT_DIR"

echo "→ Running Maven tests with JaCoCo..."
mvn $MVN_FLAGS clean test -Dmaven.test.failure.ignore=true -q 2>&1
MVN_TEST_EXIT=$?
if [ $MVN_TEST_EXIT -ne 0 ]; then
    echo "  [WARN] Maven reported issues, continuing..."
fi

echo "→ Generating JaCoCo report..."
mvn $MVN_FLAGS jacoco:report -q 2>&1
JACOCO_EXIT=$?
if [ $JACOCO_EXIT -ne 0 ]; then
    echo "  [WARN] JaCoCo report generation failed"
fi

if [ ! -f "$JACOCO_XML" ]; then
    echo "  [WARN] No JaCoCo XML — likely no test files. Writing 0% report."
    echo '{"meta":{"schema_version":"ut_coverage_v1"},"coverage":{"line":{"covered":0,"missed":0,"total":0,"pct":0.0},"branch":{"covered":0,"missed":0,"total":0,"pct":0.0},"method":{"covered":0,"missed":0,"total":0,"pct":0.0}},"classes":{},"packages":[],"_total":{"line":0.0,"branch":0.0,"method":0.0}}' > "$OUTPUT_FILE"
    echo "   TOTAL: 0% (no tests)"
else
    echo "→ Parsing coverage report..."
    python3 "$SCRIPT_DIR/parse_coverage.py" "$JACOCO_XML" > "$OUTPUT_FILE"
fi

# 若指定了 subset，过滤只保留 subset 中的类，并重算 _total
if [ -n "$SUBSET_FILE" ]; then
    if [[ "$SUBSET_FILE" = /* ]]; then
        SUBSET_PATH="$SUBSET_FILE"
    else
        SUBSET_PATH="$SCRIPT_DIR/$SUBSET_FILE"
    fi
    python3 - <<PYEOF
import json

with open("$OUTPUT_FILE") as f:
    report = json.load(f)
with open("$SUBSET_PATH") as f:
    subset = json.load(f)

subset_names = {c["class_name"] for c in subset["classes"]}
all_classes = report.get("classes", {})
filtered_classes = {k: v for k, v in all_classes.items() if k in subset_names}

# 重新计算 _total（仅 subset 类，按类平均）
if filtered_classes:
    metrics = ("line", "branch", "method")
    totals = {
        m: round(sum(cls.get(m, 0.0) for cls in filtered_classes.values()) / len(filtered_classes), 2)
        for m in metrics
    }
else:
    totals = {"line": 0.0, "branch": 0.0, "method": 0.0}

with open("$OUTPUT_FILE", "w") as f:
    report["classes"] = filtered_classes
    report["_total"] = totals
    # Keep top-level coverage pct aligned with subset totals.
    if "coverage" in report:
        for m in ("line", "branch", "method"):
            if m in report["coverage"]:
                report["coverage"][m]["pct"] = totals[m]
    json.dump(report, f, indent=2, ensure_ascii=False)
PYEOF
fi

# 写入执行质量指标（用于 gate 质量门禁）
python3 - <<PYEOF
import json
from pathlib import Path
import xml.etree.ElementTree as ET

output_file = Path("$OUTPUT_FILE")
surefire_dir = Path("$PROJECT_DIR") / "target" / "surefire-reports"

tests_run = tests_failed = tests_error = tests_skipped = 0
if surefire_dir.exists():
    for xml_file in surefire_dir.glob("TEST-*.xml"):
        try:
            root = ET.parse(xml_file).getroot()
        except ET.ParseError:
            continue
        tests_run += int(root.attrib.get("tests", 0))
        tests_failed += int(root.attrib.get("failures", 0))
        tests_error += int(root.attrib.get("errors", 0))
        tests_skipped += int(root.attrib.get("skipped", 0))

tests_passed = max(0, tests_run - tests_failed - tests_error - tests_skipped)
pass_rate = round((tests_passed / tests_run * 100.0), 2) if tests_run else 0.0

with output_file.open() as f:
    report = json.load(f)

report["test_execution"] = {
    "tests_run": tests_run,
    "tests_passed": tests_passed,
    "tests_failed": tests_failed,
    "tests_error": tests_error,
    "tests_skipped": tests_skipped,
    "pass_rate_pct": pass_rate,
}
report["quality"] = {
    "maven_test_exit_code": $MVN_TEST_EXIT,
    "jacoco_report_exit_code": $JACOCO_EXIT,
    "compile_failed": $MVN_TEST_EXIT != 0,
}

with output_file.open("w") as f:
    json.dump(report, f, indent=2, ensure_ascii=False)
PYEOF

echo "→ Coverage summary$([ -n "$SUBSET_FILE" ] && echo " (subset: $(basename $SUBSET_FILE))"):"
python3 -c "
import json
with open('$OUTPUT_FILE') as f:
    data = json.load(f)
classes = data.get('classes', {})
for name, cov in sorted(classes.items()):
    print(f'   {name:30s}  line={cov[\"line\"]:5.1f}%  branch={cov[\"branch\"]:5.1f}%  method={cov[\"method\"]:5.1f}%')
total = data.get('_total', {})
if total and classes:
    print()
    print(f'   {\"TOTAL\":30s}  line={total[\"line\"]:5.1f}%  branch={total[\"branch\"]:5.1f}%  method={total[\"method\"]:5.1f}%')
"
