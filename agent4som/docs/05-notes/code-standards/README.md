# 代码规范：clean code / 测试 / 安全

> 版本: 1.0 · 日期: 2026-09-26 · 状态: **执行中（ruff 已作为闸门）**
> 关联: [审计报告 audit-2026-09-26.md](audit-2026-09-26.md)、[测试套件设计 008](../../02-features/008-test-suite-completion.md)、[安全总览](../../04-security/user-role-tool-permission-design.md)

本规范由 2026-09-26 clean-code 审计（见审计报告）固化而来，是**强制执行**的最低标准：
ruff 规则集已写入 `pyproject.toml`，CI/本地都以 `ruff check .` 0 error 为闸门。

---

## 1. clean code 规则（ruff 强制）

### 1.1 规则集

`pyproject.toml [tool.ruff.lint]`，基础层 + clean-code 扩展层：

| 层 | 规则 | 内容 |
|----|------|------|
| 基础 | `E` `F` `W` | pycodestyle 错误/警告 + pyflakes（正确性兜底） |
| 扩展 | `B` | bugbear：常见 bug 模式（B904 异常链、B039 可变 ContextVar 默认值等） |
| 扩展 | `SIM` | simplify：冗余分支/写法 |
| 扩展 | `C4` | comprehensions：推导式简化 |
| 扩展 | `RET` | return：返回路径完整性（RET503 隐式 None 返回） |
| 扩展 | `RUF` | ruff 专属（RUF012 可变类属性 ClassVar、RUF013 隐式 Optional 等） |
| 扩展 | `PLW1510` | subprocess.run 显式 `check=` |

### 1.2 强制条款（违反即为 lint error）

1. **所有分支显式返回**（RET503）：声明 `-> str` 等返回类型的函数，任何路径不得隐式落 None；类型分发后必须有兜底 return，哪怕「正常到不了」。
2. **subprocess 必须表态**（PLW1510）：`subprocess.run(...)` 必须显式写 `check=True` 或 `check=False`。非零退出按失败处理用前者；忽略退出码必须有注释说明。
3. **异常链**（B904）：`except ... as exc:` 内再 raise 必须带 `from exc`（或 `from None` 并注明吞栈原因），不得丢根因。
4. **禁止可变默认值**：
   - module 级 `ContextVar` 默认值禁止是共享可变实例（B039）——用 `default=None`，读取处 `get() or NewInstance()`。这是 role-bleed 的温床（见 [tool-defs-cache-role-bleed](../../04-security/tool-defs-cache-role-bleed.md)）。
   - 类属性为可变容器必须标 `ClassVar[...]`（RUF012）。
5. **新代码复杂度上限**：函数圈复杂度 **≤ C 级（radon）**；D 级必须拆分或在 PR 里说明理由；E 级禁止合入。检查命令：`python3 -m radon cc <path> -s -n C`。**存量 D/E 热点已全部拆分清零**（`radon cc -n D` 全仓零残留，见审计报告 §3），新代码不得再引入 D/E。
6. **全局变量**（PLW0603，现为 unfix 告警级）：存量 11 处集中在单例缓存/模块级配置，禁止新增；新代码用工厂 + 显式传参。

### 1.3 豁免清单（逐条有审计依据，禁止无理由扩列）

`ignore` 列表每一项都必须附原因注释。当前豁免及理由：

| 规则 | 理由 |
|------|------|
| `E501` `E402` `E701` `E702` `E731` `E741` `F841` | 存量风格债（详见 pyproject 注释） |
| `RUF001/002/003` | 中文全角标点在注释/字符串，中文仓库必然误报 |
| `B023` | 本仓库「当轮定义、当轮调用」闭包模式，无跨迭代持有 |
| `SIM115` | 刻意持锁/跨 finally 返回文件句柄 |
| `SIM118` | **sqlite3.Row 的 `in`/迭代产出的是值不是键**。ruff 无类型信息，其 autofix 曾把 `row.keys()` 改成 `row`，导致 9 处 DAO 行转换 kwargs 恒空（2026-09-26 回归，42 例失败）。代码里凡 `sqlite3.Row` 一律显式 `.keys()`。 |

另：FastAPI 依赖注入默认参数属官方惯用法，经 `extend-immutable-calls` 豁免 B008。

### 1.4 autofix 纪律（事故教训）

- `ruff --fix`（safe fixes）可直接跑；
- **`--unsafe-fixes` 禁止批量跑**，只允许逐条 `--diff` 审阅后应用。SIM118 事故即来自 unsafe 批量 autofix 对无类型信息代码的语义改写。
- 任何 autofix 批量应用后必须跑全量离线测试套件确认 0 failed 才能提交。

---

## 2. 单元/集成测试规范

### 2.1 分层与离线基线

marker 分层见 `pyproject.toml [tool.pytest.ini_options]`：

| marker | 语义 | 本机可跑 |
|--------|------|----------|
| （无） | 单元/离线集成（合成数据） | ✅ |
| `integration` | 需常驻服务（chroma-server / GPU 推理） | 需服务 |
| `network` | 需外网/校园网 | 需网络 |
| `security` | tests/security/（本身离线） | ✅ |
| `samples` | 依赖 gitignored 真实样本（含学生数据，仅开发机，见 [009](../../02-features/009-test-data-layering.md)） | 仅开发机 |

**强制基线**：任何提交前 `python -m pytest tests/ -m "not integration and not network"` 必须 **0 failed**；skip 必须带 reason。干净检出（无真实样本）用 `-m "not integration and not network and not samples"`。这是所有 autofix/重构的验收闸门。

### 2.2 结构约定

1. 测试目录**镜像源码结构**（`tests/knowledge_base/repository/` ↔ `knowledge_base/repository/`）。
2. 新增源文件/大函数（>50 行）必须伴随测试；零测试合入视为缺口（对应审计「测试覆盖缺口」类别）。
3. `sys.path` 被 `~/.hermes` 遮蔽的模块（如 `gateway`），用 `importlib.util.spec_from_file_location` 按文件加载，不用裸 import。

### 2.3 既定测试模式（沿用，勿另造）

- DB/文件类：`tmp_path` 隔离，绝不触碰 `data/`；
- API 类：FastAPI `TestClient`，新建 app 不触发 lifespan；鉴权用 `monkeypatch.setattr(module, "_API_KEY", ...)` 打模块级变量；
- HTTP 外呼：mock transport / requests stub，禁止真实网络（该打 `network`）；
- 环境变量：`monkeypatch.setenv`，禁止改 `os.environ` 裸赋值。

### 2.4 测试隔离义务（易踩坑清单）

- `QueryContext` 用完必须 reset（contextvars 跨用例泄漏）；
- 测试文件基名唯一，防止 MockRepo 内同名校验互串；
- guard/计数器类夹具使用后复位；
- 依赖真实服务（reranker/chroma 单例）的用例必须打 `integration`。

---

## 3. 安全测试规范（tests/security/）

### 3.1 套件组织

按 OWASP 类别组织于 `tests/security/`（离线可跑，`pytest tests/security/ -v`）：
认证 / 授权 / 输入校验 / 注入 / 审计完整性 / 密钥管理 / 会话隔离，共 7 类。

### 3.2 新增代码的强制安全义务

1. **新增 API 端点**：必须补认证负面用例（缺 key / 错 key / 空 key → 401）；涉及角色判定时更新 `tests/security/test_authorization.py` 的角色×操作决策矩阵（5 角色 × 操作），`can_assign_role` 不得允许设 owner。
2. **新增检索/查询入口**：必须补参数化验证（`'` / `;--` / 特殊字符不炸不注入）到 `tests/security/test_injection.py`。
3. **新增上传/文件功能**：必须补文件名穿越（`../../`、`\`、CJK、控制字符）、类型门（magic bytes/扩展名）、配额用例到 `tests/security/test_input_validation.py`。
4. **密钥**：一律经 `.env` 注入；`tests/security/test_secrets.py` 会扫 git 跟踪文件的 secret 模式与 `.env.example` 占位符，新增配置项必须过此关。
5. **已知未修复问题**：相关测试保持 skip 并注明 reason + 指向 `docs/04-security/` 对应文档（如 [tool-dispatch-authz-bypass](../../04-security/tool-dispatch-authz-bypass.md)），禁止静默删除 skip。

---

## 4. 执行方式

| 检查 | 命令 | 闸门 |
|------|------|------|
| Lint | `ruff check .` | 0 error |
| 单元/离线集成 | `pytest tests/ -m "not integration and not network" -q` | 0 failed |
| 安全套件 | `pytest tests/security/ -v` | 全过 |
| 复杂度（抽查） | `radon cc <path> -s -n C` | 新代码无 D/E |
| 覆盖率 | `pytest --cov`（source 限业务包） | 报告参考，暂不设阈值 |

修订本规范须同步更新 `pyproject.toml` 的规则/豁免与根 `CLAUDE.md` 的引用。
