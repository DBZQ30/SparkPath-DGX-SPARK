# SparkPath-DGX-SPARK

> **面向高校教务场景的智能体系统** —— 让培养方案「看得懂」、选课风险「提前避」、学业路径「算得清」。

SparkPath 是一套面向高校本科教务的 AI 智能助手 —— **「本科新生学业规划智能助手」**，运行在 **NVIDIA DGX Spark** 平台上。
它把教务老师手里的培养方案、选课结果、成绩单、政策通知，变成师生可以直接对话获取、
**有出处、可追溯、能落地**的服务，并通过微信小程序交付给全体师生。

- 对**学生**：新生一句话就能读懂四年培养方案、按自己的目标规划学习路径；
- 对**教务老师**：上传培养方案与选课数据后，由系统自动检查学生的选课是否偏离培养计划；
- 对**教师**：上传的政策与通知入库后，全校师生可检索，数据来源真实、实时同步。

本仓库是一个 **monorepo**，由四部分组成：RAG 后端与业务模块、Hermes 运行时、微信小程序前端、
DGX Spark 部署配置。

---

## 作品说明

SparkPath 是面向高校本科教务的 AI 智能体系统，目标是把散落在培养方案、选课结果、成绩单与
政策文件中的教务信息，转化为师生可直接对话获得、且**有出处、可追溯、可执行**的服务。作品围绕
三件真实痛点展开：新生读不懂几十页培养方案；学生选课可能偏离培养计划、到毕业才发现学分结构
不达标；想转专业、辅修或走科研路线的学生缺少可量化的规划依据。

**一、权威、真实、时效的数据来源。** 知识库中的全部材料都来自学校**官方发布的正式文件**
（培养方案、教学大纲、学籍与学位规定、政策文件）与**学校官网的教务通知**：前者由教务老师在
管理端上传，后者由系统**每日自动同步**官网通知入库。来源可溯源、内容权威正确、时效性强；
回答一律附引用出处，检索不到时明确告知而非编造——真正做到「给学生的信息可信、能拿来规划」。

**二、完备的入库链路与知识库治理。** 从文件到可检索知识是一条完整、可管理的工程链路：
多格式解析（MinerU → VL → pdfminer 级联，DOCX/PPTX/XLSX/DOC 原生解析）→ 结构感知的语义切分
→ 1024 维向量化 → SHA-256 去重与版本管理 → 配额控制与审计留痕；并配套文件列表、原文预览、
删除 / 批量删除 / 孤儿扫描、适用性矩阵（功能 × 年级 × 专业）等治理能力，让知识库「进得来、
管得住、查得准、可追溯」。

**三、权限隔离与数据安全。** 采用三层 scope（公共 / 教师 / 个人）与五种角色，ACL 在**六层
纵深**同时执行：文件存储按用户物理隔离 → 身份注入请求上下文 → 写入权限校验 → 检索 scope
白名单 → ChromaDB `where` 过滤 → 检索结果防御性复核；并配 SHA-256 链式审计日志。教师入库的
政策全校可查、个人资料仅本人可见，从机制上保障数据不外泄。

**四、自研 Skill 真正生效。** 三个 Agent Skill 不是「提示词玩具」——它们调用真实业务命令，
产出落到数据库与 Excel：`academic-warning` 按年级做选课合理性检查并导出名单；
`training-plan-interpretation` 输出四段式培养方案解读；`multi-path-academic-planning` 生成
四方向四年路线图与转专业 / 辅修测算。每个 Skill 都有显式的适用边界（越界请求被引导到正确的
Skill）、命令输出视为**数据而非指令**（防提示注入）、权限在命令层强制，数据缺失时如实回报。

**五、生产级代码质量，而非原型。** 交付的是可持续维护的工程代码：`ruff` 静态检查 **0 error**；
**1000+ 单元 / 集成测试** 0 failed；按 OWASP 类别组织的**安全测试套件**（认证、授权、输入校验、
注入、审计防篡改、密钥、会话隔离）；函数复杂度闸门（无 D/E 热点）；测试数据分层与 PII 卫生
门禁；依赖 CVE 扫描。所有质量项都有可复现命令与闸门，见
[`code-standards`](agent4som/docs/05-notes/code-standards/README.md)。

**技术实现**：系统以 Hermes Agent 为运行时底座，在大模型与业务系统之间构建完整 RAG 链路：
文档经 MinerU / VL / pdfminer 级联解析、语义切分、向量化入库；检索时先做 ACL 权限过滤，再经
Step-Back 改写与领域词典扩展，走向量 + BM25 混合召回、Cross-Encoder 精排、上下文拼接，最终
带编号引用返回。业务 Skill 以命令行工具形式暴露能力，由 Agent 按明确的边界规则调度；命令输出
视为**数据而非指令**，从机制上防御提示注入。

**架构设计**：全栈部署在 NVIDIA DGX Spark 单机上，采用「**数据面 / 策略面解耦**」——RAG 数据层
负责文档的摄入、索引与权限过滤，业务模块与 Skill 负责选课可行性、学分缺口等计算，不重复解析
文档；微信小程序作为统一入口，经校园网关与反向隧道接入，保证校园网络边界内的安全交付。

**优化方案**：推理侧用本地 vLLM 部署 Qwen3.8-27B-FP8，启用 FP8 量化、MTP 投机解码与 262K
上下文；嵌入与精排用 0.6B 小模型在 GPU 上以 FP32 复刻，兼顾精度与显存；检索侧通过查询改写、
混合召回与精排提升准确率；工程侧以 ruff 静态闸门、千级单元/集成测试、OWASP 安全套件与测试
数据分层保证可维护性与合规。

---

## 目录

0. [作品说明](#作品说明)
1. [项目背景](#1-项目背景)
2. [三大核心能力](#2-三大核心能力)
3. [项目亮点](#3-项目亮点)
4. [系统架构](#4-系统架构)
5. [三个自研 Skill](#5-三个自研-skill)
6. [仓库结构](#6-仓库结构)
7. [技术栈](#7-技术栈)
8. [DGX Spark 部署全景](#8-dgx-spark-部署全景)
9. [快速开始](#9-快速开始)
10. [开发全生命周期](#10-开发全生命周期)
11. [文档索引](#11-文档索引)
12. [许可证](#12-许可证)

> **开源项目交付对照（评审 5 项 → 本文档位置）**
>
> | 交付物 | 对应位置 |
> |---|---|
> | ① 完整项目仓库 | 本仓库（GitHub）；结构见 [§6 仓库结构](#6-仓库结构)，克隆 / 运行见 [§9 快速开始](#9-快速开始)、[§10 开发全生命周期](#10-开发全生命周期) |
> | ② 项目说明文档（500+ 字） | [作品说明](#作品说明)（约 1800 字：作品特点 / 核心亮点 / 技术实现 / 架构设计 / 优化方案） |
> | ③ 部署说明 | [§8.1 本地算力部署智能体](#81-本地算力部署智能体) · [§8.2 大模型优化](#82-大模型优化) · [§8.3 Agent Skills 设计](#83-agent-skills-设计) · [§8.4 平台适配与全栈能力](#84-平台适配与全栈能力) + [`模型部署方案`](docs/DGX-SPARK-模型部署方案.md) |
> | ④ 技术栈说明（NVIDIA SDK / 模型） | [§7 技术栈](#7-技术栈)：NVIDIA SDK、NVIDIA 与 StepFun 阶跃星辰模型清单；平台适配见 [§8.4](#84-平台适配与全栈能力) |
> | ⑤ Skill Markdown 文件 | [§5 三个自研 Skill](#5-三个自研-skill)：`agent4som/shared_skills/<skill>/SKILL.md` |
>
> **演示前置（评委快速体验，约 5 步）**：① `git clone` 本仓库；② 后端 `cd agent4som && pip install -r requirements.txt`；
> ③ 前端 `cd miniprogram-framework-frontend && npm install && npm run switch`（构建产物不入库，**必做**）并配置 `config/instances/jwc.local.js`；
> ④ Hermes 运行时按 [§10.1](#101-环境初始化克隆后一次性) 安装上游框架，再 `bash agent4som/scripts/deploy_tools.sh`；
> ⑤ 模型服务按 [`模型部署方案`](docs/DGX-SPARK-模型部署方案.md) §8 启动。详见 [§9 快速开始](#9-快速开始)。

---

## 1. 项目背景

高校本科教务长期存在三组信息不对称：

| 痛点 | 具体表现 |
|------|----------|
| **培养方案读不懂** | 培养方案动辄几十页 PDF，新生看不懂学分结构、课程先后修关系、毕业与授学位的硬条件 |
| **选课偏差难发现** | 学生可能「躲开专业选修、只攻必修」钻空子，等到毕业审核才发现学分结构不达标，为时已晚 |
| **规划没有抓手** | 想走科研、想转专业、想辅修的学生，不知道要补多少学分、每学期压力多大、会不会撞上推免红线 |

同时，教务老师手里的政策文件与通知散落在官网各处、更新频繁，师生很难第一时间拿到
**准确、正式、实时**的答案。

SparkPath 用「**大模型 + RAG 知识库 + 可执行的业务 Skill**」把这三件事系统化解决。

---

## 2. 三大核心能力

### 2.1 选课风险预警（面向教务老师）

帮助教务老师智能检查学生的选课情况：结合不同年级、不同专业的培养计划与当年的学生选课结果，
自动判断学生选课是否偏离培养计划，识别「回避专业选修、只攻必修」等不合理选课行为，并在**选课阶段**就标红提醒。

- 选课合理性检查（识别回避专业选修、类别学分差额）已由 `academic-warning` Skill 落地；
- 先修未修、学分缺口、模块不达标等问题，结合培养方案解读的课程先修关系与规划测算统一呈现；
- 每一条提示都对应可执行的整改方向，把问题从「毕业前才发现」变成「选课阶段就能避开」。

### 2.2 培养方案智能解读（面向学生）

把几十页的培养方案拆成新生看得懂的三件事：

- **四年学分结构图**：必修、选修、通识、实践各占多少，一目了然；
- **课程先后修关系**：标出哪门课必须先于哪门课修，避免排到冲突的学期；
- **毕业与授学位硬条件**：直接列出还差什么，不用自己翻 PDF 去猜。

### 2.3 多路径个性化学业规划（面向学生）

学生选一个培养方向（**常规 / 交叉融合 / 科学研究 / 创新创业**），系统按方向排出四年节奏：
每年该选什么课、什么阶段进竞赛、什么时候开始科研或备考证书。

- 想辅修或转专业的学生，系统算出新增修读要求、可抵扣学分、学业压力变化，给出**可参考方案**而非替学生拍板；
- 路线图不是死的——目标变了、转专业了，重新跑一遍就能更新。

---

## 3. 项目亮点

| 亮点 | 说明 |
|------|------|
| **权威、真实、时效的数据源** | 全部材料来自学校**官方正式文件**（培养方案 / 大纲 / 学籍学位规定 / 政策）与**官网教务通知**；前者由教务老师上传、后者每日自动同步。来源可溯源、权威正确、时效性强，回答附引用出处 |
| **完备的入库链路与知识库治理** | 多格式解析级联（MinerU → VL → pdfminer）→ 语义切分 → 1024 维向量化 → SHA-256 去重/版本 → 配额与审计；配套文件列表、原文预览、删除/批量/孤儿扫描、适用性矩阵（功能×年级×专业） |
| **三级权限隔离 + 六层纵深防线** | 公共 / 教师 / 个人三层 scope、五种角色；ACL 在「文件隔离 → 身份注入 → 写入校验 → 检索白名单 → Chroma `where` 过滤 → 结果复核」六层同时执行，并配 SHA-256 链式审计 |
| **每日自动同步官网通知** | 每天定时抓取学校教务处最新通知入库（见 `agent4som/scripts/sync_jxtz.py`），确保信息时效性 |
| **真正生效的自研 Skill** | 三个 Skill 调用真实业务命令完成「选课检查并导出 Excel」「培养方案四段式解读」「四方向路线图与转专业/辅修测算」，结果落库、可复核；边界显式、拒绝越权、缺失不编造 |
| **完整的 RAG 工程链路** | 解析入库 / 出库、提问改写（Step-Back）、混合检索（向量 + BM25）、Cross-Encoder 精排、引用回填 |
| **本地化模型推理** | Embedding、Reranker、VL 与主对话 LLM **全部部署在 DGX Spark 本地**，问答与检索内容不出校；框架支持按需接入云端 OpenAI 兼容模型（可选） |
| **生产级代码质量（非原型）** | `ruff` 静态检查 **0 error**、**1000+ 单元/集成/安全测试** 0 failed、函数复杂度闸门（无 D/E 热点）、测试数据分层与 PII 卫生门禁、依赖 CVE 扫描（详见 [`code-standards`](agent4som/docs/05-notes/code-standards/README.md)） |
| **可评测的 Skill 工程** | 三个 Skill 均用 NVIDIA SkillEvaluator 做过安全、去重与行为评测（双 agent：`claude-code` + `codex`；Tier 1 PASSED WITH OBSERVATIONS（各 2–4 findings）、Tier 2 PASS、Tier 3 `verdict=pass` 且双侧全出分），评测脚本与夹具见 [`EVAL.md`](agent4som/shared_skills/EVAL.md) |
| **安全的降级设计** | 数据缺失时明确回报「缺失项」而不是编造结论；非管理员请求由命令层强制拦截，不提供伪造身份入口 |

### 3.1 创新点 vs 传统方案

| 维度 | 传统做法 | SparkPath 的做法 |
|------|----------|-----------------|
| 教务信息获取 | 翻几十页 PDF、跑线下窗口 | 对话式问答，回答**附引用出处**；检索不到如实告知而非编造 |
| 知识来源与时效 | 静态文档、更新滞后 | 学校官方文件 + **官网教务通知每日自动同步**；SHA-256 去重与版本管理 |
| 权限与数据安全 | 全公开或人工分权 | 三层 scope × 五角色，ACL **六层纵深** + SHA-256 链式审计 |
| RAG 检索 | 纯向量 top-k | ACL 过滤 → Step-Back 改写 → **向量 + BM25 混合召回** → Cross-Encoder 精排 → 引用回填 |
| Agent 能力 | 提示词插件、易幻觉 | Skill **调用真实业务命令**、结果落库 / 导出 Excel；边界门互斥；命令输出视为**数据而非指令**（防提示注入） |
| 大模型部署 | 直接加载 BF16 权重 | 面向 GB10「大内存、中带宽」做 **FP8 + MTP 投机 + 262K 满窗 + 批合并** 全链路调优 |
| 更换嵌入 / 精排模型 | 需全量重灌向量库 | 0.6B 小模型复刻线上口径，**1e-7 级对齐**，8373 条存量向量**零重灌复用** |
| 交付形态 | Demo 脚本 | systemd 常驻（9 个常驻服务 unit + 5 个 timer，另有反向隧道 unit；清单见 [`模型部署方案 §3.2`](docs/DGX-SPARK-模型部署方案.md)）；`ruff` 零 error、千级单元/集成测试、OWASP 安全套件 |

> **关键量化指标**：单流解码 **11–18.3 tok/s**（MTP 加速 **+49~57%**）；8 路并发聚合 **59.5 tok/s**（单路仅衰减 8%）；
> 嵌入 / 精排与生产向量库 **1e-7 级对齐**（8373 条零重灌）；三个 Skill 评测 **lift +15~+33 点**（双 agent）；
> 本地 vLLM 侧工具调用单轮决策 **1.66 s**。

---

## 4. 系统架构

### 4.1 端到端链路

```
                      微信小程序（学生 / 教师 / 教务管理员）
                                    │ HTTPS
                                    ▼
              校园网关 <CAMPUS_GATEWAY>（仅放行 /accapi/）
                                    │
                        acc-svr nginx（灰度路由转发）
              ┌─────────────────────┼────────────────────────┐
              │ /accapi/dgx-agentapi│ /accapi/dgx-warning     │ /accapi/dgx-plan
              ▼                     ▼                         ▼
        SSH 反向隧道 18000      SSH 反向隧道 18008        SSH 反向隧道 18009
              │                     │                         │
┌─────────────┴─────────────────────┴─────────────────────────┴──────────────────┐
│                             NVIDIA DGX Spark                                    │
│                                                                                │
│   miniapp-proxy :8020 ───► hermes 网关 :8010（对话 / 工具调度 / 权限）          │
│         │                        │                                             │
│         │                        └──► knowledge_search（RAG 检索工具）          │
│         │                                     │                               │
│   academic-warning-api :8008          training-plan-api :8009                   │
│         │                                     │                               │
│   ┌─────┴─────────────────────────────────────┴───────────────────────────┐   │
│   │  大模型与检索服务                                                       │   │
│   │  qwen3.8-27b :8000（chat / VL / Step-Back）                            │   │
│   │  qwen3-embedding :8001（1024 维）   qwen3-reranker :8002               │   │
│   │  ChromaDB :8007（向量库）           MinerU :8005（文档解析，经隧道）    │   │
│   └────────────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 一次提问发生了什么

```
用户提问
  → Hermes 网关（miniapp 适配器，HMAC 会话 token 鉴权）
  → Agent 决策：直接回答 / 调用 knowledge_search / 加载业务 Skill
  → 知识检索：ACL scope 过滤 → 查询改写 → 向量 + BM25 混合检索 → Reranker 重排
             → 上下文拼接 → 带引用的答案
  → 业务 Skill：调用 academicwarning / trainingplan 命令，执行检查 / 解读 / 规划
  → 回复用户（含出处；可下钻到小程序查看完整图表与报告）
```

---

## 5. 三个自研 Skill

三个 Skill 均位于 `agent4som/shared_skills/`，通过 Hermes 的 `skills.external_dirs` 注册。

| Skill | 版本 | 定义文件 | 面向 | 一句话职责 |
|-------|------|----------|------|-----------|
| `training-plan-interpretation` | 1.5.0 | [`SKILL.md`](agent4som/shared_skills/training-plan-interpretation/SKILL.md) | 学生 | 培养方案智能解读：学分结构 / 课程地图 / 先修关系 / 毕业授学位条件 |
| `multi-path-academic-planning` | 1.4.0 | [`SKILL.md`](agent4som/shared_skills/multi-path-academic-planning/SKILL.md) | 学生 | 多路径个性化学业规划：四方向四年路线图、专业分流、转专业/辅修模拟 |
| `academic-warning` | 4.7.0 | [`SKILL.md`](agent4som/shared_skills/academic-warning/SKILL.md) | 管理员 | 选课合理性检查：识别回避专业选修等不合理选课，并导出检查名单 |

> 三个 Skill 有明确的**边界分派**：结构解读走 `training-plan-interpretation`，
> 路径规划走 `multi-path-academic-planning`，选课检查走 `academic-warning`，
> 互不越界（SKILL.md 中的 Boundary gate 会显式拒绝越界请求）。

Skill 的开发、评测与发布流程见 [`agent4som/shared_skills/README.md`](agent4som/shared_skills/README.md)。

### 5.1 评测结论（三 tier 全 PASS）

三个 Skill 均通过 **NVIDIA SkillEvaluator** 三 tier 评测（**双 agent**：`claude-code` + `codex`）：

| Skill | Tier 1 | Tier 2 | Tier 3 | scored | lift（`claude-code` / `codex`） |
|-------|:------:|:------:|:------:|:------:|-------------------------------|
| `training-plan-interpretation` | 11/11 | PASS | **pass** | 47/47 | **+24.0 / +22.2 点** |
| `multi-path-academic-planning` | 11/11 | PASS | **pass** | 48/48 | **+34.1 / +9.3 点** |
| `academic-warning` | 11/11 | PASS | **pass** | 72/72 | **+22.3 / +10.1 点** |

- 评测源 revision：`85746b6`；三者 `BENCHMARK.md` 均为官方生成版
  **"✅ Overall verdict: PASS — Recommended for publication"**（见各 Skill 目录）。
- 判定依据：*dimension PASS ≥ 50%* 且 *overall lift ≥ +5 点*。

### 5.2 可发布包（`dist/`）

`dist/` 存放**已签名**的可发布包 —— 由 `agent4som/scripts/export_skill_release.sh`
从 Skill 源码树生成（自动排除本机评测夹具 / 缓存 / PII），无需本地重跑即可直接使用：

| 发布包 | 大小 | 版本 | 官方 5 件 |
|--------|------|------|-----------|
| [`academic-warning-4.7.0.zip`](dist/academic-warning-4.7.0.zip) | 27 KB | 4.7.0 | `SKILL.md` · `skill-card.md` · `BENCHMARK.md` · `evals/evals.json` · `skill.oms.sig` |
| [`training-plan-interpretation-1.5.0.zip`](dist/training-plan-interpretation-1.5.0.zip) | 18 KB | 1.5.0 | 同上 |
| [`multi-path-academic-planning-1.4.0.zip`](dist/multi-path-academic-planning-1.4.0.zip) | 21 KB | 1.4.0 | 同上 |

> **签名说明**：`skill.oms.sig` 为**本项目自建根证书**（`SparkPath Internal Root CA`）签发的
> detached OMS 签名，可验证"发布包内容自签名后未被修改"。
> 它**不是 NVIDIA 官方签发**，因此无法通过 `nv-agent-root-cert.pem` 验证 —— 发布到
> NVIDIA catalog 需由 NVIDIA 重新签名（见 [`agent4som/shared_skills/README.md`](agent4som/shared_skills/README.md) §11）。
> 源码树**不放** `skill.oms.sig`（官方流程：跑评测 → 评审 → **再签名**）。

**重新生成发布包**（改动 Skill 后需重签）：

```bash
bash agent4som/scripts/export_skill_release.sh <skill-name>   # 产出到 ~/work/skill-release/<skill>/
```

---

## 6. 仓库结构

```
SparkPath-DGX-SPARK/
├── README.md                        # 本文件（项目总览）
├── LICENSE                          # MIT
├── agent4som/                       # 【后端】RAG 知识库 + 四大业务模块 + 三个 Skill
│   ├── knowledge_base/              #   RAG 数据层（摄入 / 检索 / 权限 / 审计）
│   ├── academicwarning/             #   选课预警业务（检查 / 豁免 / 导出）
│   ├── trainingplan/                #   培养方案解读 + 学业规划业务
│   ├── doccenter/                   #   公共教学文件中心（统一上传 + 适用性）
│   ├── management_flow/             #   教师 / 管理员认证审核工作流
│   ├── miniapp_proxy/               #   小程序入口反向代理（路径归一化）
│   ├── shared_skills/               #   三个自研 Skill
│   ├── gateway/hooks/kb_init/       #   网关启动钩子（初始化知识库）
│   ├── scripts/                     #   入库 / 同步 / 备份 / 评测脚本
│   └── infra/                       #   systemd 服务与定时任务模板
├── agent4som-hermesagent/           # 【运行时】Hermes 家目录（~/.hermes 软链到此）
│   ├── config.yaml                  #   网关配置（模型 / 平台 / 技能注册 / 端口）
│   ├── SOUL.md                      #   Agent 身份与系统提示词
│   ├── plugins/miniapp-platform/    #   微信小程序平台适配器
│   ├── hooks/kb_init/               #   知识库初始化钩子（运行时软链，不入库；源码见 agent4som/gateway/hooks/kb_init/）
│   └── hermes-agent/                #   上游 Hermes Agent 框架源码（含本地补丁）
├── miniprogram-framework-frontend/  # 【前端】微信小程序（17 个页面）
│   ├── pages/                       #   对话 / 服务 / 预警 / 培养方案 / 文件中心 …
│   ├── config/                      #   实例配置与接口地址
│   └── scripts/                     #   多实例构建 / 校验 / 发布
├── deploy/                          # 【部署】DGX 灰度路由、隧道、GPU 服务
│   ├── dgx/                         #   nginx 路由、SSH 反向隧道、miniapp-proxy
│   └── gpu-services/                #   vLLM / Embedding / Reranker systemd 单元
├── dist/                            # 【交付】三个 Skill 的已签名可发布包（见 §5.2）
└── docs/                            # DGX Spark 迁移部署记录
```

各部分职责与详细说明见各自的 README：

| 子目录 | README | 定位 |
|--------|--------|------|
| `agent4som/` | [README](agent4som/README.md) | 后端核心：RAG 知识库、业务模块、三个 Skill |
| `agent4som-hermesagent/` | [README](agent4som-hermesagent/README.md) | Hermes 运行时家目录、配置与平台插件 |
| `miniprogram-framework-frontend/` | [README](miniprogram-framework-frontend/README.md) | 微信小程序前端 |
| `dist/` | [§5.2 可发布包](#52-可发布包dist) | 三个 Skill 的已签名发布包（含 OMS 签名） |

---

## 7. 技术栈

> **一句话**：平台 = **NVIDIA DGX Spark**；**NVIDIA SDK** = CUDA 13.0 / cuDNN / Triton 3.7.1，
> 以及 Agent Skill 治理工具 **SkillEvaluator / SkillSpector / model-signing**；**模型** = 本地开源
> （Qwen / MinerU）+ 可选云端 **StepFun 阶跃星辰**（默认全本地；评测链路用 `step-3.7-flash`）；
> **未使用 NVIDIA 发布的模型权重**（NVIDIA 的贡献在平台与工具链）。详见 §7.1 / §7.2。

### 7.1 平台与 NVIDIA SDK

| 类别 | 组件 | 说明 |
|------|------|------|
| 硬件平台 | **NVIDIA DGX Spark**（GB10 Grace Blackwell，`sm_121`，arm64，128 GB 统一内存（约 121 GiB 可用）） | 单机承载全部模型推理与业务服务 |
| GPU 计算栈 | **NVIDIA CUDA 13.0 / cuDNN**（PyTorch 2.13.0+cu130，driver 580.178.04） | 本地大模型与向量/精排服务 |
| GPU 推理引擎 | **vLLM 0.29.0**（官方 aarch64 wheel；PagedAttention / FP8 kernel / MTP 投机 / prefix cache；Triton 3.7.1） | 服务 Qwen3.8-27B-FP8 |
| 模型服务运行时 | Transformers 5.17.0、accelerate 1.15.0、FastAPI/uvicorn | Embedding / Reranker wrapper |
| Agent Skill 工具链 | **NVIDIA SkillEvaluator**（Tier 1/2/3）、**SkillSpector**（Tier 1 安全）、**model-signing**（OMS 签名） | Skill 的安全、去重、行为评测与发布签名（`skill.oms.sig` 属发布产物，**不入库**） |

> NVIDIA 官方 **NGC NIM 容器**路线经实测放弃（qwen3.8-27b NIM 无 arm64 manifest、bge-m3 NIM 为
> amd64-only、NGC 中国区 451 封锁），改为 **vLLM 0.29 原生部署 + 本地权重**，彻底脱离 NGC 分发链路。
> 选型演进与性能实录见 [`docs/DGX-SPARK-模型部署方案.md`](docs/DGX-SPARK-模型部署方案.md)。

> **关于 NVIDIA 模型**：当前推理链路使用的模型权重均为开源模型（Qwen / MinerU），
> **未使用 NVIDIA 发布的模型权重**；框架亦支持按需接入 StepFun 等云端模型（可选）。
> NVIDIA 在本项目中的角色是**平台与工具链**
> （DGX Spark + CUDA/vLLM 推理栈 + SkillEvaluator 等 Agent Skill 治理工具）。
> （历史：迁移初期 dgx 曾用 NVIDIA `nemotron-rerank-1b` 作精排，已替换为 `qwen3-reranker`
> 以与线上向量/重排口径一致。）

### 7.2 模型清单

| 用途 | 模型 | 提供方 | 部署位置 |
|------|------|--------|----------|
| 主对话 + 视觉（VL）/ Step-Back | **`qwen3.8-27b`**（Qwen3.8-27B-FP8） | 开源（Qwen） | **本地 vLLM `:8000`（默认；问答内容不出校）** |
| 可选云端对话模型 | `step-5-preview`（Step Plan） | StepFun 阶跃星辰 | 可选：`config.yaml` 支持切换至 OpenAI 兼容的云端模型 |
| 文本嵌入 | **`qwen3-embedding`**（Qwen3-Embedding-0.6B，1024 维） | 开源 | 本地 `:8001` |
| 检索精排 | **`qwen3-reranker`**（Qwen3-Reranker-0.6B） | 开源 | 本地 `:8002` |
| 文档解析 | **MinerU**（MinerU2.5-Pro-1.2B，本地已验证备用） | 开源 | 当前经隧道到 acc-svr `:8005` |
| Skill 评测被测 agent ① | **`step-3.7-flash`**（claude-code） | StepFun 阶跃星辰 | 云端（Anthropic 协议） |
| Skill 评测被测 agent ② | **`deepseek-flash`**（codex） | DeepSeek | 云端（Responses 协议） |
| Skill 评测判官 | **`deepseek-chat`** | DeepSeek | 云端（必须非推理模型，见 [`EVAL.md`](agent4som/shared_skills/EVAL.md)） |
| Tier 1 安全扫描 | **SkillSpector** / Gitleaks | NVIDIA / 开源 | 本地工具 |

> **StepFun 模型的角色**：产品运行时**默认全本地、不调用云端模型**；StepFun 模型用于
> **Skill 评测链路**（`step-3.7-flash` 作为被测 agent `claude-code`、`step-5-preview` 曾作判官测试）。
> 表中的 `step-5-preview` 仅表示「框架支持切换至云端 OpenAI 兼容模型」的可选能力（默认不启用）。
> VL / Step-Back / 上下文压缩走本地 `:8000`。

### 7.3 应用与数据层

| 层次 | 技术选型 | 说明 |
|------|----------|------|
| 运行时框架 | Hermes Agent v0.18.2 | 消息路由、Skill 调度、会话管理、工具调用 |
| 前端 | 微信小程序原生 | 17 个页面，单实例配置 + 构建产物生成 |
| 向量数据库 | ChromaDB 1.5.9 | HTTP 服务模式（:8007），Token 认证 |
| 结构化存储 | SQLite（WAL） | 配额、审计、业务库（预警 / 培养方案 / 文件中心） |
| 文档解析 | MinerU → VL → pdfminer 级联 | 多格式解析，质量门自动回退 |
| 后端服务 | Python 3.11+、FastAPI / uvicorn | 小程序 HTTP 接口 |
| 工程质量 | ruff / pytest / radon / pip-audit | Lint 闸门、单元/集成测试、复杂度、CVE 扫描 |
| 进程管理 | systemd | 服务自启、定时任务 |

---

## 8. DGX Spark 部署全景

本项目在 DGX Spark 单机上承载了从大模型推理到业务服务的完整链路。

### 8.1 本地算力部署智能体

Embedding / Reranker / VL 与本地 LLM 全部在 **DGX Spark 本地**以 systemd 常驻（模型服务 `dgx-*` +
应用服务，共 9 个常驻服务 unit + 5 个 timer，另有反向隧道 unit），通过 `127.0.0.1` 内网端口互相调用，模型权重与数据不出校
（主对话模型默认本地 `qwen3.8-27b`）。小程序请求经「校园网关 → acc-svr nginx → SSH 反向隧道 →
dgx `miniapp-proxy` → Hermes 网关 → 业务 / 检索服务」进入，反向隧道由 autossh 保活；各服务随机器自启、
失败自动重启（单元模板见 `agent4som/infra/` 与 `deploy/gpu-services/systemd/`）。
复现步骤见 §9 与 §10.1，完整架构与性能见 [`docs/DGX-SPARK-模型部署方案.md`](docs/DGX-SPARK-模型部署方案.md)。

| 端口 | 服务 | 说明 |
|------|------|------|
| 8000 | vLLM `qwen3.8-27b` | chat / VL / Step-Back（0.0.0.0） |
| 8001 | `qwen3-embedding` | 1024 维向量（127.0.0.1） |
| 8002 | `qwen3-reranker` | 精排（127.0.0.1） |
| 8005 | MinerU | 文档解析（前向隧道到 acc-svr，127.0.0.1） |
| 8007 | ChromaDB | 向量库（唯一写入者，127.0.0.1） |
| 8008 | academic-warning-api | 学业预警 HTTP 接口（0.0.0.0） |
| 8009 | training-plan-api | 培养方案解读 / 规划 / 文件中心 / GPU 指标（127.0.0.1） |
| 8010 | Hermes miniapp 适配器 | 小程序对话入口（127.0.0.1） |
| 8020 | miniapp-proxy | 小程序网关前置代理（127.0.0.1） |

定时任务：审计清理（每日 00:00）、知识库备份（每日 02:00）、临时文件清理（每周六 03:00）、
教务通知同步（每日 04:00）、GPU 服务每日重启兜底（每日 03:30）。

### 8.2 大模型优化

本地 LLM 以 **vLLM 0.29** 服务 **Qwen3.8-27B-FP8**（权重 29 GB），针对 GB10「大内存、中带宽」的物理
特性调优——单流解码上限 ≈ 权重字节数 ÷ 内存带宽（273 GB/s），因此优化方向是「权重更小、每轮多产出、
批合并读取、prefill 复用」：

- **FP8 量化**：权重由 BF16 的 ~54 GB 降到 29 GB，解码基线 8.7 tok/s ≈ 带宽上限的 93%；
- **MTP 投机解码**（`num_speculative_tokens=2`，模型自带投机层权重，零额外下载、零质量损失）：
  吞吐 **+49~57%**——单流解码 11–18.3 tok/s，8 路并发聚合 **59.5 tok/s**（单路仅衰减 8%）；
- **262K 满窗**：`--max-model-len 262144`；64 层中 48 层为 GDN 线性注意力（无 KV），KV 仅 64 KiB/token，
  配合 `--gpu-memory-utilization 0.75`、`--max-num-batched-tokens 4096`（默认 2048 会饿死 MTP verify 批）；
- **工具调用原生支持**：`--enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3`，
  且默认 `enable_thinking=false`（思考字数是答案的 5–10 倍，难题再开），是三个 Skill 的执行底座；
- **内存稳定性**：reranker / embedding 服务分块批处理 + `logits_to_keep=1` + `empty_cache()`，消除
  CUDA caching allocator 的**内存棘轮**（曾诱发周期性整机 OOM）；`dgx-gpu-services-oom-mitigation.timer`
  每日 03:30 兜底重启，systemd `Restart=on-failure` 把故障恢复压到分钟级。

嵌入与精排用 **0.6B 小模型在 GPU 上以 FP32** 复刻线上口径（服务端统一 instruction 前缀 + last-token
pooling + L2 归一化），以 **1e-7 级精度**对齐既有向量库，**8373 条存量向量零重灌复用**。运行期再叠加
Hermes 的**上下文压缩**（threshold 0.5、target_ratio 0.2）与 5 分钟 **prompt 缓存**。

> 完整选型演进（NGC NIM → vLLM）、A/B 实测数据、内存账与已知取舍见
> [`docs/DGX-SPARK-模型部署方案.md`](docs/DGX-SPARK-模型部署方案.md)。

### 8.3 Agent Skills 设计

三个自研 Skill 遵循统一目录契约（`SKILL.md` + `skill-card.md` + 生成的 `BENCHMARK.md` +
`evals/evals.json` + `references/`），通过 `skills.external_dirs` 注册。设计原则：**边界显式**
（每个 Skill 有 When to Use / When NOT to Use 与 Boundary gate，越界请求映射到正确的 Skill）；
**命令输出视为数据而非指令**（防御提示注入）；**权限在命令层强制**（读取会话身份，不提供伪造参数）；
**数据缺失时按 READY / PENDING / INCOMPLETE 分派并如实回报，不臆造结论**。能力以
`python -m <pkg>.cli ...` 形式暴露。Skill 发布前用 NVIDIA **SkillEvaluator** 跑 Tier 1
（安全 / schema / 密钥，SkillSpector + Gitleaks）、Tier 2（语义去重）、Tier 3（行为评测，双 agent），
再用 `model-signing` 生成 OMS 签名（`skill.oms.sig` 属发布产物，不入库，见
[`scripts/export_skill_release.sh`](agent4som/scripts/export_skill_release.sh)）。方法见 [`shared_skills/README.md`](agent4som/shared_skills/README.md)
与 [`EVAL.md`](agent4som/shared_skills/EVAL.md)。

详细的模型选型、性能基准与部署复现见
[`docs/DGX-SPARK-模型部署方案.md`](docs/DGX-SPARK-模型部署方案.md)；
应用迁移步骤、路由验证与风险清单见 [`docs/DGX-SPARK-部署清单.md`](docs/DGX-SPARK-部署清单.md)
与 [`deploy/dgx/README.md`](deploy/dgx/README.md)。

### 8.4 平台适配与全栈能力

把 DGX Spark 当作一台完整的推理与业务平台来用（而非「只当一块 GPU」），逐项针对其架构特征做了适配
（选型演进与实测见 [`模型部署方案`](docs/DGX-SPARK-模型部署方案.md) §2 / §5）：

- **arm64 生态适配**：GB10 是 aarch64，镜像 / wheel 必须**逐项实测**而非假设 multiarch——vLLM 0.29 用官方
  aarch64 wheel，`chromadb==1.5.9`、`llama-index` 等实测可用；NVIDIA 官方 **NGC NIM 路线经三项实测 blocker
  后放弃**（qwen3.8-27b NIM 无 arm64 manifest、bge-m3 NIM 为 amd64-only、NGC 中国区 451 封锁），
  改为 **vLLM 0.29 原生部署 + 本地权重**。
- **统一内存平台**：128 GB（约 121 GiB 可用）CPU/GPU 共享 LPDDR，单机常驻 **27B 主模型 + Embedding +
  Reranker + 向量库 + 业务服务**（9 个常驻服务 unit + 5 个 timer，另有反向隧道 unit），无 PCIe 拷贝开销；`nvidia-smi` 显存列显示 `N/A`
  是该平台的正常现象（看进程 RSS）。
- **带宽约束驱动调优**：GB10 带宽 ~273 GB/s（低于数据中心 HBM 一至两个数量级），单流解码上限
  ≈ 权重字节数 ÷ 带宽 → 由此确立「**权重更小（FP8）、每轮多产出（MTP）、批合并读取（多路并发）、
  prefill 复用（prefix cache）**」的调优主线，成效见 §8.2。
- **Blackwell（`sm_121`）kernel 实测**：CUDA 13.0 + cuDNN，vLLM 的 **Cutlass FP8 block-scaled kernel**
  在 GB10 上跑通（解码基线 8.7 tok/s ≈ 带宽上限的 93%），**Triton 3.7.1** JIT 正常。
- **NVIDIA 技术栈与 Agent 工具链**：CUDA / cuDNN / PyTorch `+cu130` 构成 GPU 推理底座；Agent Skill 的质量
  与安全由 NVIDIA **SkillEvaluator（Tier 1/2/3）+ SkillSpector + model-signing** 治理（见 §8.3）。
- **单机服务化与自愈**：全部服务 systemd 常驻（开机自启、`Restart=on-failure` + 熔断 `StartLimitBurst`）、
  `127.0.0.1` 内网互调、autossh 保活反向隧道，把「单机全栈」做到可运维。

---

## 9. 快速开始

> 完整环境要求与配置项见各子仓库 README。以下为最小启动路径。

### 9.1 后端（agent4som）

```bash
cd agent4som

# 1. 准备环境变量
cp .env.example .env        # 填入 Embedding / ChromaDB / GPU 服务地址等

# 2. 安装依赖
pip install -r requirements.txt

# 3. 运行测试
python -m pytest tests/ -v
```

### 9.2 运行时（Hermes）

```bash
# 项目约定：~/.hermes 软链到本仓库的 agent4som-hermesagent/
ln -sfn "$PWD/agent4som-hermesagent" "$HOME/.hermes"

# 启动网关
hermes gateway run --replace
```

### 9.3 前端（小程序）

```bash
cd miniprogram-framework-frontend

npm install
npm run switch              # 生成构建产物（必需，产物不入库）
# 然后用微信开发者工具导入本目录
```

---

## 10. 开发全生命周期

本章覆盖从零克隆到升级补丁的完整开发流程。§9「快速开始」是最小启动路径；
本节是全量说明，两者命令一致。

### 10.1 环境初始化（克隆后一次性）

```bash
# ── 后端（agent4som）────────────────────────────────────────
cd agent4som

python3 -m venv venv
source venv/bin/activate

python3 -m pip install -r requirements.txt
python3 -m pip install -r requirements-dev.txt   # 测试/lint 工具（pytest、ruff、pytest-cov 等）

cp .env.example .env    # 填入 Embedding / ChromaDB / GPU 服务地址等（.env 不入库）

# ── 工具链 uv（Hermes venv 需要；仓库不再内置该二进制）────
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
# 注：也可不用 uv —— 用 `python3 -m venv venv && venv/bin/pip install -e ".[dev]"` 替代下面两步

# ── Hermes 运行时（上游框架约 940MB，单独安装、不入库）────────
cd ..
git clone https://github.com/NousResearch/hermes-agent agent4som-hermesagent/hermes-agent
cd agent4som-hermesagent/hermes-agent
git checkout v2026.7.7.2   # 对应 hermes-agent 0.18.2（本项目验证版本）
uv venv --python 3.12
uv pip install -e ".[dev]"

# 项目约定：~/.hermes 软链到本仓库的 agent4som-hermesagent/
ln -sfn "$PWD/.." "$HOME/.hermes"

# ── 部署本项目对框架的定制（工具 / 补丁 / 启动钩子，幂等）────
cd ../agent4som
bash scripts/deploy_tools.sh

# ── 前端（小程序）──────────────────────────────────────────
cd ../miniprogram-framework-frontend
npm install
npm run switch        # 生成构建产物（config/instance.js / app.json / project.config.json，不入库）
```

初始化完成后用 `bash agent4som/scripts/smoke_test.sh` 冒烟检查（网关 / ChromaDB /
GPU 三服务、SQLite、磁盘）。

### 10.2 构建

- **后端无构建步骤**：Python 源码即运行时，依赖见 `agent4som/requirements.txt` / `requirements-dev.txt`；
- **前端**：`npm run switch` 即构建——由 `scripts/build.js` 按
  `config/instances/<id>.js` 组装出多实例产物（详见前端 README §3）；
- **Hermes 定制部署**：`bash agent4som/scripts/deploy_tools.sh`（升级上游后必须重跑，见 §10.5）。

### 10.3 测试

后端（`cd agent4som`，pytest 用 marker 分层，配置见 `pyproject.toml`）：

| 场景 | 命令 |
|------|------|
| 全量（含需本机服务的用例） | `python -m pytest tests/ -v` |
| **离线开发机（推荐日常）** | `python -m pytest tests/ -m "not integration and not network"` |
| 干净检出（无真实样本） | `python -m pytest tests/ -m "not integration and not network and not samples"` |
| 安全测试套件（`tests/security/`） | `python -m pytest tests/security/ -v` |
| 覆盖率报告 | `python -m pytest --cov --cov-report=term` |
| Lint | `ruff check .` |
| 类型检查（非阻断） | `mypy .` |
| 依赖 CVE 扫描 | `bash scripts/cve_check.sh` |

marker 语义：`integration` 需本机常驻服务（chroma-server / GPU 推理）、`network`
需外网、`security` 标记 `tests/security/` 套件（本身离线可跑）、`samples` 依赖
gitignored 真实样本（含学生数据，仅开发机，见 `agent4som/docs/02-features/009-test-data-layering.md`）；
测试数据分层策略（合成单测 + 开发机样本集成测试与 PII 卫生门禁）详见该设计文档。

前端（`cd miniprogram-framework-frontend`）：

```bash
npm test          # validate.js（架构核验）+ validate-ui.js（UI 规约）+ jest（单元测试）
npm run test:unit # 只跑 jest 单元测试（tests/unit/，纯逻辑，无需构建产物）
```

Skill 行为评测（NVIDIA SkillEvaluator）：`bash agent4som/scripts/run_skill_eval.sh ...`
（见 agent4som README §7）。

### 10.4 发布

| 目标 | 方式 |
|------|------|
| 小程序 | `npm run release -- jwc --version 1.1.0 --desc "..." --robot 1`（需上传密钥 `keys/<AppID>.key`，成功后自动追加 CHANGELOG 并打 tag） |
| 自研 Skill | `bash agent4som/scripts/export_skill_release.sh <skill-name>` |
| 生产部署（systemd 单元、灰度路由、隧道） | 见 [`docs/DGX-SPARK-部署清单.md`](docs/DGX-SPARK-部署清单.md)——网关 `hermes-gateway@jwc-assistant`、ChromaDB `chroma-server.service` 等 |

### 10.5 补丁与升级（Hermes 上游）

本项目对 Hermes Agent 框架的全部定制不放在框架目录内，固化在
`agent4som/hermes_overlay/`（`tools/` 检索与入库工具 + `patches/hermes-local.patch`），由
`scripts/deploy_tools.sh` 幂等部署。**升级 hermes-agent 后**：

```bash
cd agent4som
bash scripts/deploy_tools.sh                      # 1. 重新部署定制（工具/补丁/钩子）
bash tests/verification/upgrade_patch_check.sh    # 2. 完整性检查（应 0 FAIL）
python -m pytest tests/ -m "not integration and not network"   # 3. 回归
sudo systemctl restart hermes-gateway@jwc-assistant            # 4. 重启网关生效
```

若 `upgrade_patch_check.sh` 有 FAIL 且重跑 `deploy_tools.sh` 无效，说明上游文件
结构变化导致 `hermes-local.patch` 不再适用——参照
`agent4som/hermes_overlay/README.md` 手工重做补丁。

---

## 11. 文档索引

| 文档 | 内容 |
|------|------|
| [`agent4som/README.md`](agent4som/README.md) | 后端架构、业务模块、Skill、测试与运维 |
| [`agent4som-hermesagent/README.md`](agent4som-hermesagent/README.md) | Hermes 运行时配置与平台插件 |
| [`miniprogram-framework-frontend/README.md`](miniprogram-framework-frontend/README.md) | 小程序前端页面、配置、开发与发布 |
| [`docs/DGX-SPARK-模型部署方案.md`](docs/DGX-SPARK-模型部署方案.md) | **本地大模型部署方案**：选型演进（NGC NIM → vLLM）、性能基准、内存账、OOM 排障实录与已知取舍 |
| [`docs/DGX-SPARK-部署清单.md`](docs/DGX-SPARK-部署清单.md) | DGX Spark 应用迁移部署记录 |
| [`deploy/dgx/README.md`](deploy/dgx/README.md) | DGX 灰度路由与隧道 |
| [`agent4som/shared_skills/README.md`](agent4som/shared_skills/README.md) | 自研 Skill 开发与评测指南 |
| [`agent4som/shared_skills/EVAL.md`](agent4som/shared_skills/EVAL.md) | Skill 行为评测手册（NVIDIA SkillEvaluator Tier 1/2/3） |
| [`agent4som/docs/01-architecture/som-rag-knowledge-base-design.md`](agent4som/docs/01-architecture/som-rag-knowledge-base-design.md) | RAG 知识库实现详解 |
| [`agent4som/docs/05-notes/code-standards/README.md`](agent4som/docs/05-notes/code-standards/README.md) | 代码规范：clean code / 测试 / 安全（ruff 闸门、复杂度、测试分层） |
| [`agent4som/docs/02-features/008-test-suite-completion.md`](agent4som/docs/02-features/008-test-suite-completion.md) | 测试套件设计：单元 / 集成 / 安全测试矩阵 |
| [`agent4som/docs/02-features/009-test-data-layering.md`](agent4som/docs/02-features/009-test-data-layering.md) | 测试数据分层与 PII 卫生门禁 |
| [`agent4som/hermes_overlay/README.md`](agent4som/hermes_overlay/README.md) | Hermes 上游框架的本地定制（工具 + 补丁） |
| [`deploy/gpu-services/README.md`](deploy/gpu-services/README.md) | GPU 模型服务（Embedding / Reranker / MinerU）部署与口径 |

---

## 12. 许可证

本项目基于 [MIT License](LICENSE) 开源。

> 许可分层：仓库整体采用 MIT；各子目录/文件可能在文件头另行声明（如三个自研 Skill 采用
> Apache-2.0，见各 `SKILL.md` 的 `license` 字段）。上游 Hermes Agent 为 MIT（见其 `LICENSE`）。

Copyright (c) 2026 Wei Xiong
