# 用户角色与工具权限体系设计（规划）

> 版本: v0.4 · 日期: 2026-05-28 · 状态: 部分实现
> **平台口径说明**：企业微信（wecom）已弃用并移除，本文出现的 `WeCom`/`wecom`/`wecom-admin`/`wecom.py`/`WECOM_HOME_CHANNEL` 等均为历史平台名（下方已逐处标注），现行唯一入口为微信小程序（平台标识 `miniapp`）。
> 
> **重要说明**：本文档 §5.2 和 §8 中的"已完成"标记反映的是**设计阶段的工作成果**（方案确定、测试编写），**并非运行时已生效的功能**。实际运行时状态请参阅 [`som-rag-knowledge-base-design.md`](../01-architecture/som-rag-knowledge-base-design.md) §7（权限控制：角色体系 / 三层防线 / 角色管理），以及 [`tool-defs-cache-role-bleed.md`](tool-defs-cache-role-bleed.md)、[`tool-dispatch-authz-bypass.md`](tool-dispatch-authz-bypass.md)（已知问题与待办）。当前 Hermes 运行时 `_get_platform_tools()` 无 `role` 参数，`_resolve_user_role()` 方法不存在，`role_toolsets` 配置被忽略。建议改用 **工具内部角色过滤**（知识搜索工具已通过 `ACLFilter` 实现），替代 gateway 层方案，避免修改 Hermes 框架源码。

---

## 1. 背景

当前 Hermes 部署中，所有小程序用户共享同一组 `platform_toolsets`：

```yaml
platform_toolsets:
  miniapp: [web, terminal, file, skills, todo, rag]
```

这导致以下问题：

1. **普通用户拥有高危工具**：`web`（浏览器）和 `terminal`（命令执行）对非管理员用户开放，存在安全风险。
2. **审批绕行**：Hermes 默认审批模式将审批请求发送到当前聊天窗口，提问者可以自行批准自己的高危操作，审批机制形同虚设。
3. **无角色区分**：学生、教师、管理员在工具层面没有差异，无法按职责最小授权。

## 2. 角色定义

### 2.1 角色层级

| 角色 | 身份来源 | 说明 |
|------|----------|------|
| `owner` | bot 部署者 | 拥有全部权限，可审批、可执行 |
| `admin` | pairing 审批 + 企业微信管理员标记（历史） | 可执行管理操作，可审批他人 |
| `teacher` | 企业微信通讯录部门识别（历史） | 可使用教学类工具，不可执行系统命令 |
| `student` | 企业微信通讯录部门识别（历史） | 仅可使用知识库检索、文件上传等基本功能 |
| `guest` | 未配对的外部用户 | 最低权限，仅限预设问答 |

### 2.2 设计原则

- **最小权限**：每个角色只拥有其职责所需的工具
- **审批不出圈**：高危操作的审批请求只发送给 `owner`/`admin`，不发送给操作者本人
- **拒绝无声**：越权操作直接拒绝，不弹审批、不给选项
- **数据联动**：角色体系与 `knowledge_base/retrieval/acl_filter.py` 的可见性模型对齐

## 3. 工具分类

| 风险等级 | 工具 | 说明 |
|----------|------|------|
| **安全** | `rag` | 知识库检索，只读 |
| **安全** | `file` | 文件上传/读取，受配额控制 |
| **安全** | `skills` | 预设技能执行 |
| **安全** | `todo` | 任务管理 |
| **低风险** | `vision` | 图片识别 |
| **中风险** | `web` | 浏览器上网，可外泄数据 |
| **高风险** | `terminal` | 终端命令执行，可写文件 |
| **高风险** | `browser` | 完整浏览器控制 |

## 4. 推荐工具矩阵

| 工具 | guest | student | teacher | admin | owner |
|------|-------|---------|---------|-------|-------|
| `rag` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `file` | ❌ | ✅ | ✅ | ✅ | ✅ |
| `skills` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `todo` | ❌ | ✅ | ✅ | ✅ | ✅ |
| `vision` | ❌ | ❌ | ✅ | ✅ | ✅ |
| `web` | ❌ | ❌ | ❌ | ✅ | ✅ |
| `terminal` | ❌ | ❌ | ❌ | ❌ | ✅ |
| `browser` | ❌ | ❌ | ❌ | ❌ | ✅ |

### 审批规则

| 场景 | 操作角色 | 审批流程 |
|------|---------|----------|
| student 触发 rag/file/skills/todo | 自动放行 | 无需审批 |
| admin 触发 web | 自动放行 | 无需审批 |
| owner 触发 terminal | 自动放行 | 无需审批 |
| guest/student/teacher 触发 web | 直接拒绝 | "无权使用该功能" |
| 任何角色触发越权工具 | 直接拒绝 | 不弹审批 |
| cron 任务执行命令 | — | 默认拒绝，owner 可单独放行 |

## 5. 实现方案（已选择：方案 B 变体 — `_get_platform_tools()` 角色交集）

> **2026-05-15 更新**：已实现并部署。去掉了 WeCom 通讯录依赖，改用本地 `roles.json` + 助手审批。

### 5.1 方案 A：平台层工具集静态拆分（低投入）— 待规划

利用 Hermes 的多平台能力，为不同角色创建不同的平台入口。例如：

- `miniapp-admin` 平台：开放 `web, terminal, file, skills, todo, rag`
- `miniapp-student` 平台：仅开放 `rag, file, skills`

但 Hermes 原生不支持同一 gateway 实例监听多平台，需要部署两个 gateway 实例。

### 5.2 方案 B：Gateway 层角色拦截器（设计中，⚠️ 未在 Hermes 运行时生效）

采用 **方案 B 变体** — 不在企业微信适配器 `wecom.py`（已弃用）做中间件，改为在工具集解析层 `_get_platform_tools()` 做角色交集：

```
小程序收到消息
  → GatewayRunner._resolve_user_role(source, config)  ← ❌ Hermes v0.14.0 无此方法
  → 从 roles.json 查询用户角色（Owner 自动检测）
  → _get_platform_tools(config, platform_key, role=role)  ← ❌ 函数无 role 参数
  → 结果 = platform_toolsets ∩ role_toolsets[role]  ← ❌ 未执行
  → Agent 只获得授权后的工具集
```

**当前运行时状态**：`_get_platform_tools()` 不接受 `role` 参数，`_resolve_user_role()` 不存在，`role_toolsets` 配置被忽略。详见架构文档 §5.6。

**改动文件**（设计阶段，等待实施）：
| 文件 | 改动 | 实施状态 |
|------|------|---------|
| `hermes_cli/tools_config.py` | `_get_platform_tools()` 新增 `role` 参数，做集合交集 | ❌ 未实施 |
| `gateway/run.py` | 新增 `_resolve_user_role()` 方法，两个调用处传入 `role` | ❌ 未实施 |
| `~/.hermes/config.yaml` | 新增 `role_toolsets` 定义每角色允许的工具集 | ✅ 已定义但被忽略 |
| `~/.hermes/roles.json` | JSON 文件存储 `{platform: {user_id: role}}` 映射 | ✅ 已实现 |
| `shared_skills/auth-manager/SKILL.md` | `/promote` / `/demote` 技能 | ✅ 已实现 |
| `knowledge_base/auth/role_store.py` | 角色读写模块（公共 API） | ✅ 已实现 |

**角色-工具矩阵**（定义在 `config.yaml:role_toolsets`）：

```
owner   → ["*"]                       # 全部平台工具
admin   → [web, file, skills, todo, rag]
teacher → [file, skills, todo, rag, vision]
student → [file, skills, todo, rag]   # 默认角色
guest   → [rag, skills]
```

**所有者识别逻辑**（`_resolve_user_role`）：
1. `HERMES_OWNER` 或 `WECOM_HOME_CHANNEL`（历史，企业微信已弃用）环境变量 → 自动 `owner`
2. `~/.hermes/roles.json` 显式映射
3. config.yaml 中 `roles.platform` 遗留映射
4. 默认 → `student`

优点：改动极小，不破坏 Hermes 升级兼容性。  
缺点：工具过滤在 Agent 构造时生效，运行时无法动态切换（需新会话）。

### 5.3 方案 C：Hermes 审批系统改造（高投入）— 待规划

修改 Hermes 审批机制，增加以下能力：

- 审批请求指定接收人（不发送给操作者）
- 工具级别角色 ACL（不依赖 platform_toolsets 的全局配置）
- 审批超时自动拒绝（默认 60 秒）

## 6. 与其他系统的关系

```
角色权限体系
  ├── 工具层  ← 本文讨论范围（gateway/platforms/ + config.yaml）
  ├── 数据层  ← 已有实现（knowledge_base/retrieval/acl_filter.py）
  │              knowledge_base/retrieval/conflict_resolver.py
  └── 技能层  ← 后续规划（skills/ 按角色加载不同技能）
```

- **数据层 ACL** 已通过 `ACLFilter` 实现：student 不能读他人私库，admin 不能读任意学生私库。
- **工具层 ACL** 是本文核心目标。
- **技能层** 建议后续按角色加载不同 skill 集合。

## 7. 风险与约束

1. ~~**WeCom 通讯录 API**~~：已取消此依赖，改用本地 roles.json + 助手审批。
2. **并发 gateway**：如果采用方案 A 多实例，需要处理端口冲突和进程管理。（不适用，已选方案 B）
3. **审批超时**：owner 不在线时，admin 的高危操作会阻塞等待审批。（当前无实时审批，越权直接拒绝）

## 8. 后续工作

### 设计阶段已完成

- [x] 确定最终方案（方案 B 变体：`_get_platform_tools()` 角色交集）
- [x] 角色层级定义（owner/admin/teacher/student/guest）
- [ ] `_get_platform_tools()` 支持 `role` 参数 — ⚠️ **设计完成但未在 Hermes 运行时实施**，详见架构文档 §5.6
- [ ] Gateway 层 `_resolve_user_role()` 方法 — ⚠️ **同上**
- [x] `~/.hermes/roles.json` 角色持久化
- [x] `auth-manager` skill（`/promote`, `/demote`）
- [x] `knowledge_base/auth/role_store.py` 公共角色 API

### 测试阶段已完成

- [x] 编写负向测试用例：越权访问应直接拒绝（75 tests: 41 role_store + 15 permission_intersection + 19 gateway_role_resolution）

### 待实现

- [ ] 更新部署脚本，支持角色配置
- [ ] 更新 `knowledge_base/retrieval/acl_filter.py` 与工具层角色体系对齐

### 策略调整（2026-05-28）

根据架构评审 C-5 建议，Gateway 层 `_get_platform_tools(role)` 方案已调整为**工具内部角色过滤**（Phase 2.4）：

- `knowledge_search` 工具已通过 `ACLFilter` 按角色过滤检索结果，替代 gateway 层的 toolset 交集
- `RawIndexNode` 新增 `visibility_tag` 字段（`public`/`student_scope`/`teacher_scope`/`internal`），实现四标签可见性模型
- 后续其他工具（如 `terminal`、`web`）可遵循同样模式：工具 handler 内部检查 `QueryContext.role` 决定是否放行
