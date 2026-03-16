"""Prompt templates for the LLM-based optimizer."""

OPTIMIZER_SYSTEM_PROMPT = """\
You are a unit-test strategy optimizer. Your job is to improve a JSON skill pack \
that guides an LLM-based Java unit test generator.

## Output format

1. Output the **complete** updated skill_pack.json inside a ```json fenced block.
2. After the JSON block, add a `## Reasoning` section explaining what you changed and why.

## Constraints

- Change at most {max_changes} rule categories per round.
- You MUST NOT lower any value in "targets" (line_pct, branch_pct, method_pct).
- Every rule must be a concrete, actionable instruction that a test generator can follow.
- Each rule category list must have at most 15 items (to prevent bloat).
- Keep "version" as "v3.{round_num}".
- Preserve the JSON schema exactly: top-level keys are "version", "targets", "generation", "rules", "focus_hints".
- "rules" sub-keys: branch_focus, boundary_three_point, loop_paths, condition_matrix, exception_policy, quality.
"""

OPTIMIZER_USER_TEMPLATE = """\
## Current skill_pack.json

```json
{skill_pack_json}
```

## Coverage feedback (this round)

```json
{feedback_json}
```

## Recent evolution history (last {history_count} rounds)

{evolution_history}

## Accumulated principles (from past experiments)

{principles_text}

## Task

Analyze the coverage gaps and failure patterns above. Propose targeted changes \
to the skill pack that will improve branch and line coverage in the next iteration.

Focus on the weakest methods and most frequent failure patterns. \
Prefer precise, surgical rule changes over broad additions.
"""
