# 工具定义缓存角色无关——跨角色工具清单串扰

> 版本: v1.0 · 日期: 2026-07-11 · 状态: 已确认机制（端到端窗口待实测）

> 关联：[`tool-dispatch-authz-bypass.md`](./tool-dispatch-authz-bypass.md)（本文是其加剧因素）、[`user-role-tool-permission-design.md`](./user-role-tool-permission-design.md)。发现于 014 引用脱敏调查。

---

## 一、结论

`model_tools.get_tool_definitions` 对工具清单做进程级 memoization，`cache_key` **不含 role / user**。同一 gateway worker 上，**上次填充缓存的角色决定该 worker 所有后续用户看到的工具清单**，直到缓存失效。两个方向都会坏：

- **越权（危险）**：owner/teacher/admin 的请求先填充 → 同 worker 的**学生**在缓存失效前被"广告"出 `terminal`/`file`/`web`（配合 [`tool-dispatch-authz-bypass.md`](./tool-dispatch-authz-bypass.md) 即可执行，**连提示注入都不需要**）。
- **误拒**：学生先填充 → owner/teacher 反而丢失这些工具。

**严重度：中高（作为执行层无鉴权的加剧因素）。** 单独存在时表现为"工具清单偶尔不对"，叠加执行层无鉴权则成为免注入的越权路径。

---

## 二、机制与证据（代码）

**外层：工具清单结果缓存（角色无关）**

- `model_tools._tool_defs_cache`（`model_tools.py:261`），模块级全局 dict，LRU 上限 8（`_TOOL_DEFS_CACHE_MAX`，`:269`）。
- `cache_key`（`model_tools.py:319-326`）：
  ```
  (frozenset(enabled_toolsets), frozenset(disabled_toolsets),
   registry._generation, config.yaml的(mtime,size), kanban_flag, skip_flag)
  ```
  **无 role、无 user_id。** `enabled_toolsets` 是平台级（`platform_toolsets.miniapp`，所有小程序用户相同）→ 全部塌缩到**同一 cache_key**。
- 命中缓存时 `_compute_tool_definitions` 不执行 → check_fn（含角色闸）**完全不跑**。
- 仅当 `quiet_mode=True` 时启用该 memoization；Gateway 正是 `quiet_mode=True`（`gateway/run.py` 等多处）。

**缓存失效条件（决定窗口长度）**：`config.yaml` 的 mtime 变化、`registry._generation` 跳变（MCP 刷新 / 插件加载）、LRU 驱逐（>8 个 key）、进程重启。**没有 30s TTL——外层缓存是"粘住"的，窗口可达小时级。**

**关键**：`get_config_path()` 指向 `~/.hermes/config.yaml`（`hermes_cli/config.py:747`），**不是 `roles.json`**。改用户角色**不会**让该缓存失效。

**内层：check_fn 结果缓存（同样角色无关）**

- `registry._check_fn_cached`（`tools/registry.py:145`），按 **check_fn 本身**缓存，TTL 30s（`_CHECK_FN_TTL_SECONDS`），失败宽限 60s（`_CHECK_FN_FAILURE_GRACE_SECONDS`）。角色判断在 check_fn 内部读 `current_context()`，但缓存不区分 role → 同样串扰。

## 三、影响与触发

- 生产为 4 个 gateway worker 服务全院；每个 worker 一份进程级缓存，服务混合角色用户 → 同 worker 内共享是结构性的，非巧合。
- owner/教师会正常使用助手 → 越权方向的窗口在真实流量下可能被打开。
- **尚未端到端实测**：具体哪个角色先填充缓存取决于流量时序与 nginx 路由，机制已由代码证实，但"真实窗口重叠概率"需构造实测（owner 先发一次触发 terminal/file 的请求 → 30s 内学生在同 worker 发请求，观察是否被放行）。

## 四、修复选项

1. **把 role 纳入缓存 key**：外层 `cache_key` 与内层 `_check_fn_cache` 均按 `(…, role)` 缓存；或对"含角色闸的 check_fn"禁用缓存。属 Hermes 核心改动。
2. **釜底抽薪**：若按 [`tool-dispatch-authz-bypass.md`](./tool-dispatch-authz-bypass.md) 选项 1 在执行层补鉴权，则暴露层缓存退化为纯"界面"问题（清单偶尔不对，但无法越权执行）——本问题的安全严重度随之降为体验问题。
3. **收敛平台工具集**（`config.yaml` 移除 terminal/file/web）：消除被广告出危险工具的可能，缓解越权方向；但误拒方向与执行层洞不受影响。

**建议**：优先做执行层鉴权（选项 2 的前置）；缓存 key 修正作为纵深。

## 五、备注

本问题是 [`tool-dispatch-authz-bypass.md`](./tool-dispatch-authz-bypass.md) 的加剧因素而非独立根因。排期上：**执行层无鉴权 > 本问题**。
