#!/usr/bin/env python3
"""
monitor.py — 运行监控：历史追踪、趋势检测、智能决策

子命令:
    init         初始化一次新 run（返回 run_id）
    record       记录一次 iteration 结果
    should_stop  判断是否应提前停止（plateau/regression）
    checkpoint   保存/恢复最佳 SKILL.md
    summary      输出 run 历史摘要
    feedback     生成给 optimizer 的结构化反馈（方法级 gap）
"""

import json
import sys
import shutil
import argparse
from pathlib import Path
from datetime import datetime, timezone
from copy import deepcopy
from collections import Counter

HISTORY_FILE = "results/history.json"
CHECKPOINT_DIR = "results/checkpoints"
EVOLUTION_LOG = "results/evolution_log.json"


def extract_classes(cov: dict) -> dict:
    if isinstance(cov.get("classes"), dict):
        return cov["classes"]
    return {k: v for k, v in cov.items() if not str(k).startswith("_")}


def extract_total(cov: dict) -> dict:
    if isinstance(cov.get("_total"), dict):
        return cov["_total"]
    coverage = cov.get("coverage", {})
    return {
        "line": coverage.get("line", {}).get("pct", 0.0),
        "branch": coverage.get("branch", {}).get("pct", 0.0),
        "method": coverage.get("method", {}).get("pct", 0.0),
    }


def load_history():
    p = Path(HISTORY_FILE)
    if p.exists():
        return json.loads(p.read_text())
    return {"runs": []}


def save_history(data):
    Path(HISTORY_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(HISTORY_FILE).write_text(json.dumps(data, indent=2, ensure_ascii=False))


def evaluate_report_success(report: dict, min_pass_rate: float) -> bool:
    quality = report.get("quality", {})
    execution = report.get("test_execution", {})
    compile_ok = not bool(quality.get("compile_failed", False))
    tests_run = int(execution.get("tests_run", 0) or 0)
    pass_rate = float(execution.get("pass_rate_pct", 0.0) or 0.0)
    return compile_ok and tests_run > 0 and pass_rate >= min_pass_rate


def to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def to_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def classify_failure_signals(
    report: dict,
    previous_branch: float = None,
    best_branch: float = None,
    branch_drop_tolerance: float = 0.2,
) -> list:
    """对单次评测结果做失败分类，输出结构化信号。"""
    execution = report.get("test_execution", {})
    quality = report.get("quality", {})
    total = extract_total(report)

    tests_run = to_int(execution.get("tests_run", 0))
    tests_failed = to_int(execution.get("tests_failed", 0))
    tests_error = to_int(execution.get("tests_error", 0))
    pass_rate = to_float(execution.get("pass_rate_pct", 0.0))
    compile_failed = bool(quality.get("compile_failed", False))
    branch_now = to_float(total.get("branch", 0.0))

    signals = []
    if tests_run == 0:
        signals.append({
            "code": "generation_missing",
            "pattern": "generation_missing:no_tests_executed",
            "reason": "No tests were executed; likely missing or invalid generated tests.",
            "evidence": {"tests_run": tests_run},
        })

    if compile_failed:
        signals.append({
            "code": "compile_error",
            "pattern": "compile_error:maven_test_or_compile_failed",
            "reason": "Maven test/compile stage failed.",
            "evidence": {
                "maven_test_exit_code": quality.get("maven_test_exit_code"),
                "jacoco_report_exit_code": quality.get("jacoco_report_exit_code"),
            },
        })

    if tests_run > 0 and (tests_failed + tests_error) > 0:
        signals.append({
            "code": "test_failure",
            "pattern": "test_failure:failed_or_error_tests_present",
            "reason": "Some tests failed or errored during execution.",
            "evidence": {
                "tests_failed": tests_failed,
                "tests_error": tests_error,
                "pass_rate_pct": pass_rate,
            },
        })

    if previous_branch is not None and (to_float(previous_branch) - branch_now) > branch_drop_tolerance:
        signals.append({
            "code": "coverage_regression",
            "pattern": "coverage_regression:branch_drop_vs_previous_iter",
            "reason": "Branch coverage regressed versus the previous iteration.",
            "evidence": {
                "previous_branch": round(to_float(previous_branch), 2),
                "current_branch": round(branch_now, 2),
                "drop": round(to_float(previous_branch) - branch_now, 2),
            },
        })
    elif best_branch is not None and to_float(best_branch) > 0 and (to_float(best_branch) - branch_now) > 5.0:
        signals.append({
            "code": "coverage_regression",
            "pattern": "coverage_regression:branch_drop_vs_best_iter",
            "reason": "Branch coverage regressed significantly versus historical best.",
            "evidence": {
                "best_branch": round(to_float(best_branch), 2),
                "current_branch": round(branch_now, 2),
                "drop": round(to_float(best_branch) - branch_now, 2),
            },
        })

    return signals


def append_failure_trace(run: dict, iter_no: int, phase: str, failure_signals: list):
    trace = run.get("failure_trace", [])
    for sig in failure_signals:
        trace.append({
            "iter": iter_no,
            "phase": phase,
            "code": sig.get("code", "unknown"),
            "pattern": sig.get("pattern", "unknown"),
            "reason": sig.get("reason", ""),
            "evidence": sig.get("evidence", {}),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    # 控制体积，保留最近 200 条
    run["failure_trace"] = trace[-200:]


def build_top_failure_patterns(run: dict, limit: int = 5) -> list:
    trace = run.get("failure_trace", [])
    if not trace:
        return []

    counter = Counter()
    last_seen = {}
    for item in trace:
        pattern = item.get("pattern") or "unknown"
        counter[pattern] += 1
        last_seen[pattern] = {"iter": item.get("iter"), "phase": item.get("phase")}

    out = []
    for pattern, count in counter.most_common(limit):
        out.append({
            "pattern": pattern,
            "count": count,
            "last_iter": last_seen[pattern].get("iter"),
            "last_phase": last_seen[pattern].get("phase"),
        })
    return out


def cmd_init(args):
    """开始一次新 run"""
    h = load_history()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    h["runs"].append({
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model or "unknown",
        "train_classes": args.train_classes or [],
        "test_classes": args.test_classes or [],
        "iterations": [],
        "test_result": None,
        "best_iter": None,
        "best_branch": 0,
        "reliability": [],
    })
    save_history(h)
    print(run_id)


def cmd_record(args):
    """记录一次 iteration 的覆盖率"""
    h = load_history()
    run = h["runs"][-1]

    with open(args.coverage) as f:
        cov = json.load(f)

    total = extract_total(cov)
    branch = total.get("branch", 0)
    previous_branch = None
    if args.phase == "train" and run.get("iterations"):
        previous_branch = run["iterations"][-1].get("branch_total")

    failure_signals = classify_failure_signals(
        cov,
        previous_branch=previous_branch if args.phase == "train" else None,
        best_branch=run.get("best_branch") if args.phase == "train" else None,
    )

    entry = {
        "iter": args.iter,
        "phase": args.phase,
        "coverage": cov,
        "branch_total": branch,
        "failure_signals": failure_signals,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if args.phase == "train":
        run["iterations"].append(entry)
        # 更新 best
        if branch > run.get("best_branch", 0):
            run["best_iter"] = args.iter
            run["best_branch"] = branch
    elif args.phase == "test":
        run["test_result"] = entry

    if failure_signals:
        append_failure_trace(run, args.iter, args.phase, failure_signals)

    save_history(h)
    print(f"  [monitor] recorded {args.phase} iter={args.iter} branch={branch:.1f}%")
    if failure_signals:
        codes = ", ".join(sorted({sig.get("code", "unknown") for sig in failure_signals}))
        print(f"  [monitor] failure taxonomy: {codes}")
        patterns = build_top_failure_patterns(run, limit=3)
        if patterns:
            formatted = ", ".join(f"{p['pattern']} x{p['count']}" for p in patterns)
            print(f"  [monitor] top failure patterns: {formatted}")


def cmd_should_stop(args):
    """判断是否应提前停止。返回 exit code: 0=停止, 1=继续"""
    h = load_history()
    run = h["runs"][-1]
    iters = run["iterations"]

    if len(iters) < 2:
        sys.exit(1)  # 不够 2 轮，继续

    recent = [it["branch_total"] for it in iters[-3:]]  # 最近3轮

    # 规则1: plateau — 连续 2 轮覆盖率变化 < 1%（但 0% 不算 plateau，那是生成失败）
    if len(recent) >= 2:
        deltas = [abs(recent[i] - recent[i-1]) for i in range(1, len(recent))]
        if all(d < 1.0 for d in deltas) and recent[-1] > 10:
            print(f"  [monitor] PLATEAU detected: recent branch = {recent}, stopping early")
            sys.exit(0)

    # 规则2: 严重回退 — 当前比 best 低 5%+
    best = run.get("best_branch", 0)
    current = iters[-1]["branch_total"]
    if best - current > 5:
        print(f"  [monitor] REGRESSION: current={current:.1f}% vs best={best:.1f}%, stopping")
        sys.exit(0)

    sys.exit(1)  # 继续


def cmd_checkpoint(args):
    """保存或恢复最佳 SKILL.md"""
    Path(CHECKPOINT_DIR).mkdir(parents=True, exist_ok=True)
    skill = Path(args.skill_file)
    skill_pack = Path(args.skill_pack) if args.skill_pack else None

    if args.action == "save":
        dst = Path(CHECKPOINT_DIR) / f"SKILL_iter{args.iter}.md"
        shutil.copy2(skill, dst)
        print(f"  [monitor] checkpoint saved: {dst}")
        if skill_pack and skill_pack.exists():
            pack_dst = Path(CHECKPOINT_DIR) / f"SKILL_PACK_iter{args.iter}.json"
            shutil.copy2(skill_pack, pack_dst)
            print(f"  [monitor] checkpoint saved: {pack_dst}")

    elif args.action == "restore-best":
        h = load_history()
        run = h["runs"][-1]
        best_iter = run.get("best_iter")
        if best_iter is None:
            print("  [monitor] no best checkpoint found")
            sys.exit(1)
        src = Path(CHECKPOINT_DIR) / f"SKILL_iter{best_iter}.md"
        if src.exists():
            shutil.copy2(src, skill)
            print(f"  [monitor] restored SKILL from best iter {best_iter}")
            if skill_pack:
                pack_src = Path(CHECKPOINT_DIR) / f"SKILL_PACK_iter{best_iter}.json"
                if pack_src.exists():
                    shutil.copy2(pack_src, skill_pack)
                    print(f"  [monitor] restored SKILL_PACK from best iter {best_iter}")
        else:
            print(f"  [monitor] checkpoint {src} not found")
            sys.exit(1)


def cmd_summary(args):
    """输出 run 历史摘要"""
    h = load_history()
    for run in h["runs"]:
        print(f"\n  Run: {run['run_id']}  model={run.get('model','?')}")
        for it in run.get("iterations", []):
            b = it["branch_total"]
            marker = " ★" if it["iter"] == run.get("best_iter") else ""
            print(f"    train iter {it['iter']}: branch={b:5.1f}%{marker}")
        tr = run.get("test_result")
        if tr:
            print(f"    test:        branch={tr['branch_total']:5.1f}%")
        best = run.get("best_branch", 0)
        test_b = tr["branch_total"] if tr else 0
        if best > 0 and test_b > 0:
            delta = test_b - best
            label = "✓ generalized" if delta >= -5 else "✗ overfit"
            print(f"    → {label} (delta={delta:+.1f}%)")

        rel_entries = run.get("reliability", [])
        if rel_entries:
            print("    reliability:")
            rel_entries = sorted(rel_entries, key=lambda x: (x.get("phase", ""), x.get("iter", 0), x.get("scope", "")))
            for rel in rel_entries:
                phase = rel.get("phase", "?")
                iter_no = rel.get("iter", "?")
                scope = rel.get("scope", "default")
                k = rel.get("k", 0)
                success = rel.get("success_runs", 0)
                pass_at_k = "1" if rel.get("pass_at_k", False) else "0"
                pass_pow_k = "1" if rel.get("pass_pow_k", False) else "0"
                print(
                    "      "
                    f"{phase} iter {iter_no} ({scope}, k={k}): "
                    f"pass@k={pass_at_k}, pass^k={pass_pow_k}, success={success}/{k}"
                )

        top_patterns = build_top_failure_patterns(run, limit=5)
        if top_patterns:
            print("    top failure patterns:")
            for p in top_patterns:
                print(
                    "      "
                    f"{p['pattern']} x{p['count']} "
                    f"(last={p.get('last_phase','?')} iter {p.get('last_iter','?')})"
                )


def cmd_reliability(args):
    """记录同一策略 k 次重复评测的稳定性指标"""
    h = load_history()
    run = h["runs"][-1]
    reports = args.reports or []
    k = len(reports)
    if k == 0:
        print("  [monitor] no reports provided for reliability")
        sys.exit(1)

    if args.k and args.k != k:
        print(f"  [monitor] --k={args.k} but got {k} reports; using report count as k")

    success_runs = 0
    compile_fail_runs = 0
    branch_values = []
    pass_rates = []
    for report_file in reports:
        with open(report_file) as f:
            report = json.load(f)
        if evaluate_report_success(report, args.min_pass_rate):
            success_runs += 1
        if report.get("quality", {}).get("compile_failed", False):
            compile_fail_runs += 1
        pass_rates.append(float(report.get("test_execution", {}).get("pass_rate_pct", 0.0) or 0.0))
        branch_values.append(float(extract_total(report).get("branch", 0.0) or 0.0))

    entry = {
        "iter": args.iter,
        "phase": args.phase,
        "scope": args.scope,
        "k": k,
        "min_pass_rate": args.min_pass_rate,
        "success_runs": success_runs,
        "compile_fail_runs": compile_fail_runs,
        "pass_at_k": success_runs >= 1,
        "pass_pow_k": success_runs == k,
        "pass_rates": pass_rates,
        "branch_values": branch_values,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    current = run.get("reliability", [])
    replaced = False
    for idx, old in enumerate(current):
        if old.get("iter") == args.iter and old.get("phase") == args.phase and old.get("scope") == args.scope:
            current[idx] = entry
            replaced = True
            break
    if not replaced:
        current.append(entry)
    run["reliability"] = current

    save_history(h)
    pass_at_k = "1" if entry["pass_at_k"] else "0"
    pass_pow_k = "1" if entry["pass_pow_k"] else "0"
    print(
        f"  [monitor] reliability {args.phase} iter={args.iter} scope={args.scope}: "
        f"pass@k={pass_at_k} pass^k={pass_pow_k} success={success_runs}/{k}"
    )


def cmd_feedback(args):
    """生成结构化优化反馈（方法级 gap 分析）"""
    with open(args.coverage) as f:
        cov = json.load(f)

    classes = extract_classes(cov)
    gaps = []
    for cls_name, cls_data in classes.items():
        methods = cls_data.get("methods", {})
        for mname, mcov in methods.items():
            if mcov.get("branch", 100) < 100 or mcov.get("line", 100) < 100:
                gaps.append({
                    "class": cls_name,
                    "method": mname,
                    "line": mcov.get("line", 0),
                    "branch": mcov.get("branch", 0),
                })

    # 按 branch 升序排序（最差的排前面）
    gaps.sort(key=lambda g: g["branch"])

    failure_signals = classify_failure_signals(cov)
    top_failure_patterns = []
    h = load_history()
    if h.get("runs"):
        run = h["runs"][-1]
        top_failure_patterns = build_top_failure_patterns(run, limit=args.top_k_patterns)
        if args.phase == "train":
            target = next(
                (it for it in run.get("iterations", []) if it.get("iter") == args.iter and it.get("phase") == "train"),
                None,
            )
            if target and target.get("failure_signals"):
                failure_signals = target["failure_signals"]
        elif args.phase == "test":
            target = run.get("test_result")
            if target and target.get("iter") == args.iter and target.get("failure_signals"):
                failure_signals = target["failure_signals"]

    failure_counts = dict(Counter(sig.get("code", "unknown") for sig in failure_signals))

    feedback = {
        "total_gaps": len(gaps),
        "class_summary": {
            cls: {"line": d["line"], "branch": d["branch"], "method": d["method"]}
            for cls, d in classes.items()
        },
        "method_gaps": gaps[:20],  # top 20 worst methods
        "failure_taxonomy": {
            "signals": failure_signals,
            "counts": failure_counts,
            "top_patterns": top_failure_patterns,
        },
    }

    output = args.output or "/dev/stdout"
    with open(output, "w") as f:
        json.dump(feedback, f, indent=2, ensure_ascii=False)

    print(f"  [monitor] {len(gaps)} method-level gaps found")
    for g in gaps[:5]:
        print(f"    {g['class']}.{g['method']}: line={g['line']:.0f}% branch={g['branch']:.0f}%")
    if failure_counts:
        print(f"  [monitor] failure categories: {failure_counts}")
    if top_failure_patterns:
        for p in top_failure_patterns[:3]:
            print(f"    top pattern: {p['pattern']} x{p['count']}")


def load_evolution_log():
    p = Path(EVOLUTION_LOG)
    if p.exists():
        return json.loads(p.read_text())
    return {"entries": []}


def save_evolution_log(data):
    Path(EVOLUTION_LOG).parent.mkdir(parents=True, exist_ok=True)
    Path(EVOLUTION_LOG).write_text(json.dumps(data, indent=2, ensure_ascii=False))


def cmd_evolve(args):
    """记录一次 SKILL.md 进化事件"""
    log = load_evolution_log()

    # 读取 feedback.json 获取 gap 摘要
    gaps_summary = []
    if args.feedback and Path(args.feedback).exists():
        with open(args.feedback) as f:
            fb = json.load(f)
        gaps_summary = [
            f"{g['class']}.{g['method']} (branch={g['branch']}%)"
            for g in fb.get("method_gaps", [])[:10]
        ]

    # 读取覆盖率
    coverage_summary = {}
    if args.coverage and Path(args.coverage).exists():
        with open(args.coverage) as f:
            cov = json.load(f)
        coverage_summary = extract_total(cov)

    entry = {
        "iter": args.iter,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "coverage_before": coverage_summary,
        "top_gaps": gaps_summary,
        "changes_made": args.changes or "pending",
        "optimizer_method": getattr(args, "optimizer_method", None),
    }
    log["entries"].append(entry)
    save_evolution_log(log)
    print(f"  [monitor] evolution entry #{len(log['entries'])} recorded")


def cmd_evolve_context(args):
    """为 optimizer 生成精简上下文（最近 N 轮进化摘要 + 当前 gaps + principles）"""
    log = load_evolution_log()
    recent = log["entries"][-5:]  # 最近 5 轮

    # 读取 principles（合并所有项目）
    principles_text = ""
    principles_dir = Path("results/principles")
    if principles_dir.is_dir():
        all_principles = []
        for f in sorted(principles_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text())
                for p in data.get("principles", []):
                    p.setdefault("project", f.stem)
                    all_principles.append(p)
            except (json.JSONDecodeError, OSError):
                continue
        all_principles.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        recent = all_principles[:10]
        if recent:
            prin_lines = ["经验原则（从历史实验蒸馏，跨项目）："]
            for p in recent:
                ptype = p.get("type", "?")
                project = p.get("project", "?")
                summary = p.get("change_summary", "?")
                delta = p.get("coverage_delta", {})
                prin_lines.append(f"  - [{ptype}] ({project}) {summary} (branch_delta={delta.get('branch', 0):+.1f}%)")
            principles_text = "\n".join(prin_lines)

    # 读取当前 feedback
    gaps_text = ""
    failure_text = ""
    if args.feedback and Path(args.feedback).exists() and Path(args.feedback).stat().st_size > 2:
        with open(args.feedback) as f:
            fb = json.load(f)
        method_gaps = fb.get("method_gaps", [])
        if method_gaps:
            lines = ["当前未满覆盖的方法（按 branch 升序）："]
            for g in method_gaps[:15]:
                lines.append(f"  - {g['class']}.{g['method']}: line={g['line']}% branch={g['branch']}%")
            gaps_text = "\n".join(lines)
        taxonomy = fb.get("failure_taxonomy", {})
        tax_lines = []
        counts = taxonomy.get("counts", {})
        if counts:
            tax_lines.append(f"失败分类计数: {counts}")
        for sig in taxonomy.get("signals", [])[:5]:
            tax_lines.append(f"  - [{sig.get('code', 'unknown')}] {sig.get('reason', '')}")
        top_patterns = taxonomy.get("top_patterns", [])
        if top_patterns:
            tax_lines.append("近期 top failure patterns:")
            for p in top_patterns[:5]:
                tax_lines.append(f"  - {p.get('pattern')} x{p.get('count')}")
        if tax_lines:
            failure_text = "\n".join(tax_lines)

    # 构建进化摘要
    history_text = ""
    if recent:
        lines = ["最近迭代摘要："]
        for e in recent:
            cov = e.get("coverage_before", {})
            b = cov.get("branch", "?")
            changes = e.get("changes_made", "?")
            lines.append(f"  iter {e['iter']}: branch={b}%, 改动={changes}")
        history_text = "\n".join(lines)

    output = args.output or "/dev/stdout"
    context = f"""## 进化上下文

{history_text}

{gaps_text}

{failure_text}

{principles_text}

## 优化指令

请**替换式更新** SKILL.md（不是追加日志）：
1. 如果某条规则已被证明无效或可以改进，直接修改它
2. 如果需要新规则，加到合适的位置
3. 保持 SKILL.md < 80 行，精炼不冗余
4. 只更新顶部版本号（v2.X → v2.{args.next_version}）
"""
    with open(output, "w") as f:
        f.write(context)
    print(f"  [monitor] evolve-context written to {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("init")
    p.add_argument("--model", default="")
    p.add_argument("--train-classes", nargs="*", default=[])
    p.add_argument("--test-classes", nargs="*", default=[])

    p = sub.add_parser("record")
    p.add_argument("--coverage", required=True)
    p.add_argument("--iter", type=int, required=True)
    p.add_argument("--phase", choices=["train", "test"], required=True)

    p = sub.add_parser("should-stop")

    p = sub.add_parser("checkpoint")
    p.add_argument("--action", choices=["save", "restore-best"], required=True)
    p.add_argument("--skill-file", required=True)
    p.add_argument("--skill-pack", default="")
    p.add_argument("--iter", type=int, default=0)

    p = sub.add_parser("summary")

    p = sub.add_parser("reliability")
    p.add_argument("--iter", type=int, required=True)
    p.add_argument("--phase", choices=["train", "test"], required=True)
    p.add_argument("--scope", default="default")
    p.add_argument("--k", type=int, default=0)
    p.add_argument("--min-pass-rate", type=float, default=95.0)
    p.add_argument("--reports", nargs="+", required=True)

    p = sub.add_parser("feedback")
    p.add_argument("--coverage", required=True)
    p.add_argument("--output", default=None)
    p.add_argument("--iter", type=int, default=0)
    p.add_argument("--phase", choices=["train", "test"], default="train")
    p.add_argument("--top-k-patterns", type=int, default=5)

    p = sub.add_parser("evolve")
    p.add_argument("--iter", type=int, required=True)
    p.add_argument("--coverage", default=None)
    p.add_argument("--feedback", default=None)
    p.add_argument("--changes", default=None)
    p.add_argument("--optimizer-method", default=None, help="llm or rules")

    p = sub.add_parser("evolve-context")
    p.add_argument("--feedback", default=None)
    p.add_argument("--next-version", type=int, default=1)
    p.add_argument("--output", default=None)

    args = parser.parse_args()
    if args.cmd == "init":       cmd_init(args)
    elif args.cmd == "record":   cmd_record(args)
    elif args.cmd == "should-stop": cmd_should_stop(args)
    elif args.cmd == "checkpoint":  cmd_checkpoint(args)
    elif args.cmd == "summary":  cmd_summary(args)
    elif args.cmd == "reliability": cmd_reliability(args)
    elif args.cmd == "feedback": cmd_feedback(args)
    elif args.cmd == "evolve":   cmd_evolve(args)
    elif args.cmd == "evolve-context": cmd_evolve_context(args)
    else: parser.print_help()
