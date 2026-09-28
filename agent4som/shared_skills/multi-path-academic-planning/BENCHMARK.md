# Skill Benchmark: multi-path-academic-planning

> ✅ **Overall verdict: PASS — Recommended for publication**

## Publication Recommendation

Recommended for publication based on the completed evaluation evidence in this report.

## Evaluation Metadata

- Skill: `multi-path-academic-planning`
- Evaluation date: 2026-09-27
- Evaluator version: `0.3.0`
- Evaluated source: `DBZQ30/SparkPath-DGX-SPARK`
- Evaluated source revision: `73fa2372cadbcd051d7c79c002024eb5280ea8b0`
- Evaluator container revision: not recorded (not supplied by the orchestration input)
- Agents: Claude Code (`step-3.7-flash`), Codex (`deepseek-flash`)
- Tasks: 11 evaluation tasks (8 positive, 3 negative)
- Dataset digest: `sha256:d0ae75fc7d3ca5a7622d1e61a3abd08cf3a9dfbf35c71838b583b48057d094c8` (skill-evaluator-dataset-snapshot/1)
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
| Overall | 60% → 93% (+33 points) | 78% → 93% (+15 points) |
| Security | 100% → 100% (±0 points) | 100% → 100% (±0 points) |
| Correctness | 50% → 91% (+41 points) | 77% → 100% (+23 points) |
| Discoverability | 49% → 89% (+40 points) | 62% → 67% (+5 points) |
| Effectiveness | 48% → 88% (+40 points) | 76% → 96% (+20 points) |
| Efficiency | 53% → 98% (+44 points) | 75% → 100% (+25 points) |

**How to read this table:** baseline is the same task attempted without the target skill. Uplift is `skill score - baseline score`, shown in percentage points.

Example: `47% → 92% (+45 points)` means the skill-assisted run scored 92%, 45 percentage points above its 47% no-skill baseline.

## Tier Status

| Tier | Purpose | Status | Evidence |
|---|---|---|---|
| Tier 1 | Static validation | **PASSED WITH OBSERVATIONS** | 11 validator(s); 2 finding(s) |
| Tier 2 | Semantic deduplication | **PASSED** | 1 validator(s); 0 finding(s) |
| Tier 3 | Live agent evaluation | **PASS** | 2 agent(s); 11 task(s) |

Test execution limitations:

- No standard Python test-file candidates found; target tests were not executed and coverage was not measured. Consider adding tests.

## Findings and Observations

<details>
<summary>Show detailed findings and successful checks</summary>

- **MEDIUM** SCHEMA/folder\_hierarchy: Skill not in standard location (skills/ or team-skills/) (`multi-path-academic-planning`)
- **LOW** SCHEMA/unexpected\_file: Unexpected 'CHANGELOG.md' in skill root (`CHANGELOG.md`)

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
