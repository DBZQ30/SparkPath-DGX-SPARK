# SOM 文档规范

> 版本: v2.0 · 更新日期: 2026-09-26

---

## 目录结构

文档按内容类型分目录存放，禁止混放。

```
docs/
├── README.md                     # 本文档规范
├── 01-architecture/              # 系统架构设计（*.md）
├── 02-features/                  # 功能特性设计（{nnn}-{feature-slug}.md）
├── 03-issues/                    # 问题排查、BUG 修复与验证（{nnn}-{issue-slug}.md）
├── 04-security/                  # 权限模型、安全设计（*.md）
└── 05-notes/                     # 专项笔记（按主题建子目录）
```

### 目录职责

| 目录 | 内容定位 | 示例 |
|------|----------|------|
| `01-architecture/` | 全局系统架构、数据流设计、分层设计、技术选型论证 | `som-rag-knowledge-base-design.md` |
| `02-features/` | 具体功能特性的设计方案 | `001-academic-warning-design.md` |
| `03-issues/` | 线上问题根因分析、修复方案、验证矩阵 | `001-media-path-cjk-concatenation-bug.md` |
| `04-security/` | 权限模型、用户角色、工具访问控制、审批策略 | `user-role-tool-permission-design.md` |
| `05-notes/` | 专项运维/配置笔记、代码规范 | `jxtz-sync-management/README.md`、`code-standards/README.md` |

### 禁止规则

1. **禁止在路径中出现角色名或 Agent 名**：如 `jwc-assistant/`、`student/` 等。角色是配置层的概念，文档层按文档类型划分。
2. **禁止在 `docs/` 根目录直接放文档**：所有文档必须归入上述子目录之一。
3. **允许跨目录引用**：文档间可用相对路径（含 `../`）互链，以链接能正确解析为准；引用项目根资源统一用根相对路径。

---

## 文件命名规范

### 架构文档（`01-architecture/`）

```
格式: {descriptive-slug}.md
示例: som-rag-data-plane-design.md
```

- 不使用序号前缀；名称反映文档主题；多词用连字符 `-` 分隔。

### 特性文档（`02-features/`）

```
格式: {nnn}-{feature-slug}.md
示例: 004-training-plan-interpretation-design.md
```

- 三位数字序号，全局递增；每个特性一份设计文档（实现记录见代码与各 Skill 的 `CHANGELOG`）。

### 问题文档（`03-issues/`）

```
格式: {nnn}-{issue-slug}.md
示例: 001-media-path-cjk-concatenation-bug.md
```

- 三位数字序号，全局递增；slug 概括问题现象或功能点。

### 安全设计文档（`04-security/`）

```
格式: {descriptive-slug}.md
示例: user-role-tool-permission-design.md
```

---

## 文档元信息

每个文档应在开头标注版本 / 日期 / 状态：

```markdown
# 文档标题

> 版本: x.y · 日期: YYYY-MM-DD · 状态: [设计评审/实现中/已完成/已废弃]
```

状态说明：

| 状态 | 含义 |
|------|------|
| 设计评审 | 初稿完成，待架构评审 |
| 实现中 | 已有对应实现代码在开发 |
| 已完成 | 功能已上线，文档锁定 |
| 已废弃 | 不再适用的旧方案 |
