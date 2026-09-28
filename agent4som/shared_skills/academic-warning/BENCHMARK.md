# Skill Benchmark: academic-warning

> ✅ **Overall verdict: PASS — Recommended for publication**

## Publication Recommendation

Recommended for publication based on the completed evaluation evidence in this report.

## Evaluation Metadata

- Skill: `academic-warning`
- Evaluation date: 2026-09-28
- Evaluator version: `0.3.0`
- Evaluated source: `DBZQ30/SparkPath-DGX-SPARK`
- Evaluated source revision: `85746b6f99c0252700162bdf00ebddc7f8c10f55`
- Evaluator container revision: not recorded (not supplied by the orchestration input)
- Agents: Claude Code (`step-3.7-flash`), Codex (`deepseek-flash`)
- Tasks: 17 evaluation tasks (14 positive, 3 negative)
- Dataset digest: `sha256:06dae522b8ec843d475e180350bde9656249f9f3fe17374ca37ec358c5dcb8bd` (skill-evaluator-dataset-snapshot/1)
- Attempts per task: 2
- Environment: `local`
- Tier 3 evidence: required for publication

Tasks ran on the trusted local host; local mode is not sandboxed.

## What This Report Answers

The three-tier evaluation checks whether the skill:

- is safe to use;
- produces correct answers;
- is discovered and activated when needed;
- helps the agent complete the user's goal and expected workflow; and
- avoids wasted skill and tool usage.

## Results at a Glance

| Measure | Claude Code (Baseline → Skill Uplift) | Codex (Baseline → Skill Uplift) |
|---|---:|---:|
| Overall | 66% → 88% (+22 points) | 83% → 93% (+10 points) |
| Security | 100% → 100% (±0 points) | 82% → 94% (+12 points) |
| Correctness | 66% → 90% (+24 points) | 100% → 100% (±0 points) |
| Discoverability | 52% → 78% (+26 points) | 57% → 75% (+17 points) |
| Effectiveness | 55% → 84% (+29 points) | 95% → 96% (+1 points) |
| Efficiency | 57% → 89% (+32 points) | 79% → 100% (+21 points) |

**How to read this table:** baseline is the same task attempted without the target skill. Uplift is `skill score - baseline score`, shown in percentage points.

Example: `47% → 92% (+45 points)` means the skill-assisted run scored 92%, 45 percentage points above its 47% no-skill baseline.

## Tier Status

| Tier | Purpose | Status | Evidence |
|---|---|---|---|
| Tier 1 | Static validation | **PASSED WITH OBSERVATIONS** | 11 validator(s); 4 finding(s) |
| Tier 2 | Semantic deduplication | **PASSED** | 1 validator(s); 0 finding(s) |
| Tier 3 | Live agent evaluation | **PASS** | 2 agent(s); 17 task(s) |

Test execution limitations:

- No standard Python test-file candidates found; target tests were not executed and coverage was not measured. Consider adding tests.

## Findings and Observations

<details>
<summary>Show detailed findings and successful checks</summary>

- **MEDIUM** PII/phone\_numbers: International phone number: +0.2846 (`CHANGELOG.md:47`)
- **MEDIUM** SCHEMA/folder\_hierarchy: Skill not in standard location (skills/ or team-skills/) (`academic-warning`)
- **LOW** SCHEMA/unexpected\_file: Unexpected 'CHANGELOG.md' in skill root (`CHANGELOG.md`)
- **LOW** UNICODE/isolated\_invisible\_char: Isolated invisible character(s) (1): VARIATION SELECTOR-16 (`CHANGELOG.md:22`)

</details>

## Scoring Methodology

<details>
<summary>Show dimension definitions, source signals, and thresholds</summary>

| Dimension | Question | Scored signals |
|---|---|---|
| Security | Is it safe to use? | `security` (100%) |
| Correctness | Is the answer correct? | `accuracy` (100%) |
| Discoverability | Was the right skill loaded when needed? | `skill_execution` (100%) |
| Effectiveness | Did the skill help complete the task? | `goal_accuracy` (50%) + `behavior_check` (50%) |
| Efficiency | Did it avoid wasted tool or skill usage? | `skill_efficiency` (100%) |

- Dimension bands: PASS at 50% or above; NEUTRAL from 40% to below 50%; FAIL below 40%.
- Overall Tier 3 lift: PASS at +5 points or more; FAIL at -10 points or less; values between those bands are NEUTRAL.
- Overall verdict: PASS only when every configured dimension passes for at least one supported agent. Lift is reported as diagnostic evidence and does not override this gate.
- The 50% attempt pass threshold is a separate per-task gate; it is not the dimension pass threshold.
- Effectiveness is the equal-weight mean of goal completion (`goal_accuracy`) and expected workflow adherence (`behavior_check`).
- Token efficiency is a separate report-only signal. It does not change a dimension score or the overall verdict.

Signals present in this run:

- `security` (Security): unsafe operations, secret leakage, and unauthorized access.
- `skill\_execution` (Skill Execution): whether the expected skill was found and executed.
- `skill\_efficiency` (Efficiency): routing quality, workspace-aware skill reads, and productive tool use.
- `accuracy` (Accuracy): final-answer correctness against the reference answer.
- `goal\_accuracy` (Goal Accuracy): whether the user's goal was achieved.
- `behavior\_check` (Behavior Check): whether the expected workflow behavior was followed.

</details>

## Freshness

Regenerate this benchmark when the skill, evaluation dataset, target agent/model, evaluator version, environment, or scoring policy changes.
