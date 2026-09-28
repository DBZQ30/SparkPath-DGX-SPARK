# CHANGELOG

## 1.4.0 — 2026-09-28

**提交比赛作品前的最终复评（revision `73fa2372`）。**

- 结果：**三 tier 全 PASS、双侧全出分（scored 46/46）**：
  - `claude-code`：Overall 0.5891 → 0.8320（**lift +24.3 点**），五维全部 PASS
  - `codex`：Overall 0.6876 → 0.8462（**lift +15.9 点**），五维全部 PASS
- 本轮**零看门狗误杀**。
- 同步刷新 `skill-card.md` 与 `BENCHMARK.md`。无 skill 内容改动（仅升 version）。

## 1.3.0 — 2026-09-27

**业务代码大改后重评（trainingplan 拆分重构 / db DAO 拆分 / 年级注册表统一）。**

- 夹具按最新业务代码重建（`build_training_plan_eval_env.sh`）。
- 结果：**Tier 1 `PASSED WITH OBSERVATIONS`（11 validators；2 findings）· Tier 2 PASS · Tier 3 `verdict=pass`、双侧全出分（scored 46/46）**：
  - `claude-code`：Overall 0.5940 → 0.7958（**lift +20.2 点**），五维全部 PASS
  - `codex`：Overall 0.7691 → 0.8737（**lift +10.5 点**），五维全部 PASS
- 同步刷新 `skill-card.md` 与 `BENCHMARK.md`。无 skill 内容改动（仅升 version）。

## 1.2.0 — 2026-09-26

**双 agent 重新评测（参照官方 289/366 的双 agent 形态；本环境 `codex` 走 DeepSeek）。**

- 评测改为 **`claude-code` + `codex` 双 agent**：
  - `claude-code` + `step-3.7-flash`（StepFun）
  - `codex` + `deepseek-flash`（DeepSeek；变量分配与取舍见 `shared_skills/README.md` §6-J）
- 结果：**Tier 1 `PASSED WITH OBSERVATIONS`（11 validators；2 findings）· Tier 2 PASS · Tier 3 `verdict=pass`、双侧全出分（scored 47/47）**：
  - `claude-code`：Overall 0.5672 → 0.7121（**lift +14.5 点**），五维全部 PASS
  - `codex`：Overall 0.7180 → 0.8445（**lift +12.7 点**），五维全部 PASS
- 同步刷新 `skill-card.md` 与 `BENCHMARK.md`（官方生成版）。
- 无 skill 内容改动（SKILL.md 正文不变，仅升 version）。

## 1.1.0 — 2026-09-24

- **年级相对说法可折算**：「大一/新生/今年」用 `trainingplan.cli list` 末尾的当前学年折算
  （2026-09 → 大一=2026级）确定，并在回复中说明该假设；**不再仅因未写明年份就反问**。
- 注意：行为变更使既有 `BENCHMARK.md`（v1.0.0）**不再对应本版本**；若要发布需重跑评测。

## 1.0.0 — 2026-09-23

- 首个版本：培养方案智能解读 skill。
- 触发：学生问「帮我解读某专业培养方案」；专业未说明时先引导确认（可以是本人专业或其他专业），年级缺失时列出可用年级。
- 输出：固定四段摘要（学分结构 / 四年课程地图 / 先修关系 / 毕业与授学位硬条件），原样回报 `python -m trainingplan.cli interpret` 的 stdout。
- 先修关系只展示管理员校对后的条目；未校对明确说明「待管理员校对，暂不展示」。
- 与 `academic-warning` 完全独立：不查成绩、不做选课检查、不接收上传文件。
