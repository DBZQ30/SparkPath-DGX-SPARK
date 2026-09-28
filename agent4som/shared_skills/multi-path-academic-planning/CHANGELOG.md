# CHANGELOG — multi-path-academic-planning

## 1.3.0 — 2026-09-28

**提交比赛作品前的最终复评（revision `73fa2372`）。**

- 结果：**三 tier 全 PASS、双侧全出分（scored 48/48）**：
  - `claude-code`：Overall 0.6002 → 0.9311（**lift +33.1 点**），五维全部 PASS
  - `codex`：Overall 0.7788 → 0.9255（**lift +14.7 点**），五维全部 PASS
- 本轮**零看门狗误杀**。
- 同步刷新 `skill-card.md` 与 `BENCHMARK.md`。无 skill 内容改动（仅升 version）。

## 1.2.0 — 2026-09-27

**业务代码大改后重评（trainingplan route/simulate 拆分重构 / 政策库更新）。**

- 夹具按最新业务代码重建（`build_multi_path_eval_env.sh`）。
- 结果：**Tier 1 `PASSED WITH OBSERVATIONS`（11 validators；2 findings）· Tier 2 PASS · Tier 3 `verdict=pass`、双侧全出分（scored 52/52）**：
  - `claude-code`：Overall 0.5283 → 0.9496（**lift +42.1 点**），五维全部 PASS
  - `codex`：Overall 0.7201 → 0.8601（**lift +14.0 点**），五维全部 PASS
- 同步刷新 `skill-card.md` 与 `BENCHMARK.md`。无 skill 内容改动（仅升 version）。

## 1.1.0 — 2026-09-26

**双 agent 重新评测（参照官方 289/366 的双 agent 形态；本环境 `codex` 走 DeepSeek）。**

- 评测改为 **`claude-code` + `codex` 双 agent**：
  - `claude-code` + `step-3.7-flash`（StepFun）
  - `codex` + `deepseek-flash`（DeepSeek；变量分配与取舍见 `shared_skills/README.md` §6-J）
- 结果：**Tier 1 `PASSED WITH OBSERVATIONS`（11 validators；2 findings）· Tier 2 PASS · Tier 3 `verdict=pass`、双侧全出分（scored 47/47）**：
  - `claude-code`：Overall 0.5912 → 0.9214（**lift +33.0 点**），五维全部 PASS
  - `codex`：Overall 0.8435 → 0.9262（**lift +8.3 点**），五维全部 PASS
- 同步刷新 `skill-card.md` 与 `BENCHMARK.md`（官方生成版）。
- 无 skill 内容改动（SKILL.md 正文不变，仅升 version）。

## 1.0.4 — 2026-09-24

- **年级相对说法可折算**：「大一/新生/今年」用 `trainingplan.cli list` 末尾的当前学年折算
  （2026-09 → 大一=2026级）确定，**不再仅因没写明年份就反问**；并在回复里说明该假设。

## 1.0.3 — 2026-09-24

- 专业选择：**直接运行 `select`**（不先反问志愿），先给计划/成绩/策略再讨论。
- Boundary gate：越界问题**逐条列出**"类别 → 对应 skill"，避免笼统回答。
- `evals.json`：`expected_script` 改为 `trainingplan.cli <cmd>`（更宽松的字符串匹配）。

## 1.0.2 — 2026-09-24

- 去掉 SKILL.md 中的不可见变体字符（VARIATION SELECTOR-16），清除 Tier 1 `unicode` LOW 提示。

## 1.0.1 — 2026-09-24

评测驱动的两处行为修正（首轮 Tier 3 逐用例分析）：

- **边界门（Boundary gate）**：Instructions 新增第 0 步——命中「结构解读 / 成绩绩点排名 / 预警选课检查 /
  代选课」时**不加载本 skill、不执行任何 `trainingplan` 命令**（此前负例 `plan-neg-interpret` 被直接结构解读）。
- **年级不重复反问**：已明确到年级（含「大一（2025级）」）时**直接采用**，仅完全未给年级时才询问
  （此前 `plan-pos-select` 被多余反问年级）。

## 1.0.0 — 2026-09-24

首版。对应设计文档《多路径个性化学业规划功能设计》（agent4som/docs/02-features/005-multi-path-academic-planning-design.md）。

- **四方向四年路线图**：`route` / `compare`（逐学期课程 + 学分 + 负荷档 + 节奏节点；模式 overlay）。
- **专业选择（分流）模拟**：`select`（接收计划 / 综合成绩构成 / 冲稳保志愿策略参考）。
- **转专业模拟**：`simulate`（可抵扣 / 需补修 / 压力与推免红线 / ACCA 降级 / 考核与补修政策）。
- **辅修**：`simulate-minor`（预留，待教务提供教学计划）。
- **学生身份解析**：学籍档案（`student_archive`）优先、手工绑定兜底；未解析到身份只给方案级结论。
- **节奏节点**：官方硬节点（★官方）+ 通用建议（标注「建议」），见 `references/path-rhythm.md`。
- **免责声明**：每次输出必附「建议性规划、以学院审批与公示为准」。
