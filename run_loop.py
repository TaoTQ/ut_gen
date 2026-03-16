#!/usr/bin/env python3
"""
Python implementation of the UT self-improvement loop.
Keeps behavior compatible with run_loop.sh while improving maintainability.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def load_project(script_dir: Path) -> dict[str, Any]:
    return json.loads((script_dir / "project.json").read_text(encoding="utf-8"))


def run_cmd(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        check=False,
        text=True,
        capture_output=capture,
    )
    if check and result.returncode != 0:
        if capture and result.stdout:
            print(result.stdout, end="")
        if capture and result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        raise subprocess.CalledProcessError(result.returncode, args)
    return result


def write_task_context(
    script_dir: Path,
    split_file: str,
    phase: str,
    iteration: str,
    output_file: Path,
    class_name: str | None = None,
) -> int:
    split = json.loads((script_dir / split_file).read_text(encoding="utf-8"))
    classes = split.get("classes", [])
    if class_name:
        classes = [c for c in classes if c["class_name"] == class_name]

    rows = "\n".join(
        f"| {c['class_name']} | {c['source_path']} | {c['test_output_path']} |" for c in classes
    )
    content = f"""# 当前任务上下文

**阶段**: {phase}  **迭代**: {iteration}  **数据集**: {split_file}

## 待生成测试的类

| 类名 | 源码路径 | 测试输出路径 |
|------|---------|------------|
{rows}

## 指令

1. 先读取上表中的源码文件
2. 按照附件 SKILL.md 中的策略生成单元测试
3. 将测试文件覆盖写入对应的「测试输出路径」
"""
    output_file.write_text(content, encoding="utf-8")
    return len(classes)


def clean_tests(test_dir: Path) -> None:
    if not test_dir.exists():
        return
    for p in test_dir.rglob("*.java"):
        p.unlink(missing_ok=True)


def refresh_regression_subset(script_dir: Path, target_file: Path) -> None:
    train = json.loads((script_dir / "datasets/train.json").read_text(encoding="utf-8"))
    train_classes = train.get("classes", [])
    subset_classes = train_classes[: min(3, len(train_classes))]
    subset = {
        "split": "regression_subset",
        "generated_at": train.get("generated_at"),
        "total": len(subset_classes),
        "classes": subset_classes,
    }
    target_file.write_text(json.dumps(subset, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  ✓ regression subset refreshed ({len(subset_classes)} classes)")


def validate_regression_subset(script_dir: Path, subset_file: Path) -> None:
    if not subset_file.exists():
        raise RuntimeError(f"regression subset not found: {subset_file}")

    train = json.loads((script_dir / "datasets/train.json").read_text(encoding="utf-8"))
    subset = json.loads(subset_file.read_text(encoding="utf-8"))
    train_names = {c["class_name"] for c in train.get("classes", [])}
    subset_names = [c["class_name"] for c in subset.get("classes", [])]

    if not subset_names:
        raise RuntimeError("regression subset is empty")

    missing = [name for name in subset_names if name not in train_names]
    if missing:
        raise RuntimeError(f"regression subset includes non-train classes: {', '.join(missing)}")

    print(f"  ✓ regression subset ready ({len(subset_names)} classes): {', '.join(subset_names)}")



def check_compile(mvn_dir: Path, mvn_flags: str) -> bool:
    print("  -> compile sanity check...")
    args = ["mvn"] + shlex.split(mvn_flags) + ["test-compile", "-q"]
    result = run_cmd(args, cwd=mvn_dir, check=False, capture=True)
    if result.returncode == 0:
        print("  ✓ compile passed")
        return True
    print("  ✗ compile failed")
    if result.stderr:
        lines = [ln for ln in result.stderr.splitlines() if "ERROR" in ln]
        for ln in lines[:20]:
            print(f"    {ln}")
    return False


def split_classes(script_dir: Path, split_file: str) -> list[dict[str, Any]]:
    split = json.loads((script_dir / split_file).read_text(encoding="utf-8"))
    return split.get("classes", [])


# ---------------------------------------------------------------------------
# Method-level chunking for high-complexity classes
# ---------------------------------------------------------------------------

CHUNK_METHOD_THRESHOLD = int(os.environ.get("CHUNK_THRESHOLD", 20))
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", 15))
MAX_WORK_UNITS = int(os.environ.get("MAX_WORK_UNITS", 0))  # 0 = unlimited


def chunk_class_methods(classes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expand classes with many methods into multiple chunk entries.

    Each chunk gets its own test_output_path (e.g. FooGroup1Test.java).
    Classes below the threshold pass through unchanged.
    """
    result: list[dict[str, Any]] = []
    for c in classes:
        methods = c.get("public_methods", [])
        if len(methods) <= CHUNK_METHOD_THRESHOLD:
            result.append(c)
            continue

        # Split methods into groups
        total_chunks = (len(methods) + CHUNK_SIZE - 1) // CHUNK_SIZE
        test_path = Path(c["test_output_path"])
        base_name = c["class_name"]
        print(f"  [chunk] {base_name}: {len(methods)} methods → {total_chunks} chunks of ≤{CHUNK_SIZE}")

        for i in range(total_chunks):
            start = i * CHUNK_SIZE
            method_subset = methods[start : start + CHUNK_SIZE]
            chunk_test_name = f"{base_name}Group{i + 1}Test"
            chunk_test_path = str(test_path.parent / f"{chunk_test_name}.java")
            chunk = dict(c)  # shallow copy
            chunk["test_output_path"] = chunk_test_path
            chunk["test_class_name"] = chunk_test_name
            chunk["_chunk"] = {
                "index": i + 1,
                "total": total_chunks,
                "method_subset": method_subset,
                "original_class": base_name,
            }
            result.append(chunk)
    return result


def extract_method_source(source_path: str, method_names: list[str]) -> str:
    """Extract source code of specific methods from a Java file.

    Uses brace-counting to capture the full method body including nested blocks.
    Returns a string with all matched method sources separated by blank lines.
    """
    try:
        content = Path(source_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    # Match method signature lines only (no Javadoc in regex — we look back for it)
    name_pattern = "|".join(re.escape(m) for m in method_names)
    sig_re = re.compile(
        r'^[ \t]*public\s+(?:(?:static|final|synchronized|abstract)\s+)*'
        r'[\w<>\[\],\s]+?\s+(?:' + name_pattern + r')\s*\(',
        re.MULTILINE,
    )

    snippets: list[str] = []
    for match in sig_re.finditer(content):
        # Walk backwards to capture annotations and Javadoc immediately above
        start = match.start()
        # Look at preceding lines for @annotations and /** ... */ Javadoc
        preceding = content[:start].rstrip()
        lines_before = preceding.split('\n')
        extra_start = start
        i = len(lines_before) - 1
        while i >= 0:
            stripped = lines_before[i].strip()
            if stripped.startswith('@'):
                # annotation line
                extra_start = sum(len(l) + 1 for l in lines_before[:i])
                i -= 1
            elif stripped.endswith('*/'):
                # end of Javadoc — find matching /**
                j = i
                while j >= 0:
                    if '/**' in lines_before[j]:
                        extra_start = sum(len(l) + 1 for l in lines_before[:j])
                        break
                    j -= 1
                break
            elif stripped == '':
                i -= 1
            else:
                break
        start = extra_start
        # Find the opening brace
        brace_pos = content.find('{', match.end())
        if brace_pos == -1:
            continue
        # Count braces to find method end
        depth = 1
        pos = brace_pos + 1
        while pos < len(content) and depth > 0:
            ch = content[pos]
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
            elif ch == '"':
                # skip string literal
                pos += 1
                while pos < len(content) and content[pos] != '"':
                    if content[pos] == '\\':
                        pos += 1
                    pos += 1
            elif ch == '/' and pos + 1 < len(content):
                if content[pos + 1] == '/':
                    # skip line comment
                    pos = content.index('\n', pos) if '\n' in content[pos:] else len(content)
                elif content[pos + 1] == '*':
                    end = content.find('*/', pos + 2)
                    pos = end + 1 if end != -1 else len(content)
            pos += 1
        snippets.append(content[start:pos].rstrip())

    return "\n\n".join(snippets)


def write_chunk_context(
    class_entry: dict[str, Any],
    phase: str,
    iteration: str,
    output_file: Path,
) -> None:
    """Write a task context file for a single class or chunk."""
    chunk = class_entry.get("_chunk")
    class_name = class_entry["class_name"]
    source_path = class_entry["source_path"]
    test_output = class_entry["test_output_path"]
    test_class = class_entry.get("test_class_name", class_name + "Test")

    method_section = ""
    source_section = ""
    if chunk:
        methods = chunk["method_subset"]
        methods_str = ", ".join(f"`{m}`" for m in methods)
        method_section = f"""
## 方法范围限定

本次只需为以下方法生成测试（第 {chunk['index']}/{chunk['total']} 组）：

{methods_str}

**重要**：只为上述方法生成测试，其余方法将在其他批次中处理，请勿生成。
"""
        # Extract and embed method source code so LLM doesn't need to explore the file
        extracted = extract_method_source(source_path, methods)
        if extracted:
            source_section = f"""
## 相关源码（已提取）

以下是需要测试的方法源码，无需再读取源文件：

```java
{extracted}
```
"""

    # For non-chunk entries, read the whole source if it's small enough
    if not chunk:
        try:
            src = Path(source_path).read_text(encoding="utf-8", errors="replace")
            if len(src.splitlines()) <= 500:
                source_section = f"""
## 源码

```java
{src}
```
"""
        except OSError:
            pass

    read_instruction = "1. 按照附件 SKILL.md 中的策略生成单元测试" if source_section else "1. 先读取上表中的源码文件\n2. 按照附件 SKILL.md 中的策略生成单元测试"
    step_offset = 2 if source_section else 3

    content = f"""# 当前任务上下文

**阶段**: {phase}  **迭代**: {iteration}

## 待生成测试的类

| 类名 | 源码路径 | 测试输出路径 | 测试类名 |
|------|---------|------------|---------|
| {class_name} | {source_path} | {test_output} | {test_class} |
{method_section}{source_section}
## 指令

{read_instruction}
{step_offset}. 测试类名必须是 `{test_class}`
{step_offset + 1}. 将测试文件覆盖写入对应的「测试输出路径」
"""
    output_file.write_text(content, encoding="utf-8")


def generate_single_class(
    *,
    script_dir: Path,
    split_file: str,
    phase: str,
    iteration: str,
    class_entry: dict[str, Any],
    skill_file: Path,
    model: str,
    logs_dir: Path,
) -> tuple[str, bool]:
    chunk = class_entry.get("_chunk")
    class_name = class_entry["class_name"]
    label = class_entry.get("test_class_name", class_name + "Test") if chunk else class_name
    class_ctx = logs_dir / f"task_{phase}_{label}.md"
    class_log = logs_dir / f"{phase}_{label}.log"

    # Use chunk-aware context writer
    write_chunk_context(class_entry, phase, iteration, class_ctx)

    # Check if source was embedded in context
    has_embedded_source = class_entry.get("_chunk") and "_chunk" in class_entry

    if chunk:
        methods_hint = ", ".join(chunk["method_subset"][:5])
        extra = f"（第 {chunk['index']}/{chunk['total']} 组，方法: {methods_hint}）"
        prompt = (
            f"根据附件 SKILL.md 中的测试生成策略和 task_context.md 中的任务描述，为 {class_name} 的指定方法生成 JUnit 5 测试{extra}。\n\n"
            "重要规则：\n"
            "1. 只为 task_context.md 中「方法范围限定」列出的方法生成测试\n"
            "2. 源码已在 task_context.md 的「相关源码」中提供，不要再用 Read 工具读取源文件\n"
            "3. 直接用 Write 工具将完整的测试代码写入 task_context.md 中指定的「测试输出路径」\n"
            "4. 不要只回复代码，必须实际用 Write 工具写入文件"
        )
    else:
        prompt = (
            "根据附件 SKILL.md 中的测试生成策略和 task_context.md 中的类列表，为该类生成高覆盖率 JUnit 5 测试。\n\n"
            "重要：你必须只处理 task_context.md 中唯一的这个类。"
        )
        # Only instruct to Read source if it wasn't embedded
        try:
            src_lines = Path(class_entry["source_path"]).read_text(encoding="utf-8", errors="replace").splitlines()
            if len(src_lines) <= 500:
                prompt += "\n源码已在 task_context.md 中提供，不要再用 Read 工具读取源文件。直接用 Write 工具将测试代码写入对应路径。"
            else:
                prompt += "\n先用 Read 工具读取源码，再用 Write 工具将测试代码写入对应路径。"
        except OSError:
            prompt += "\n先用 Read 工具读取源码，再用 Write 工具将测试代码写入对应路径。"
        prompt += "\n不要只回复代码，要实际写入文件。"

    args = [
        "opencode",
        "run",
        prompt,
        "-f",
        str(skill_file),
        "-f",
        str(class_ctx),
        "-m",
        model,
    ]
    print(f"  [opencode] {label} started")
    with open(class_log, "w", encoding="utf-8") as log_f:
        proc = subprocess.Popen(
            args,
            cwd=str(script_dir),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(f"    [{label}] {line}")
            sys.stdout.flush()
            log_f.write(line)
        proc.wait()
    print(f"  [opencode] {label} finished (exit={proc.returncode})")
    return label, proc.returncode == 0


def verify_chunked_files(script_dir: Path, work_units: list[dict[str, Any]]) -> bool:
    """Verify that all generated files (including chunk files) exist."""
    missing: list[str] = []
    empty: list[str] = []
    ok: list[str] = []
    for c in work_units:
        label = c.get("test_class_name", c["class_name"] + "Test")
        p = script_dir / c["test_output_path"]
        if not p.exists():
            missing.append(label)
        elif p.stat().st_size < 50:
            empty.append(label)
        else:
            ok.append(label)
    if ok:
        print(f"  ✓ generated: {', '.join(ok)}")
    if missing:
        print(f"  ✗ missing: {', '.join(missing)}")
    if empty:
        print(f"  ✗ empty: {', '.join(empty)}")
    return not missing and not empty


def generate_with_retry(
    *,
    script_dir: Path,
    split_file: str,
    phase: str,
    iteration: str,
    max_retries: int,
    parallel_jobs: int,
    test_dir: Path,
    mvn_dir: Path,
    mvn_flags: str,
    skill_file: Path,
    model: str,
) -> bool:
    logs_dir = script_dir / "results/generation_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    classes = split_classes(script_dir, split_file)

    # Expand high-complexity classes into chunks
    work_units = chunk_class_methods(classes)
    chunk_count = sum(1 for u in work_units if u.get("_chunk"))
    if chunk_count:
        print(f"  [chunk] {len(classes)} classes expanded to {len(work_units)} work units ({chunk_count} chunks)")

    if MAX_WORK_UNITS > 0 and len(work_units) > MAX_WORK_UNITS:
        print(f"  [debug] MAX_WORK_UNITS={MAX_WORK_UNITS}, truncating from {len(work_units)}")
        work_units = work_units[:MAX_WORK_UNITS]

    for attempt in range(1, max_retries + 1):
        if attempt > 1:
            print(f"  ↻ retry {attempt}...")
            clean_tests(test_dir)

        print(f"  -> generation: {len(work_units)} units, parallel={parallel_jobs}")
        failures: list[str] = []
        with ThreadPoolExecutor(max_workers=parallel_jobs) as ex:
            futures = [
                ex.submit(
                    generate_single_class,
                    script_dir=script_dir,
                    split_file=split_file,
                    phase=phase,
                    iteration=iteration,
                    class_entry=c,
                    skill_file=skill_file,
                    model=model,
                    logs_dir=logs_dir,
                )
                for c in work_units
            ]
            for fut in as_completed(futures):
                label, ok = fut.result()
                if not ok:
                    failures.append(label)

        if failures:
            print("  ✗ generation failed:")
            for name in sorted(set(failures)):
                print(f"    - {name}")
            continue

        if verify_chunked_files(script_dir, work_units):
            if check_compile(mvn_dir, mvn_flags):
                return True
            print("  ✗ compile failed, will retry...")

    print(f"  ✗ failed after {max_retries} attempts")
    return False


def run_reliability_checks(
    script_dir: Path,
    split_file: str,
    phase: str,
    iter_no: str,
    scope: str,
    reliability_k: int,
    reliability_min_pass_rate: int,
) -> None:
    reports: list[str] = []
    reliability_dir = script_dir / "results/reliability"
    reliability_dir.mkdir(parents=True, exist_ok=True)
    print(f"  [reliability] running k={reliability_k} on {scope}...")
    for run_no in range(1, reliability_k + 1):
        out = reliability_dir / f"{phase}_iter{iter_no}_run{run_no}.json"
        print(f"    - run {run_no}/{reliability_k}")
        run_cmd(
            ["bash", str(script_dir / "eval.sh"), "--subset", split_file, "--output", str(out)],
            cwd=script_dir,
        )
        reports.append(str(out))
    run_cmd(
        [
            "python3",
            "monitor.py",
            "reliability",
            "--iter",
            str(iter_no),
            "--phase",
            phase,
            "--scope",
            scope,
            "--k",
            str(reliability_k),
            "--min-pass-rate",
            str(reliability_min_pass_rate),
            "--reports",
            *reports,
        ],
        cwd=script_dir,
    )


def summarize_optimizer_result(path: Path) -> str:
    if not path.exists():
        return "optimizer_result_missing"
    data = json.loads(path.read_text(encoding="utf-8"))
    decision = data.get("decision", "unknown")
    reasons = data.get("reject_reasons", [])
    proposal = data.get("proposal", {})
    changes = proposal.get("changes", [])
    risk_level = proposal.get("risk_level", "unknown")
    expected_benefit = proposal.get("expected_benefit", "")
    step_changes = proposal.get("parameter_changes", [])

    method = proposal.get("method", "rules")
    parts = [f"decision={decision}", f"method={method}"]
    if reasons:
        parts.append("reject_reasons=" + "; ".join(reasons))
    if changes:
        parts.append("proposal_changes=" + "; ".join(changes))
    if step_changes:
        parts.append(f"step_changes={len(step_changes)}")
    parts.append(f"risk={risk_level}")
    if expected_benefit:
        parts.append("expected_benefit=" + expected_benefit)
    return " | ".join(parts)


def run_loop(max_iters: int) -> int:
    os.environ["PATH"] = "/opt/homebrew/opt/openjdk/bin:" + os.environ.get("PATH", "")

    script_dir = Path(__file__).resolve().parent
    project = load_project(script_dir)

    max_retries = env_int("MAX_RETRIES", 2)
    parallel_jobs = max(1, env_int("PARALLEL_JOBS", 2))
    split_ratio = os.getenv("SPLIT_RATIO", "0.6")
    split_seed = os.getenv("SPLIT_SEED", "42")
    train_count = os.getenv("TRAIN_COUNT", "")
    test_count = os.getenv("TEST_COUNT", "")
    reliability_k = env_int("RELIABILITY_K", 3)
    reliability_min_pass_rate = env_int("RELIABILITY_MIN_PASS_RATE", 95)

    skill_file = script_dir / ".claude/skills/java-ut-generator/SKILL.md"
    skill_pack = script_dir / "agent/skills/skill_pack.json"
    skill_renderer = script_dir / "agent/skills/render_skill.py"
    task_ctx = script_dir / "task_context.md"
    feedback_file = script_dir / "results/feedback.json"
    regression_subset_file = script_dir / "datasets/regression_subset.json"
    principles_dir = script_dir / "results/principles"
    project_name = project.get("name", "default")
    test_dir = script_dir / project["test_base"]
    mvn_dir = script_dir / project["maven_dir"]
    mvn_flags = project["maven_flags"]
    model = os.getenv("OPENCODE_MODEL", project.get("model", "deepseek/deepseek-chat"))

    # Render latest skill
    run_cmd(
        [
            "python3",
            str(skill_renderer),
            "--pack",
            str(skill_pack),
            "--output",
            str(skill_file),
        ],
        cwd=script_dir,
    )

    print("╔══════════════════════════════════════════════╗")
    print("║   Java UT Self-Improvement Loop v2           ║")
    print(f"║   model: {model}")
    print(f"║   generate mode: single-class parallel={parallel_jobs}")
    print("╚══════════════════════════════════════════════╝")
    print("")

    print("▶ preparing datasets...")
    run_cmd(["python3", "datasets/collect.py"], cwd=script_dir)
    split_cmd = ["python3", "datasets/split.py", "--seed", split_seed]
    if train_count or test_count:
        if train_count:
            split_cmd += ["--train-count", train_count]
        if test_count:
            split_cmd += ["--test-count", test_count]
        print(f"  -> split by count: train={train_count or 'auto'}, test={test_count or 'auto'}, seed={split_seed}")
    else:
        split_cmd += ["--ratio", split_ratio]
        print(f"  -> split by ratio: ratio={split_ratio}, seed={split_seed}")
    run_cmd(split_cmd, cwd=script_dir)
    refresh_regression_subset(script_dir, regression_subset_file)
    validate_regression_subset(script_dir, regression_subset_file)
    print("")

    train = json.loads((script_dir / "datasets/train.json").read_text(encoding="utf-8"))
    test = json.loads((script_dir / "datasets/test.json").read_text(encoding="utf-8"))
    train_classes = [c["class_name"] for c in train.get("classes", [])]
    test_classes = [c["class_name"] for c in test.get("classes", [])]

    init_cmd = ["python3", "monitor.py", "init", "--model", model, "--train-classes", *train_classes, "--test-classes", *test_classes]
    init_result = run_cmd(init_cmd, cwd=script_dir, capture=True)
    run_id = (init_result.stdout or "").strip()
    print(f"▶ Run ID: {run_id}")
    print("")

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  PHASE 1: TRAIN (max {max_iters} iterations)")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("")

    for i in range(1, max_iters + 1):
        print(f"┌─── Train Iteration {i} / {max_iters} ─────────────────────")
        print("")
        clean_tests(test_dir)

        print("  [1/4] generating tests...")
        count = write_task_context(script_dir, "datasets/train.json", "train", f"{i}/{max_iters}", task_ctx)
        print(f"  task_context.md written ({count} classes)")

        ok = generate_with_retry(
            script_dir=script_dir,
            split_file="datasets/train.json",
            phase="train",
            iteration=f"{i}/{max_iters}",
            max_retries=max_retries,
            parallel_jobs=parallel_jobs,
            test_dir=test_dir,
            mvn_dir=mvn_dir,
            mvn_flags=mvn_flags,
            skill_file=skill_file,
            model=model,
        )
        if not ok:
            print("  ⚠ generation failed, skip this iteration")
            print("")
            print(f"└─── Train Iteration {i} done (failed) ────────────────────")
            print("")
            continue
        print("")

        print("  [2/4] evaluating...")
        iter_cov = script_dir / f"results/train_iter{i}_coverage.json"
        run_cmd(
            ["bash", str(script_dir / "eval.sh"), "--subset", "datasets/train.json", "--output", str(iter_cov)],
            cwd=script_dir,
        )
        print("")

        run_cmd(["python3", "monitor.py", "record", "--coverage", str(iter_cov), "--iter", str(i), "--phase", "train"], cwd=script_dir)
        run_cmd(
            [
                "python3",
                "monitor.py",
                "checkpoint",
                "--action",
                "save",
                "--skill-file",
                str(skill_file),
                "--skill-pack",
                str(skill_pack),
                "--iter",
                str(i),
            ],
            cwd=script_dir,
        )
        run_reliability_checks(
            script_dir,
            "datasets/regression_subset.json",
            "train",
            str(i),
            "regression_subset",
            reliability_k,
            reliability_min_pass_rate,
        )

        if i < max_iters:
            stop = run_cmd(["python3", "monitor.py", "should-stop"], cwd=script_dir, check=False).returncode == 0
            if stop:
                print("  [4/4] monitor suggests stopping. restoring best skill...")
                run_cmd(
                    [
                        "python3",
                        "monitor.py",
                        "checkpoint",
                        "--action",
                        "restore-best",
                        "--skill-file",
                        str(skill_file),
                        "--skill-pack",
                        str(skill_pack),
                    ],
                    cwd=script_dir,
                )
                print("")
                break

            print("  [4/4] optimizing skill pack...")
            run_cmd(
                [
                    "python3",
                    "monitor.py",
                    "feedback",
                    "--coverage",
                    str(iter_cov),
                    "--output",
                    str(feedback_file),
                    "--iter",
                    str(i),
                    "--phase",
                    "train",
                ],
                cwd=script_dir,
            )
            gate_cov = script_dir / f"results/regression_iter{i}_coverage.json"
            gate_cmd = f'bash "{script_dir / "eval.sh"}" --subset datasets/regression_subset.json --output "{gate_cov}"'
            opt_result = script_dir / f"results/optimizer_iter{i}.json"
            run_cmd(
                [
                    "python3",
                    "optimizer/optimize.py",
                    "--report",
                    str(iter_cov),
                    "--feedback",
                    str(feedback_file),
                    "--skill-pack",
                    str(skill_pack),
                    "--skill-output",
                    str(skill_file),
                    "--round",
                    str(i),
                    "--gate-cmd",
                    gate_cmd,
                    "--gate-report",
                    str(gate_cov),
                    "--result",
                    str(opt_result),
                    "--model",
                    model,
                    "--principles-dir",
                    str(principles_dir),
                    "--project-name",
                    project_name,
                ],
                cwd=script_dir,
            )
            opt_changes = summarize_optimizer_result(opt_result)
            if "decision=rejected_" in opt_changes:
                print("  [4/4] gate reject, skill pack rolled back")
                print(f"        {opt_changes}")
            else:
                print("  [4/4] gate accept")
            optimizer_method = "llm" if "method=llm" in opt_changes else "rules"
            run_cmd(
                [
                    "python3",
                    "monitor.py",
                    "evolve",
                    "--iter",
                    str(i),
                    "--coverage",
                    str(iter_cov),
                    "--feedback",
                    str(feedback_file),
                    "--changes",
                    opt_changes,
                    "--optimizer-method",
                    optimizer_method,
                ],
                cwd=script_dir,
            )
            print("")
        else:
            print("  [4/4] reached max iterations")
            print("")

        print(f"└─── Train Iteration {i} done ────────────────────────────")
        print("")

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  PHASE 2: TEST (generalization check)")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("")

    clean_tests(test_dir)
    print("  [1/2] generate tests for test subset...")
    count = write_task_context(script_dir, "datasets/test.json", "test", "final", task_ctx)
    print(f"  task_context.md written ({count} classes)")
    ok = generate_with_retry(
        script_dir=script_dir,
        split_file="datasets/test.json",
        phase="test",
        iteration="final",
        max_retries=max_retries,
        parallel_jobs=parallel_jobs,
        test_dir=test_dir,
        mvn_dir=mvn_dir,
        mvn_flags=mvn_flags,
        skill_file=skill_file,
        model=model,
    )
    if not ok:
        print("  ⚠ test phase generation failed")
    print("")

    print("  [2/2] evaluate test subset...")
    test_cov = script_dir / "results/test_coverage.json"
    run_cmd(
        ["bash", str(script_dir / "eval.sh"), "--subset", "datasets/test.json", "--output", str(test_cov)],
        cwd=script_dir,
    )
    run_cmd(["python3", "monitor.py", "record", "--coverage", str(test_cov), "--iter", "0", "--phase", "test"], cwd=script_dir)
    run_reliability_checks(
        script_dir,
        "datasets/test.json",
        "test",
        "0",
        "test_subset",
        reliability_k,
        reliability_min_pass_rate,
    )
    print("")

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  final report")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    run_cmd(["python3", "monitor.py", "summary"], cwd=script_dir)
    print("")
    print("╔══════════════════════════════════════════════╗")
    print("║   self-improvement completed                 ║")
    print("║   history: python3 monitor.py summary        ║")
    print("║   skill: .claude/skills/java-ut-generator/SKILL.md  ║")
    print("╚══════════════════════════════════════════════╝")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("max_iters", nargs="?", type=int, default=5)
    args = parser.parse_args()
    try:
        return run_loop(args.max_iters)
    except subprocess.CalledProcessError as exc:
        print(f"Command failed ({exc.returncode}): {' '.join(map(str, exc.cmd))}", file=sys.stderr)
        return exc.returncode
    except Exception as exc:  # pragma: no cover - defensive
        print(f"run_loop failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
