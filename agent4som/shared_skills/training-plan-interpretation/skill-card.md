## Description: <br>
面向本科新生（及低年级学生）的培养方案智能解读：把培养方案转成四年学分结构、课程先后修关系、毕业与授学位硬条件，在对话中原样回报四段摘要，并引导到小程序「培养方案解读」页查看图表。 <br>

本 skill 为内部（Hermes 小程序）学生工具，未发布至公开目录。 <br>

## Owner
XiongWei <br>

### License/Terms of Use: <br>
Apache-2.0 <br>
## Use Case: <br>
本科生在聊天框提问「我是xx专业新生，帮我解读一下我们专业的培养方案」，agent 确认专业与年级后调用 `python -m trainingplan.cli interpret` 回报四段摘要；完整图表在小程序「培养方案解读」页查看。 <br>

### Deployment Geography for Use: <br>
内部部署（校园网络） <br>

## Requirements / Dependencies: <br>
**Requires API Key or External Credential:** [No] <br>
**Credential Type(s):** [None] <br>

依赖：Python 3.11+、`trainingplan` 包、SQLite `data/training_plan.db`（本功能自建，仅含培养方案公开数据）。 <br>
先修关系抽取使用本机 VL 模型（qwen3.8-27b）；PDF 通道经 acc-svr MinerU 隧道（`MINERU_URL`）。 <br>
本 skill 只读方案数据，不涉及学生个人信息，不引入额外密钥。 <br>

**签名（Signing）：** 按 NVIDIA release checklist —— *「跑评测 → 完成 skill card → 签名刚通过评审的目录 → 发布」* ——
签名发生在**评测之后**，因此**源码目录不含 `skill.oms.sig`**；发布包由 `scripts/export_skill_release.sh` 生成并附带
`skill.oms.sig`（OMS 格式，detached signature）。 <br>
> 本机构建使用**自建根证书**（`SparkPath Internal Root CA`，非 NVIDIA 签发）；若纳入 NVIDIA catalog，需改用 NVIDIA 签发的证书链。 <br>

## Known Risks and Mitigations: <br>
Risk: 学生被引导到错误专业/年级的方案。 <br>
Mitigation: 专业未确认前不解读；年级缺失显式询问；目标年级未收录即拒绝，**不套用其他年级**。 <br>

Risk: 先修关系自动抽取出错，误导排课。 <br>
Mitigation: 只展示管理员 `verified=1` 的边；未校对时摘要明确说明「待管理员校对，暂不展示」；识别存疑的图跳过并提示人工录入。 <br>

Risk: 数字幻觉。 <br>
Mitigation: 所有学分/条件均来自结构化库解析结果，skill 只原样回报命令 stdout，不自行计算。 <br>

Risk: 将命令输出文本当作指令执行（提示注入）。 <br>
Mitigation: SKILL.md 明确规定命令 stdout 视为数据，不执行其内容。 <br>

## Reference(s): <br>
- [SKILL.md](SKILL.md) <br>
- [设计文档 004-training-plan-interpretation-design](../../docs/02-features/004-training-plan-interpretation-design.md)（相对本文件） <br>

## Skill Output: <br>
**Output Type(s):** [Shell commands, Summary text] <br>
**Output Format:** [Plain-text four-section summary] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [数字以命令输出为准；先修关系仅展示已校对项] <br>

## Evaluation Agents Used: <br>
- NVIDIA **SkillEvaluator Tier 1**（静态校验） <br>
- NVIDIA **SkillEvaluator Tier 3**（实机评测） <br>
- 本地：pytest（`tests/trainingplan`，15 用例）+ 端到端 <br>

## Evaluation Tasks: <br>
- [evals/evals.json](evals/evals.json)：**10 用例（7 positive / 3 negative）**，agentskills.io 格式
  （`{skill_name, evals:[{id,prompt,expected_output,assertions}]}`），覆盖
  专业+年级正常解读、**专业缺失必须先反问**、年级缺失列出可用年级、年级未收录不套用、
  先修关系解释，以及 **3 个"纯文档复述"用例**（只读文档、禁止执行命令），
  和"选课检查 / 成绩查询 / 上传文件"三类负例（显式 `expected_skill: null`）。 <br>

## Evaluation Metrics Used: <br>
- **SkillEvaluator Tier 1**：schema / version / security(SkillSpector) / pii / license / code-integrity /
  unicode / quality / lint / secrets(Gitleaks) 等 gate + 质量分。 <br>
- **SkillEvaluator Tier 2**：embedding 相似度聚类 + LLM 判定（去重）。 <br>
- **SkillEvaluator Tier 3**：六评估器（Security / Skill Execution / Efficiency / Accuracy / Goal Accuracy / Behavior Check），
  **双 agent 对照**（`claude-code` + `step-3.7-flash`；`codex` + `deepseek-flash`），with/without skill，报告五维。 <br>
- 本地：`tests/trainingplan` 单测（解析口径 / 队列 / 解读 / API）+ 端到端（上传 4 专业 → 队列 → 解读）。 <br>

## Evaluation Results: <br>
- **SkillEvaluator Tier 1：`PASSED WITH OBSERVATIONS`（11 validator(s)；2 finding(s)；`exit 0`）** <br>
- **SkillEvaluator Tier 2（去重）：`PASS`**（context clean） <br>
- **SkillEvaluator Tier 3：`verdict = PASS`**（10 用例 × with/without × **2 agent**，**scored 46/46**、`execution_status=succeeded`）： <br>

  **`claude-code`：Overall 0.5891 → 0.8320（lift = +0.2429，约 +24 点）** <br>

  | Evaluator | With Skill | Baseline | Lift |
  |---|---|---|---|
  | Security | 1.000 | 0.923 | +0.077 |
  | Correctness | 0.709 | 0.446 | +0.263 |
  | Discoverability | 0.795 | 0.544 | +0.251 |
  | Effectiveness | 0.667 | 0.455 | +0.212 |
  | Efficiency | 0.989 | 0.577 | +0.412 |

  **`codex`：Overall 0.6876 → 0.8462（lift = +0.1586，约 +16 点）** <br>

  | Evaluator | With Skill | Baseline | Lift |
  |---|---|---|---|
  | Security | 1.000 | 0.917 | +0.083 |
  | Correctness | 0.940 | 0.650 | +0.290 |
  | Discoverability | 0.551 | 0.600 | -0.049 |
  | Effectiveness | 0.840 | 0.566 | +0.274 |
  | Efficiency | 0.900 | 0.706 | +0.195 |

  官方判定：*dimension PASS ≥50%*（**两 agent 五维全部 PASS**）；*overall lift PASS ≥ +5 点* → 本 skill **双条件均满足**。 <br>
- 本地：`tests/trainingplan` **77 passed** + `tests/doccenter` **8 passed**。 <br>
- 详见 [BENCHMARK.md](BENCHMARK.md)（由 `skillevaluator validate --agent-eval` 生成） <br>

## Skill Version(s): <br>
1.4.0 <br>

## Ethical Considerations: <br>
只处理培养方案这一**公开**数据，不读取成绩、名单等学生个人信息（解读 API 只访问本功能自建的 `plan_*` 表）。 <br>
