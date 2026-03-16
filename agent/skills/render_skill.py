#!/usr/bin/env python3
"""
Render SKILL.md from a single JSON skill pack.

Usage:
    python3 agent/skills/render_skill.py \
      --pack agent/skills/skill_pack.json \
      --output .claude/skills/java-ut-generator/SKILL.md
"""

import argparse
import json
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="Render SKILL.md from skill pack")
    parser.add_argument("--pack", required=True, help="Path to skill_pack.json")
    parser.add_argument("--output", required=True, help="Rendered SKILL.md path")
    return parser.parse_args()


def bullet_lines(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def render(pack: dict) -> str:
    version = pack.get("version", "v0.0")
    targets = pack.get("targets", {})
    generation = pack.get("generation", {})
    rules = pack.get("rules", {})
    focus_hints = pack.get("focus_hints", [])

    lines = [
        f"# Java Unit Test Generator — Strategy {version}",
        "",
        "> This file is generated from `agent/skills/skill_pack.json`.",
        "> Edit the JSON pack instead of editing this file manually.",
        "",
        "## Execution Steps",
        "1. Read `task_context.md` and list all target classes.",
        "2. For each class, read source code and enumerate public methods.",
        "3. Apply branch, boundary, loop, and exception rules method-by-method.",
        "4. Write tests directly to the requested `test_output_path`.",
        "",
        "## Coverage Targets",
        f"- Line: >= {targets.get('line_pct', 0):.1f}%",
        f"- Branch: >= {targets.get('branch_pct', 0):.1f}%",
        f"- Method: >= {targets.get('method_pct', 0):.1f}%",
        "",
        "## Generation Settings",
        f"- Test class suffix: `{generation.get('test_class_suffix', 'Test')}`",
        f"- Min cases per method: {generation.get('min_cases_per_method', 3)}",
        f"- Prefer parameterized tests: {str(generation.get('prefer_parameterized_tests', True)).lower()}",
        f"- Use @DisplayName: {str(generation.get('use_display_name', True)).lower()}",
        "",
        "## Core Rules",
        "### Branch Focus",
        bullet_lines(rules.get("branch_focus", [])),
        "",
        "### Boundary Three-Point",
        bullet_lines(rules.get("boundary_three_point", [])),
        "",
        "### Loop Paths",
        bullet_lines(rules.get("loop_paths", [])),
        "",
        "### Condition Matrix",
        bullet_lines(rules.get("condition_matrix", [])),
        "",
        "### Exception Policy",
        bullet_lines(rules.get("exception_policy", [])),
        "",
        "### Test Quality",
        bullet_lines(rules.get("quality", [])),
        "",
        "## Focus Hints From Optimizer",
    ]

    if focus_hints:
        lines.append(bullet_lines(focus_hints))
    else:
        lines.append("- No special focus hints in current round.")

    lines.extend(
        [
            "",
            "## Constraints",
            "- Use JUnit 5 only.",
            "- Test class name = source class + configured suffix.",
            "- Every generated test method must include real assertions.",
            "- No markdown fences or explanations in generated Java output.",
            "",
        ]
    )
    return "\n".join(lines)


def main():
    args = parse_args()
    pack = json.loads(Path(args.pack).read_text(encoding="utf-8"))
    content = render(pack)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    print(f"[render_skill] wrote {output}")


if __name__ == "__main__":
    main()
