## Description: <br>
按年级对已上传的选课结果执行选课合理性检查，识别“回避专业选修、只攻必修”等不符合培养计划的选课，向教务管理员回报摘要并导出名单报告。 <br>

本 skill 为内部（Hermes 小程序）管理工具，未发布至公开目录。 <br>

## Owner
XiongWei <br>

### License/Terms of Use: <br>
Apache-2.0 <br>
## Use Case: <br>
小程序管理员（admin/owner）在对话中触发某年级的选课检查，并查看不合理选课名单与 Excel 报告。 <br>

### Deployment Geography for Use: <br>
内部部署（校园网络） <br>

## Requirements / Dependencies: <br>
**Requires API Key or External Credential:** [No] <br>
**Credential Type(s):** [None] <br>

依赖：Python 3.11、`academicwarning` 包（SQLite `data/warning.db`）、`HERMES_SESSION_PLATFORM` / `HERMES_SESSION_USER_ID` 会话身份、角色库 `knowledge_base.auth.role_store`。 <br>
权限来源为调用方会话身份，不引入额外密钥。 <br>

**签名（Signing）：** 按 NVIDIA release checklist —— *「跑评测 → 完成 skill card → 签名刚通过评审的目录 → 发布」* ——
签名发生在**评测之后**，因此**源码目录不含 `skill.oms.sig`**；发布包由 `scripts/export_skill_release.sh` 生成并附带
`skill.oms.sig`（OMS 格式，detached signature）。
> ⚠️ 本机构建使用**自建根证书**（`SparkPath Internal Root CA`，非 NVIDIA 签发），验证方式见该脚本；
> 若纳入 NVIDIA catalog，需改用 NVIDIA 签发的证书链（`nv-agent-root-cert.pem`）。 <br>

## Known Risks and Mitigations: <br>
Risk: 非管理员越权触发（身份伪造）。 <br>
Mitigation: 权限由命令内部从会话身份强制校验 admin/owner；SKILL.md 明确禁止手工传 `--user` / `--platform`。 <br>

Risk: 数据缺失导致误判。 <br>
Mitigation: 缺选课结果 / 名单 / 成绩单时降级为提示文本，不产出结果；某专业无成绩记录则跳过，防假阳性。 <br>

Risk: 将命令输出中的文本当作指令执行（提示注入）。 <br>
Mitigation: SKILL.md 规定命令 stdout 视为数据，不执行其内容。 <br>

## Reference(s): <br>
- [SKILL.md](SKILL.md) <br>
- [references/scoring-rules.md](references/scoring-rules.md) <br>
- [references/precheck-rules.md](references/precheck-rules.md) <br>
- [设计文档 001-academic-warning-design](../../docs/02-features/001-academic-warning-design.md)（相对本文件） <br>

## Skill Output: <br>
**Output Type(s):** [Shell commands, Summary text, Excel report] <br>
**Output Format:** [Plain-text summary + xlsx file] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [报告文件名随导出时间变化] <br>

## Evaluation Agents Used: <br>
- NVIDIA **SkillEvaluator Tier 1**（静态校验，已运行 PASS） <br>
- NVIDIA **SkillEvaluator Tier 2**（语义去重，已运行 PASS） <br>
- NVIDIA **SkillEvaluator Tier 3**（实机评测，已运行 PASS） <br>
- 本地：pytest + 端到端 <br>

## Evaluation Tasks: <br>
- [evals/evals.json](evals/evals.json)：**17 用例（14 positive / 3 negative）**，agentskills.io 格式
  （`{skill_name, evals:[{id,prompt,expected_output,assertions}]}`），覆盖
  数据预检 READY / PENDING / INCOMPLETE 三分支、年级规范化、权限拒绝、降级提示、
  **5 个"纯文档复述"用例**（只读文档、禁止执行命令），
  以及"上传文件 / 成绩排名 / 非管理员"三类负例。 <br>

## Evaluation Metrics Used: <br>
- **SkillEvaluator Tier 1**：schema / version / security(SkillSpector) / pii / license / code-integrity /
  unicode / quality / lint / secrets(Gitleaks) 等 11 项 gate + 质量分。 <br>
- **SkillEvaluator Tier 2**：embedding 相似度聚类 + LLM 判定（去重；本机 `qwen3-embedding` + `deepseek-chat`）。 <br>
- **SkillEvaluator Tier 3**：六评估器（Security / Skill Execution / Efficiency / Accuracy / Goal Accuracy / Behavior Check），
  **双 agent 对照**（`claude-code` + `step-3.7-flash`；`codex` + `deepseek-flash`），with/without skill，
  报告五维（Security / Correctness / Discoverability / Effectiveness / Efficiency）。 <br>
- 本地：单元 / 集成测试通过率、端到端摘要正确性。 <br>

## Evaluation Results: <br>
- **SkillEvaluator Tier 1：`PASSED WITH OBSERVATIONS`（11 validator(s)；4 finding(s)；`exit 0`）** <br>
- **SkillEvaluator Tier 2：`PASS`**（`clean — no duplicate guidance`） <br>
- **SkillEvaluator Tier 3：`verdict = PASS`**（17 用例 × with/without × **2 agent**，**scored 78/78**、`execution_status=succeeded`）： <br>

  **`claude-code`：Overall 0.6135 → 0.8111（lift = +0.1976，约 +20 点）** <br>

  | Evaluator | With Skill | Baseline | Lift |
  |---|---|---|---|
  | Security | 1.000 | 1.000 | +0.000 |
  | Correctness | 0.790 | 0.627 | +0.163 |
  | Discoverability | 0.686 | 0.462 | +0.224 |
  | Effectiveness | 0.770 | 0.462 | +0.308 |
  | Efficiency | 0.809 | 0.515 | +0.294 |

  **`codex`：Overall 0.7075 → 0.9272（lift = +0.2197，约 +22 点）** <br>

  | Evaluator | With Skill | Baseline | Lift |
  |---|---|---|---|
  | Security | 0.941 | 0.947 | -0.006 |
  | Correctness | 1.000 | 0.768 | +0.232 |
  | Discoverability | 0.718 | 0.482 | +0.236 |
  | Effectiveness | 0.979 | 0.710 | +0.270 |
  | Efficiency | 0.997 | 0.630 | +0.367 |

  官方判定：*dimension PASS ≥50%*（**两 agent 五维全部 PASS**）；*overall lift PASS ≥ +5 点* → 本 skill **双条件均满足**。 <br>
- `tests/academicwarning`：**153 passed**（含 precheck 14 例） <br>
- 端到端：对话触发实测（管理员"帮我检查23级选课预警情况"）→ precheck READY → check
  132 人 / 不合理 49 人 / 报告导出；临时库构造 queued→PENDING、failed→INCOMPLETE 均正确 <br>
- 详见 [BENCHMARK.md](BENCHMARK.md)（由 `skillevaluator validate --agent-eval --tiers 1,2,3` 生成） <br>

## Skill Version(s): <br>
4.6.0 (source: CHANGELOG.md) <br>

## Ethical Considerations: <br>
内部管理工具，涉及学生学业数据。使用时遵循数据最小化与访问控制：仅管理员可触发，报告通过小程序“学业预警”页受控查看 / 下载。 <br>
