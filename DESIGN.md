# Self-Evolving Java Unit Test Generation — Design Principles

## Problem Statement

LLM-generated unit tests often suffer from low branch coverage, compilation errors, and brittle assertions. Manually tuning prompts is tedious, and improvements on one class frequently fail to generalize to others.

This project solves these problems with a **self-evolving loop**: an LLM generates tests guided by a strategy file (SKILL), an evaluator measures coverage, and an optimizer adjusts the strategy — all automatically across multiple iterations.

---

## Core Idea: Train/Test Generalization

Borrowing from machine learning, we split the target classes into **train** and **test** subsets:

- **Train set**: drives iterative strategy improvement. The optimizer sees coverage gaps on these classes and adjusts the SKILL accordingly.
- **Test set**: held out until the final round. Validates whether the evolved strategy generalizes to unseen classes.

If the test-set coverage is close to the train-set coverage, the strategy has **generalized**. If it drops significantly, the strategy has **overfit** to the train set.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                      run_loop.py                        │
│              (orchestrator — drives the loop)            │
└──────┬──────────┬──────────┬──────────┬─────────────────┘
       │          │          │          │
       ▼          ▼          ▼          ▼
  ┌─────────┐ ┌────────┐ ┌──────────┐ ┌──────────────────┐
  │Generator│ │eval.sh │ │monitor.py│ │optimizer/        │
  │(LLM via │ │(Maven +│ │(tracking │ │ optimize.py      │
  │opencode)│ │JaCoCo) │ │& control)│ │ (LLM→rules      │
  │         │ │        │ │          │ │  fallback+gate)  │
  └────┬────┘ └───┬────┘ └────┬─────┘ └──────┬───────────┘
       │          │           │               │
       ▼          ▼           ▼               ▼
  test files  coverage    history.json    skill_pack.json
  (.java)     report      evolution_log   → render → SKILL.md
              (.json)     checkpoints     principles.json
```

### Component Responsibilities

| Component | Role |
|-----------|------|
| **run_loop.py** | Orchestrates the full pipeline: dataset prep → train iterations → test validation → summary |
| **Generator** (LLM) | Reads source code + SKILL.md, writes JUnit 5 test files. Invoked via `opencode` CLI, one class at a time in parallel |
| **eval.sh** | Runs `mvn clean test` + JaCoCo, parses XML into a unified coverage JSON contract |
| **parse_coverage.py** | Converts JaCoCo XML → structured JSON with class-level and method-level metrics |
| **monitor.py** | Multi-subcommand tool: `init`, `record`, `should-stop`, `checkpoint`, `feedback`, `reliability`, `evolve`, `summary` |
| **optimizer/optimize.py** | Proposes parameter changes to skill_pack.json (LLM-based or rule-based), applies them, runs a regression gate, accepts or rolls back, distills principles |
| **optimizer/prompts.py** | Prompt templates for the LLM optimizer (system prompt + user template) |
| **agent/skills/render_skill.py** | Renders skill_pack.json → SKILL.md (the human-readable strategy file the LLM reads) |

---

## Key Design Principles

### 1. Strategy-Data Separation

The optimizable strategy lives in a **single JSON file** (`skill_pack.json`) that is rendered into a Markdown file (`SKILL.md`) for the LLM to consume. This separation means:

- The **optimizer** works on structured data (JSON fields, numeric thresholds, rule lists)
- The **generator** reads natural-language instructions (Markdown)
- No need for the LLM to parse or produce JSON

### 2. Log-Strategy Decoupling

A previous design embedded evolution history directly inside SKILL.md. This caused the strategy file to grow unboundedly and polluted the LLM's context with irrelevant history.

The current design enforces strict separation:

| Concern | Storage |
|---------|---------|
| Current strategy (what the LLM reads) | `SKILL.md` (rendered from `skill_pack.json`) |
| Evolution history (what happened) | `results/evolution_log.json` |
| Per-iteration snapshots (rollback) | `results/checkpoints/SKILL_iter{N}.md` |
| Run metrics & trends | `results/history.json` |
| Cross-round/cross-project experience (what worked/failed) | `results/principles/{project_name}.json` |

SKILL.md is **replaced** each iteration, never appended to.

### 3. Gate-Protected Optimization

Every proposed strategy change must pass a **regression gate** before being accepted:

```
propose → apply → re-render SKILL.md → run gate evaluation → accept or rollback
```

The gate evaluates the new strategy against a fixed **regression subset** (a few train classes). Rejection triggers include:
- Branch coverage drops below tolerance
- Compilation failure rate increases
- Test pass rate drops significantly

This prevents the optimizer from making destructive changes.

### 4. LLM-as-Optimizer + Principle Bank

The optimizer supports two proposal methods:

- **LLM-based** (default): An LLM analyzes coverage gaps, feedback, evolution history, and accumulated principles to propose targeted changes to `skill_pack.json`. The LLM output is validated (schema check, no target lowering, rule count limits) with one retry on failure. If LLM fails entirely, falls back to rules.
- **Rule-based** (fallback / `--no-llm`): Hardcoded if/then rules that add hints based on coverage gaps.

After each gate decision (accept or reject), a **principle** is distilled and saved to `results/principles.json`:
- **Guiding** principles (from accepted changes) tell future rounds what worked
- **Cautionary** principles (from rejected changes) tell future rounds what to avoid
- Confidence is proportional to the branch coverage delta

Principles are stored per project (`results/principles/{project_name}.json`) but **merged across all projects** when building the LLM prompt (up to 15 most recent). This enables cross-project experience transfer — e.g., a principle learned from commons-lang3 can improve test generation on a different Java project.

### 5. Small Steps (Max 1–2 Parameter Changes Per Round)

The optimizer is constrained to change **at most 2 parameters** per iteration. This makes it possible to attribute coverage changes to specific parameter adjustments, and limits blast radius when a change is harmful.

### 6. Reliability via Repeated Evaluation

LLM-generated tests are inherently non-deterministic. A single eval run may not reflect the strategy's true quality. The system runs **k repeated evaluations** (default k=3) and computes:

- **pass@k**: at least 1 of k runs succeeds (all tests compile + pass)
- **pass^k**: all k runs succeed (strong reliability signal)

This distinguishes between strategies that are consistently good vs. occasionally lucky.

### 7. Autonomous Stopping

The monitor detects two stopping conditions:

- **Plateau**: coverage changes < 1% for 2 consecutive iterations (no more room to improve with current approach)
- **Regression**: current coverage drops > 5% below the historical best (something went wrong — restore the best checkpoint and stop)

When stopping, the system automatically restores the best-performing checkpoint.

### 8. Method-Level Chunking for High-Complexity Classes

Classes with many public methods (>20 by default) overwhelm a single LLM generation call, leading to timeouts, truncated output, or low coverage. The system automatically **chunks** such classes:

- Classes with ≤20 public methods pass through unchanged
- Classes with >20 methods are split into groups of ≤15 methods each
- Each group generates a separate test file (e.g., `StringUtilsGroup1Test.java`, `StringUtilsGroup2Test.java`)
- The LLM prompt includes a "method scope" section that explicitly lists which methods to test and instructs the LLM to ignore others

This keeps each LLM call focused and within context limits while still achieving full coverage across all methods.

---

## Data Flow Per Iteration

```
 ┌──────────────────────────────────────────────────────────┐
 │  Iteration i                                             │
 │                                                          │
 │  1. Clean test dir                                       │
 │  2. For each train class (parallel):                     │
 │     LLM reads source + SKILL.md → writes TestClass.java  │
 │  3. Compile check                                        │
 │  4. eval.sh → train_iter{i}_coverage.json                │
 │  5. monitor.py record (update history + detect signals)  │
 │  6. monitor.py checkpoint save                           │
 │  7. Reliability check (k=3 repeated evals)               │
 │  8. If last iter: stop                                   │
 │     Else:                                                │
 │     a. monitor.py feedback → method-level gaps           │
 │     b. optimizer propose (LLM or rules) → apply → gate    │
 │        → accept/reject → distill principle               │
 │     c. monitor.py evolve (log the change)                │
 └──────────────────────────────────────────────────────────┘
```

---

## Skill Pack Structure

The `skill_pack.json` is the single source of truth for the test generation strategy:

```json
{
  "version": "v3.1",
  "targets": {
    "line_pct": 90.0,
    "branch_pct": 85.0,
    "method_pct": 85.0
  },
  "generation": {
    "test_class_suffix": "Test",
    "min_cases_per_method": 3,
    "prefer_parameterized_tests": true,
    "use_display_name": true
  },
  "rules": {
    "branch_focus": ["each if/else must include true and false path tests", ...],
    "boundary_three_point": ["x <= K -> test K-1, K, K+1", ...],
    "loop_paths": [...],
    "condition_matrix": [...],
    "exception_policy": [...],
    "quality": [...]
  },
  "focus_hints": ["StringUtils.isPangram branch=91.67%"]
}
```

The optimizer modifies fields in this JSON. `render_skill.py` then converts it into the Markdown that the LLM reads.

---

## Failure Taxonomy

The monitor classifies evaluation failures into structured signals:

| Code | Meaning |
|------|---------|
| `generation_missing` | No tests were executed (files missing or invalid) |
| `compile_error` | Maven compilation failed |
| `test_failure` | Some tests failed or errored at runtime |
| `coverage_regression` | Branch coverage dropped vs. previous or best iteration |

These signals are tracked across iterations to identify recurring patterns and inform the optimizer.

---

## Two Execution Modes

| Mode | Command | Characteristics |
|------|---------|-----------------|
| **Shell loop** | `python3 run_loop.py 3` | Deterministic pipeline, parallel class generation, automated optimization via `optimizer/optimize.py` |
| **Agent skill** | `/java-ut-evolve` (in Claude Code) | Autonomous agent that reads source code, reasons about coverage gaps, updates `skill_pack.json` and re-renders |

Both modes modify `skill_pack.json` as the single source of truth, then render it to `SKILL.md`. Both use the same evaluation and monitoring infrastructure.

---

## Project Structure

```
ut_gen/
├── project.json                  # Project config (change this for new projects)
├── project/                      # Target Java project (Maven + JUnit 5 + JaCoCo)
│   ├── pom.xml
│   └── src/
├── .claude/skills/
│   ├── java-ut-generator/SKILL.md  # Generated strategy (LLM reads this)
│   └── java-ut-evolve/SKILL.md     # Agent skill definition
├── agent/skills/
│   ├── skill_pack.json             # Optimizable strategy (single source of truth)
│   └── render_skill.py             # JSON → Markdown renderer
├── datasets/
│   ├── collect.py                  # Scan source → all_classes.json
│   └── split.py                    # Stratified train/test split
├── eval.sh                         # Maven + JaCoCo evaluation
├── parse_coverage.py               # JaCoCo XML → JSON
├── monitor.py                      # Tracking, control, feedback, reliability
├── optimizer/
│   ├── optimize.py                # Propose (LLM/rules) → gate → accept/reject → distill principle
│   └── prompts.py                 # LLM optimizer prompt templates
├── run_loop.py                     # Main orchestrator
├── run_loop.sh                     # Thin shell wrapper
└── results/                        # Runtime outputs
    ├── history.json                # Run history (iterations, best, reliability)
    ├── evolution_log.json          # Evolution event log (append-only)
    ├── feedback.json               # Latest method-level gap analysis
    ├── train_iter{N}_coverage.json # Per-iteration train coverage
    ├── test_coverage.json          # Final test-set coverage
    ├── optimizer_iter{N}.json      # Optimizer decision records (includes method: llm/rules)
    ├── principles/                 # Per-project experience banks (merged at prompt time)
    │   └── {project_name}.json    # Guiding + cautionary principles for one project
    ├── checkpoints/
    │   ├── SKILL_iter{N}.md        # SKILL.md snapshots (for rollback)
    │   └── SKILL_PACK_iter{N}.json # skill_pack.json snapshots
    ├── reliability/                # Repeated eval results (k runs per iter)
    │   └── {phase}_iter{N}_run{K}.json
    └── generation_logs/            # Per-class LLM generation logs
```
