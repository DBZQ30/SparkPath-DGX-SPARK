# 工具调用执行层无鉴权——角色闸可被提示注入绕过

> 版本: v1.0 · 日期: 2026-07-11 · 状态: 已确认（待排期修复）

> 关联：[`user-role-tool-permission-design.md`](./user-role-tool-permission-design.md)（该文提出"工具内部角色过滤"，本文指出其落地只覆盖了暴露层、未覆盖执行层）。发现于引用脱敏调查（原记录文档已移除）。

---

## 一、结论

`terminal` / `file` / `web` 工具集对 student/guest 的角色闸**只作用于"是否把工具告诉模型"（暴露层）**，不作用于"工具是否执行"（执行层）。`registry.dispatch` 执行前**不查角色、不查 enabled_tools、不重跑 check_fn**。因此：**只要模型发起调用（提示注入即可诱导），任意角色都能执行任意文件读写 / shell / web 外发。** 当前唯一实际拦截是"模型自己选择不调用"——这是模型行为，不是安全控制。

**严重度：高。** 面向学生的本科新生学业规划智能助手，暴露任意文件读、任意命令执行、任意 URL 外发，仅靠模型合规性兜底。

---

## 二、机制与证据（代码）

**角色闸只在暴露层：**

- `check_terminal_requirements()`（`tools/terminal_tool.py`）开头：`if role in ("student","guest"): return False`
- `check_file_requirements()` = `check_terminal_requirements()`（`tools/__init__.py:18`）→ 同一道闸覆盖 `file` 工具集
- `check_browser_requirements()`（`tools/browser_tool.py`）：`students and guests cannot use browser`
- 这些是 `check_fn`。Gateway 仅把 `check_fn()` 通过的工具**告诉模型**（`model_tools.get_tool_definitions`）。已实测：student 上下文下暴露清单为 `['process','skill_manage','skill_view','skills_list','todo']`，不含 read_file/terminal。

**执行层无闸：**

- `registry.dispatch`（`tools/registry.py:605`）全部逻辑：
  ```python
  entry = self.get_entry(name)          # 查全局注册表（所有已注册工具）
  if not entry: return {"error": ...}   # 唯一检查：工具是否注册
  result = entry.handler(args, **kwargs)  # 直接跑；enabled_tools 在 kwargs 里但从不被检查
  ```
- 直接调用路径（`model_tools.handle_function_call`，`model_tools.py:1025+`）无 `not in enabled_tools → 拒绝` 闸；`_scoped_deferrable` 那道闸**只护 `tool_call` 桥接路径**，不护直接调用。
- `read_file_tool`（`tools/file_tools.py:1204`）本身零角色 / scope / 路径限制。

**工具全局注册，与平台配置无关：** 工具在 `import tools.file_tools` 时即自注册进进程全局 registry。即使从 `platform_toolsets.miniapp` 移除 `file`，`registry.dispatch("read_file", …)` 仍能查到并执行。

## 三、复现

已在受控环境实测（走真实 `registry.dispatch`，非直调 handler）：

```
student 上下文（QueryContext role=student）
① get_tool_definitions(enabled_toolsets=[web,terminal,file,skills,todo,rag]) → 暴露清单不含 read_file  （暴露层闸生效）
② registry.dispatch("read_file", {"path":"/etc/hostname","limit":3}) → 返回 "hermes-agent"  （执行层无闸）
```

生产侧旁证：student 账号（XiongWei）曾直接请求"读 /etc/hostname"，模型**拒绝**（"我没有这个工具"）——即唯一拦截来自模型合规，提示注入可绕过。

## 四、影响面

- `platform_toolsets.miniapp: [web, terminal, file, skills, todo, rag]`，所有小程序用户（含学生）同一套。
- 可读文件示例：`~/.hermes/roles.json`、`data/chroma/*.sqlite3`、任意用户个人 KB 落盘文件、`.env`。
- `web_extract` 提供现成外发通道 → 读 + 外传闭环。
- 触发条件：让模型发起一次对应工具调用。提示注入（在用户消息、被检索文档、上传文件内容里植入指令）即可。

## 五、修复选项

1. **执行层补鉴权（根治）**：在 `registry.dispatch`（或 `handle_function_call` 直接路径）跑 handler 前，对含角色闸的工具**重跑其 `check_fn` / 复检角色**，不过则拒绝。属 Hermes 核心改动（`~/.hermes/hermes-agent/`），改后须重启 Gateway。
2. **收敛平台工具集（必要但不充分）**：在 `~/.hermes/config.yaml` 移除 `terminal` / `file` / `web`。消除"广告"与下述缓存加剧因素，但因工具仍进程级全局注册、dispatch 仍可查到，**执行层的洞不闭合**——模型被注入后仍能调起。
3. **纵深**：1 + 2 同时。学业规划助手对学生本无 shell / 任意文件读写的正当需求，2 应先做；1 堵注入路径。

## 六、与缓存问题的关系

见 [`tool-defs-cache-role-bleed.md`](./tool-defs-cache-role-bleed.md)。该缓存缺陷使暴露层闸进一步失效（学生可能被**主动广告**出这些工具，连注入都不需要）。但即便修好缓存，本文的执行层无鉴权仍是更深的根。**优先级：本文 > 缓存。**
