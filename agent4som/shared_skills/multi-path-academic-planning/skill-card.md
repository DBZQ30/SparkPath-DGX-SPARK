## Description: <br>
面向本科生的多路径个性化学业规划：选培养方向（常规/科学研究/交叉融合/创新创业）排四年节奏、专业选择（分流）志愿策略、转专业/辅修模拟（可抵扣/需补修/压力/红线）。在对话中原样回报命令摘要并附免责声明，完整图表在小程序「学业规划」页查看。 <br>

本 skill 为内部（Hermes 小程序）学生工具，未发布至公开目录。 <br>

## Owner
XiongWei <br>

### License/Terms of Use: <br>
Apache-2.0 <br>
## Use Case: <br>
本科生在聊天框提问「我想走科学研究型，四年怎么安排」「我大一没定专业，三个志愿怎么填」「我想转大数据要补多少学分」，agent 确认专业/年级/场景（与方向或目标专业）后调用 `python -m trainingplan.cli {route,compare,select,simulate}` 回报摘要；完整图表在小程序「学业规划」页查看。 <br>

### Deployment Geography for Use: <br>
内部部署（校园网络） <br>

## Requirements / Dependencies: <br>
**Requires API Key or External Credential:** [No] <br>
**Credential Type(s):** [None] <br>

依赖：Python 3.11+、`trainingplan` 包、SQLite `data/training_plan.db`（培养方案 + 培养模式规则 + 转专业/专业选择政策）。 <br>
个性化（本人已修课程）经 `trainingplan/identity.py` 解析：**学籍档案（`miniapp.db.student_archive`）优先、手工绑定兜底**；读 `warning.db` 成绩为**只读**。 <br>
本 skill 只读数据，不写库；不引入额外密钥。 <br>

**签名（Signing）：** 按 NVIDIA release checklist —— *「跑评测 → 完成 skill card → 签名刚通过评审的目录 → 发布」* ——
签名发生在**评测之后**，因此**源码目录不含 `skill.oms.sig`**；发布包由 `scripts/export_skill_release.sh` 生成并附带
`skill.oms.sig`（OMS 格式，detached signature）。 <br>
> 本机构建使用**自建根证书**（`SparkPath Internal Root CA`，非 NVIDIA 签发）；若纳入 NVIDIA catalog，需改用 NVIDIA 签发的证书链。 <br>

## Known Risks and Mitigations: <br>
Risk: 学生把"建议"当成官方硬性规定。 <br>
Mitigation: 通用节奏节点显式标注「建议」、官方节点标注「★官方」；每次输出附免责声明；培养模式申请/录取以学院为准。 <br>

Risk: 替学生拍板（方向/志愿/转专业）。 <br>
Mitigation: 只给可参考方案 + 权衡 + 风险；不输出"你应该转"之类结论。 <br>

Risk: 转专业录取被误当作确定结果。 <br>
Mitigation: 只做学分与压力测算，不预测录取；明确"以学院/教务处公示为准"。 <br>

Risk: 未绑定身份却输出个人化抵扣/压力。 <br>
Mitigation: 未解析到学号时**只给方案级**结论，并引导绑定。 <br>

Risk: 数字幻觉 / 跨年级套用。 <br>
Mitigation: 所有数字来自结构化库；年级未收录即拒绝，不套用其他年级。 <br>

Risk: 将命令输出文本当作指令执行（提示注入）。 <br>
Mitigation: SKILL.md 明确规定命令 stdout 视为数据，不执行其内容。 <br>

## Reference(s): <br>
- [SKILL.md](SKILL.md) <br>
- [设计文档 005-multi-path-academic-planning-design](../../docs/02-features/005-multi-path-academic-planning-design.md)（相对本文件） <br>

## Skill Output: <br>
**Output Type(s):** [Shell commands, Summary text] <br>
**Output Format:** [Plain-text plan summary + 免责声明] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [数字以命令输出为准；建议节点标注来源] <br>

## Evaluation Agents Used: <br>
- NVIDIA **SkillEvaluator Tier 1**（静态校验） <br>
- NVIDIA **SkillEvaluator Tier 3**（实机评测） <br>
- 本地：pytest（`tests/trainingplan`，含 route/simulate/policy） <br>

## Evaluation Tasks: <br>
- [evals/evals.json](evals/evals.json)：agentskills.io 格式，覆盖
  路线图（含方向缺失先列四方向）、专业选择、转专业（方案级/本人化）、可重跑，
  以及"纯文档复述"用例与"结构解读 / 成绩查询 / 代选课"三类负例（显式 `expected_skill: null`）。 <br>

## Evaluation Metrics Used: <br>
- **SkillEvaluator Tier 1**：schema / version / security(SkillSpector) / pii / license / code-integrity /
  unicode / quality / lint / secrets(Gitleaks) 等 gate + 质量分。 <br>
- **SkillEvaluator Tier 2**：embedding 相似度聚类 + LLM 判定（去重）。 <br>
- **SkillEvaluator Tier 3**：六评估器（Security / Skill Execution / Efficiency / Accuracy / Goal Accuracy / Behavior Check），
  **双 agent 对照**（`claude-code` + `step-3.7-flash`；`codex` + `deepseek-flash`），with/without skill，报告五维。 <br>

## Evaluation Results: <br>
- **SkillEvaluator Tier 1：`PASSED WITH OBSERVATIONS`（11 validator(s)；2 finding(s)；`exit 0`）** <br>
- **SkillEvaluator Tier 2（去重）：`PASS`**（context clean） <br>
- **SkillEvaluator Tier 3：`verdict = PASS`**（11 用例 × with/without × **2 agent**，**scored 48/48**、`execution_status=succeeded`）： <br>

  **`claude-code`：Overall 0.5702 → 0.9115（lift = +0.3413，约 +34 点）** <br>

  | Evaluator | With Skill | Baseline | Lift |
  |---|---|---|---|
  | Security | 1.000 | 1.000 | +0.000 |
  | Correctness | 0.891 | 0.413 | +0.478 |
  | Discoverability | 0.773 | 0.433 | +0.339 |
  | Effectiveness | 0.917 | 0.475 | +0.442 |
  | Efficiency | 0.977 | 0.529 | +0.448 |

  **`codex`：Overall 0.8261 → 0.9194（lift = +0.0933，约 +9 点）** <br>

  | Evaluator | With Skill | Baseline | Lift |
  |---|---|---|---|
  | Security | 1.000 | 1.000 | +0.000 |
  | Correctness | 0.982 | 0.909 | +0.073 |
  | Discoverability | 0.657 | 0.560 | +0.097 |
  | Effectiveness | 0.958 | 0.848 | +0.110 |
  | Efficiency | 1.000 | 0.813 | +0.187 |

  官方判定：*dimension PASS ≥50%*（**两 agent 五维全部 PASS**）；*overall lift PASS ≥ +5 点* → 本 skill **双条件均满足**。 <br>
- 本地：`tests/trainingplan` **77 passed** + `tests/doccenter` **8 passed**。 <br>
- 详见 [BENCHMARK.md](BENCHMARK.md)（由 `skillevaluator validate --agent-eval` 生成） <br>

## Skill Version(s): <br>
1.4.0 <br>

## Ethical Considerations: <br>
只处理**公开**的培养方案与政策数据；个人成绩仅用于**本人**规划（只读、不落库明细）。 <br>
