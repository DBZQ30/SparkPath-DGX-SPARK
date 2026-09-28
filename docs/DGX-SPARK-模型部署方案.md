# SparkPath 本地大模型部署方案（DGX Spark）

> **状态**：全栈在线（2026-09-27 核实，`systemctl` / 端口 / `/v1/models` 逐项验证）
> **范围**：DGX Spark 单机模型推理栈 + 应用接入链路
> **依据**：`~/work/docs/model-selection-design.md`（选型与性能调优实录 v3）、
> [`DGX-SPARK-部署清单.md`](DGX-SPARK-部署清单.md)（应用迁移与 Embedding 兼容性实录）、
> [`deploy/gpu-services/README.md`](../deploy/gpu-services/README.md)（服务实现细节）。
> 文中所有性能数据均为本机实测（2026-09-21，262K 满窗生产配置、全栈同跑）。

---

## 1. 一页总览

SparkPath 是面向高校教务的 RAG 智能体系统（培养方案解读 / 选课风险预警 / 多路径学业规划），
**主要模型推理部署在一台 NVIDIA DGX Spark 上**（文档解析 MinerU 当前经内网隧道复用 acc-svr
服务、本地权重已验证备用，见 §5 阶段 7），通过微信小程序向全校师生交付：

| 关键项 | 结果 |
|---|---|
| 主模型 | **Qwen3.8-27B-FP8**（vLLM 0.29，262K 上下文 + MTP 投机解码） |
| 单流解码 | 11.0–18.3 tok/s（负载相关，MTP 加速后 **+49~57%**） |
| 并发 | 8 路并发聚合 **59.5 tok/s**，单路仅衰减 8% |
| Agent 工具调用 | 每轮决策延迟 **1.66 s**（含 prefill，prefix cache 命中后） |
| 长上下文 | 262K 满窗，100K prompt 内 needle 检索正确（37.5% / 62.5% 深度） |
| 检索配套 | Qwen3-Embedding-0.6B（1024 维）+ Qwen3-Reranker-0.6B，FP32 与生产向量库 **1e-7 级对齐**，8373 条存量向量**零重灌复用** |
| 文档解析 | MinerU 级联（MinerU → VL → pdfminer），本地 MinerU2.5-Pro-1.2B 已验证 |
| 内存 | 全栈（模型 + 向量库 + 业务 + 桌面）约 **96–99 GiB**（可用约 121 GiB） |
| 数据边界 | Embedding / Rerank / VL / 本地 LLM 全在本机 `127.0.0.1`，**问答与检索内容不出校**；框架支持按需接入云端 OpenAI 兼容模型（可选） |

---

## 2. 硬件平台与物理约束

| 项 | 值 | 对部署的含义 |
|---|---|---|
| GPU | NVIDIA GB10 Grace Blackwell（`sm_121`） | Blackwell 世代 kernel；**arm64**，镜像/wheel 必须有 aarch64 版本 |
| 内存 | **128 GB 统一内存**（约 121 GiB 可用；CPU/GPU 共享 LPDDR，~273 GB/s） | 27B 模型全驻留毫无压力；但解码带宽远低于数据中心 HBM（3–8 TB/s） |
| CPU | 20 核 Grace（aarch64） | 与 GPU 共享内存，无 PCIe 拷贝开销 |
| 存储 | 3.7 TB NVMe | 权重 + 向量库 + 业务数据库全本地 |
| 系统 | Ubuntu 24.04.5，CUDA 13.0，driver 580.178.04 | torch 2.13.0+cu130 |
| 排障提示 | `nvidia-smi` 显存列显示 `N/A` 属正常 | 统一内存平台无独立显存概念，看进程 RSS |

> **核心认知：GB10 是"大内存、中带宽"平台。** 单流解码速度的理论上限 ≈ 权重字节数 ÷ 273 GB/s
> （27B FP8 = 29 GB → ~9.4 tok/s）。这条物理规律决定了全部调优方向：
> ① **权重尽量小**（FP8 量化）；② **每次前向多产出 token**（MTP 投机解码）；
> ③ **权重读取批合并**（多路并发共享一次读取）；④ **prefill 尽量复用**（prefix cache）。

### 2.1 平台适配与全栈能力（摘要）

把 DGX Spark 当作完整推理与业务平台来用，逐项适配其架构特征（细节见 §5 / §6）：

| 平台特征 | 适配动作 | 依据 |
|---|---|---|
| **arm64**（GB10 aarch64） | mirror/wheel 逐项实测，不假设 multiarch；NGC NIM 经三项 blocker 后改 vLLM 原生 | §5 阶段 1 |
| **128 GB 统一内存**（约 121 GiB 可用） | 单机常驻主模型 + Embedding + Reranker + 向量库 + 业务（9 个常驻服务 unit + 5 个 timer，另有反向隧道 unit）；无 PCIe 拷贝 | §3.2 / §6.6 |
| **~273 GB/s 带宽** | 单流上限 ≈ 权重字节 ÷ 带宽 → FP8 / MTP / 批合并 / prefix cache 主线 | §2 / §5 阶段 2–4 |
| **Blackwell `sm_121`** | CUDA 13.0 + cuDNN；Cutlass FP8 block-scaled kernel 跑通、Triton 3.7.1 JIT 正常 | §5 阶段 1–2 |
| **NVIDIA Agent 工具链** | SkillEvaluator（Tier 1/2/3）+ SkillSpector + model-signing 治理自研 Skill | §4.3 |

---

## 3. 部署架构

### 3.1 服务拓扑（2026-09-27 实测在线）

```
微信小程序（学生 / 教师 / 教务管理员）
   │ HTTPS
   ▼
校园网关 <CAMPUS_GATEWAY> ──► acc-svr nginx（仅放行 /accapi/*）
   │  /accapi/dgx-agentapi → 隧道 18000   /accapi/dgx-warning → 18008   /accapi/dgx-plan → 18009
   ▼  autossh 反向隧道（acc-svr 127.0.0.1 ◄──► DGX Spark）
┌──────────────────── NVIDIA DGX Spark（<DGX_HOST>）────────────────────┐
│ 应用层（systemd 常驻）                                                 │
│   miniapp-proxy :8020 ──► Hermes 网关 :8010（Agent 调度/权限/会话）    │
│   academic-warning-api :8008      training-plan-api :8009             │
│   ChromaDB :8007（向量库，Token 认证）                                 │
│ 模型层（systemd 常驻，全部 GPU）                                       │
│   qwen3.8-27b :8000（vLLM：chat / VL / Step-Back / 工具调用）         │
│   qwen3-embedding :8001（1024 维）     qwen3-reranker :8002           │
│   MinerU :8005（前向隧道 → acc-svr 旧版 API，见 §5 阶段 7）           │
└────────────────────────────────────────────────────────────────────────┘
```

### 3.2 在线服务清单（`systemctl` 逐项核实）

| 端口 | systemd unit | 服务 | 绑定 |
|---|---|---|---|
| 8000 | `dgx-vllm.service` | vLLM Qwen3.8-27B-FP8 | 0.0.0.0 |
| 8001 | `dgx-embedding.service` | Qwen3-Embedding-0.6B（transformers wrapper） | 127.0.0.1 |
| 8002 | `dgx-reranker.service` | Qwen3-Reranker-0.6B（transformers wrapper） | 127.0.0.1 |
| 8005 | `accsvr-mineru-tunnel`（autossh 前向） | MinerU 文档解析 | 127.0.0.1 |
| 8007 | `chroma-server.service` | ChromaDB 1.5.9 | 127.0.0.1 |
| 8008 | `academic-warning-api.service` | 学业预警 API | 0.0.0.0 |
| 8009 | `training-plan-api.service` | 培养方案/规划/文件中心 API | 127.0.0.1 |
| 8010 | `hermes-gateway@jwc-assistant.service` | Hermes 网关（miniapp 适配器） | 127.0.0.1 |
| 8020 | `miniapp-proxy.service` | 小程序前置代理 | 127.0.0.1 |

另有：反向隧道 unit `accapi-tunnel`（18000/8010/18008/18009 四条转发，autossh 保活），
以及 5 个 timer 文件（知识库备份 02:00、审计清理 00:00、临时文件清理周六 03:00、
教务通知同步 `jxtz-sync` 04:00【**已停用**，改由 hermes cron 承担】、
`dgx-gpu-services-oom-mitigation.timer` 每日 03:30 重启 embedding/reranker 兜底——见 §10）；
即 5 个 timer 文件、实际启用 4 个。

---

## 4. 组件清单

### 4.1 模型权重（`~/models/models/`）

> 落盘布局：**主模型**为 ModelScope 的 `org--name/snapshots/<rev>`（如 `Qwen--Qwen3.8-27B-FP8/snapshots/master`）；
> **Embedding / Reranker** 为裸目录名（`Qwen3-Embedding-0.6B` / `Qwen3-Reranker-0.6B`），与 `deploy/gpu-services/systemd/dgx-*.service` 的 `MODEL_PATH` 一致。

| 角色 | 模型 | 权重 | 状态 | 运行占用 |
|---|---|---|---|---|
| 主 Chat + VL + Step-Back | **Qwen3.8-27B-FP8**（66 shards，自带 MTP 投机层权重） | 29 GB | ✅ 在线 :8000 | 89.2 GiB（含 KV 池） |
| Embedding | **Qwen3-Embedding-0.6B**（1024 维） | 1.2 GB | ✅ 在线 :8001 | ~2.5 GiB（修复前曾涨至 7.4，见 §10） |
| Rerank 精排 | **Qwen3-Reranker-0.6B** | 1.2 GB | ✅ 在线 :8002 | ~3–6 GiB（修复前峰值 16.9，见 §10） |
| 文档解析 VLM | MinerU2.5-Pro-2605-1.2B + ONNX 小模型包（PP-DocLayoutV2 + ch_PP-OCRv6 + 表格） | 2.2 GB + 819 MB | 本地已验证，备援（见 §5 阶段 7） | ~5.9 GiB |
| Embedding（备选） | BAAI/bge-m3 | 4.3 GB | 离线（回滚资产） | — |

### 4.2 推理框架与运行时（`~/venvs/vllm`，Python 3.12.3）

| 组件 | 版本 | 用途 |
|---|---|---|
| **vLLM** | 0.29.0（官方 aarch64 wheel） | 主模型推理（PagedAttention / MTP 投机 / FP8 kernel / prefix cache） |
| PyTorch | 2.13.0+cu130 | GPU 计算栈 |
| Triton | 3.7.1 | JIT kernel（GB10 SM121 实测正常） |
| Transformers | 5.17.0 | Embedding/Reranker wrapper |
| accelerate | 1.15.0 | `device_map="auto"` 加载 |
| FastAPI / uvicorn | 0.136.3 / 0.53.0 | Embedding/Reranker HTTP 服务 |

另有独立 `~/venvs/mineru`（MinerU 4.0.5 + vLLM，文档解析备援环境）。

### 4.3 应用层（`~/SparkPath-DGX-SPARK` monorepo）

| 组件 | 版本 | 职责 |
|---|---|---|
| Hermes Agent | v0.18.2 | Agent 运行时：消息路由、Skill 调度、会话、工具调用、上下文压缩（threshold 0.5 / target 0.2）+ 5 分钟 prompt 缓存 |
| ChromaDB | 1.5.9 | 向量库（HTTP 服务模式，Token 认证，唯一写入者） |
| agent4som 后端 | Python 3.12 / FastAPI | RAG 数据层 + 学业预警 + 培养方案 + 文件中心 + 审计 |
| 微信小程序 | 原生（17 页面） | 统一前端入口 |
| 三个自研 Skill | — | training-plan-interpretation / multi-path-academic-planning / academic-warning（经 NVIDIA SkillEvaluator 三级评测） |

### 4.4 网络与运维组件

| 组件 | 说明 |
|---|---|
| autossh × 2 | 反向隧道（对公网暴露 4 个端口）+ MinerU 前向隧道；`ServerAliveInterval=30` 保活、`Restart=always` |
| systemd | 9 个常驻服务 unit + 5 个 timer（另有反向隧道 unit；含 GPU 服务每日重启兜底）；失败自动重启（`Restart=on-failure` + 熔断 `StartLimitBurst=5`） |
| 切换/回滚脚本 | `cutover.sh`（--check / 切换 / --rollback）、`switch-nginx.sh`（--to-dgx / --to-vm） |
| 密钥管理 | `/etc/sparkpath/gpu-services.env`（`EnvironmentFile`，chmod 600，不入库） |

---

## 5. 调优过程（选型演进实录）

### 阶段 0：平台勘测——先摸清物理边界

部署前先测平台：LPDDR 带宽 ~273 GB/s、统一内存 128 GB（约 121 GiB 可用）、arm64。
由此确立两条硬约束：**arm64 镜像/wheel 可用性**必须逐项核实（不能假设 multiarch）；
**解码速度受带宽物理限制**，选型和调优都围绕"减少权重读取"展开。

### 阶段 1：NGC NIM 路线 → 受挫放弃（三个实测 blocker）

初版方案按 NVIDIA 官方推荐走 **NGC NIM 容器**（qwen3.8-27b NVFP4 NIM 主模型 +
bge-m3 NIM + nemotron-rerank NIM）。逐项实测后全部受阻：

| # | Blocker | 核实方式 |
|---|---|---|
| 1 | qwen3.8-27b NIM **无 arm64 manifest** | `docker manifest inspect` |
| 2 | bge-m3 NIM 是 **amd64-only** | 拉取 6.4 GB 后报 `InvalidBaseImagePlatform` |
| 3 | NGC 中国区 **451 封锁**，需 buildx 代理 workaround | 实测 |

**决策：全面切换 vLLM 0.29 原生部署 + ModelScope/HF 本地权重。**
vLLM 0.29 官方 aarch64 wheel 在 GB10 SM121 上 Triton JIT 正常、Cutlass FP8 kernel 可用，
彻底脱离 NGC 分发链路。教训：**选型结论必须以本机 arm64 实测为准，目录页标注 ≠ 可运行**。

### 阶段 2：主模型量化档位——FP8 上线，NVFP4 留作升级路径

| 方案 | 权重 | 理论解码上限 | 结论 |
|---|---|---|---|
| BF16 | ~54 GB | ~5 tok/s | 带宽浪费，弃 |
| **FP8（ModelScope 官方版）** | 29 GB | ~9.4 tok/s | ✅ **GB10 实测跑通**（CutlassFp8BlockScaledMMKernel），基线 8.7 tok/s = 带宽上限的 93% |
| NVFP4（nvidia/ HF 版） | ~14 GB | ~19 tok/s | SM121 + vLLM 0.29 的 NVFP4 kernel 未经实测，作为提速备选保留 |

### 阶段 3：MTP 投机解码——最大单点性能杠杆（+49~57%）

Qwen3.8-27B checkpoint **自带 MTP 投机层权重**（`mtp.*` 22 张量），vLLM 0.29 原生
`Qwen3_5MTP` 支持，**零额外下载、零质量损失**（投机结果经原模型 verify）。

A/B 实测（400 tok 长生成）：

| 配置 | 中文贪心 | 代码贪心 | 中文采样 (t=0.7) |
|---|---|---|---|
| 无投机（基线） | — | — | 8.4 tok/s |
| **MTP spec2（生产）** | **13.2** | **18.4** | **12.5** |
| MTP spec3 | 12.4 | 21.2 | 12.4 |

为什么选 spec2 而不是 spec3：逐位接受率实测——贪心下 draft 两位接受率 0.98/0.94，
采样下仅 49–58%；**spec3 的第 3 位在采样下接受率只有 0.14–0.23**，纯浪费 verify 算力。
RAG/Agent 低温度场景 spec2 是甜点；代码密集型可临时切 spec3。
生产满窗下 MTP 平均接受长度 **2.48 / 3**。

配套修正：`--max-num-batched-tokens 4096`（vLLM 默认 2048 会饿死 MTP verify 批，日志告警定位）。

### 阶段 4：262K 满窗——架构红利与内存账

Qwen3.8-27B 的混合注意力是长窗口的架构基础：64 层中 **48 层 GDN 线性注意力（无 KV）+
16 层全注意力**，KV/token 仅 64 KiB（同规模全注意力模型约 20 倍）。

- `--gpu-memory-utilization 0.55 → 0.75`（无桌面场景）后 KV 池 **~82 万 tokens**
  → 262K 满窗 **3.12 路并发**，或 1 路 262K + 2 路 128K 混跑
- Agent 场景的窗口经济学：系统提示 + 工具定义首轮 prefill 后被 **prefix cache** 吸收，
  每轮工具往返只新增几 K tokens——所以满窗"看起来贵、实际几乎免费"
- 满窗后解码回归 12.9 tok/s（与 32K/128K 配置持平，无惩罚）
- 长上下文质量：100K prompt 中 37.5% / 62.5% 深度 needle 检索均正确；复杂数学正确判断无解

### 阶段 5：Embedding/Reranker 兼容性替换——向量口径 1e-7 级对齐

**问题**：DGX 上原部署 bge-m3 + nemotron-rerank-1b（模型组自选），但应用从生产迁移来的
Chroma 向量库（8373 条）是 **Qwen3-Embedding-0.6B** 产的。换模型 = 检索结果错乱，重灌成本高。

**决策**：把生产 GPU 主机上**未入库的自定义 wrapper** 取回仓库（`deploy/gpu-services/`），
在 DGX 上用**完全相同的模型 + 完全相同的代码**重部署，仅改三处：权重指向本地、
CPU→GPU、监听地址。保 FP32 以对齐精度。

**本项目最大的隐性坑**：wrapper 在**服务端统一加 instruction 前缀**
（`Instruct: Given a web search query…\nQuery: …`）+ last-token pooling（左 padding）+ L2 归一化。
**少了这个前缀，即使权重逐字节相同，向量余弦也只有 ~0.88**——此前探针差 0.88–0.91 的根因。

**验收（与生产逐项比对）**：

| 项 | 结果 |
|---|---|
| Embedding 维度 / 模长 | 1024 / 1.000000 ✅ |
| 5 条文本余弦 | **1.0000000000**（max\|Δ\| ≈ 2e-7，纯 fp32 噪声）✅ |
| Rerank 排序 | 与生产完全一致（`[0,2,3,1]`）✅ |
| Rerank 分数最大差 | 1.75e-05 ✅ |

→ **8373 条存量向量 100% 复用，知识库零重灌。**

### 阶段 6：systemd 产品化——GPU 服务的三个特有坑

从 nohup 进程改为 systemd 常驻（开机自启 + 崩溃自动拉起）时踩的坑：

| 坑 | 现象 | 解法 |
|---|---|---|
| `PrivateDevices=yes` | systemd 加固默认项会重建最小 `/dev`，**不含 `/dev/nvidia*`**，CUDA 初始化失败 | GPU 服务单元**必须省略**该指令 |
| 本机代理劫持 localhost | 机器上有 mihomo（7890），127.0.0.1 调用被代理拦截 | 单元内显式 `NO_PROXY=127.0.0.1,localhost` |
| Triton JIT 编译失败 | 无 sudo 装不了 python3.12-dev，`Python.h not found` / `Require native threads` | 手工放置头文件到 venv，单元/启动脚本 `export C_INCLUDE_PATH=$HOME/venvs/vllm/include/python3.12` |

### 阶段 7：MinerU 文档解析——本地已验证，当前走隧道复用

文档解析在本地完成过全链路验证：MinerU 4.0.5 新架构（doclib 守护进程 + api-server +
vlm-server，vLLM 后端自动挂载 `MinerULogitsProcessor` 防 n-gram 重复），中文 PDF 端到端
（标题/正文/表格/公式 → Markdown，公式转 LaTeX）实测通过；1.2B 专用模型在 OmniDocBench v1.5
上 ~90-91 分，**超过 GPT-4o（~85.9）和 Qwen2.5-VL-72B（~83-85）**——文档解析必须用专用调优模型。

当前生产流量经 SSH 前向隧道复用 acc-svr 上的旧版 MinerU（`/file_parse` API），
原因：本地 MinerU 4.0.5 是新 API（`/v1/parse/jobs`），应用侧尚未改造；
本地权重（~3 GB）与 venv 完整保留，应用侧改造后可随时切回全本地。
另有 pdfminer + VL 级联兜底，解析链路无单点。

### 决策一览表

| 决策点 | 选项 | 结论 | 依据 |
|---|---|---|---|
| 推理引擎 | NGC NIM vs vLLM | **vLLM 0.29** | NIM 无 arm64 / 451，三项实测 blocker |
| 主模型 | qwen3.5-35b vs **qwen3.8-27b** vs 3.6 MoE | qwen3.8-27B dense | 同代更强（Terminal Bench 73.0 vs 63.4）、dense 27B 内存占用小 |
| 量化 | BF16 / FP8 / NVFP4 | **FP8** | GB10 实测跑通；NVFP4 留作提速路径 |
| 投机解码 | off / spec2 / spec3 | **spec2** | +49~57% 吞吐；spec3 采样接受率崩塌 |
| 上下文 | 32K / 128K / 262K | **262K 满窗** | GDN 架构 KV 便宜 + prefix cache + agent 增量上下文 |
| Embedding | bge-m3 vs qwen3-embedding | **qwen3-embedding-0.6B** | 与存量 8373 向量同源，零重灌 |
| 运行方式 | nohup vs systemd | **systemd**（9 个常驻服务 unit + 5 个 timer，另有反向隧道 unit） | 自启、崩溃拉起、日志归口 |

---

## 6. 性能基准（262K 满窗生产配置，全栈同跑实测）

### 6.1 单流解码（贪心，400-500 tok 长生成）

| 负载 | 速度 |
|---|---|
| 中文论述 | 11.0 tok/s |
| 中文散文 | 12.8 tok/s |
| 代码生成（Python） | **15.8 tok/s** |
| 英文技术 | **16.2 tok/s** |
| 英文数列 | 18.3 tok/s |

规律：结构化/英文负载比中文散文快 30–60%（MTP 在代码/英文上接受率更高）。

### 6.2 首 token 延迟（TTFT）与 Prefill

| 场景 | 结果 |
|---|---|
| 短 prompt（一句话）TTFT | 4.3 s |
| 中 prompt TTFT | 16.6 s |
| Prefill 1K tok | 0.4 s（1,113 tok/s） |
| Prefill 8K tok | 2.1 s（**1,647 tok/s**） |
| Prefill 32K tok | 9.2 s（1,563 tok/s） |
| Prefill 100K tok | 103 s（890 tok/s） |

> 注：vLLM 0.29 流式为聚合式单 chunk，上表 TTFT 含整段答案生成完成；更早 32K 配置下实测
> 真实逐 token 首包为 0.15 s。Agent 多轮场景下系统提示 prefill 被 prefix cache 吸收，
> 稳态每轮开销 ≈ 工具决策 1.66 s + 解码。

### 6.3 并发扩展（每路 200 tok 贪心）

| 并发 | 聚合吞吐 | 单路速度 | 单路衰减 |
|---|---|---|---|
| 2 路 | 24.8 tok/s | 12.7–13.6 | 无损 |
| 4 路 | 41.1 tok/s | 11.4–12.7 | -5% |
| 8 路 | **59.5 tok/s** | 10.1–12.1 | -8% |

并发红利来自统一内存 + MTP verify 批合并：8 路共享权重读取，带宽远未饱和。
**多 agent 并行优先加并发而不是提单路。**

### 6.4 Agent 关键路径

| 操作 | 延迟 |
|---|---|
| 工具调用决策（含 prefill） | **1.66 s/轮** |
| 思考模式（复杂数学，3849 字思考） | 108.6 s |

思考字数是答案的 5–10 倍，故 agent 默认 `enable_thinking: false`，难题再开
（`chat_template_kwargs` 逐请求控制，另支持 `reasoning_effort`）。

### 6.5 长上下文质量（262K 窗口内）

| 测试 | 结果 |
|---|---|
| 100K prompt needle（37.5% 深度） | ✅ 正确 |
| 62.5K 深度密钥 | ✅ 正确 |
| 复杂数学（三位数×4=反转数） | ✅ 正确判断无解 |

### 6.6 内存终态（全栈同跑）

| 进程 | 占用 |
|---|---|
| Qwen3.8-27B（:8000，含 KV 池） | 89.2 GiB |
| Qwen3-Embedding（:8001） | ~2.5 GiB（2026-09-27 修复前曾涨至 7.4，见 §10） |
| Qwen3-Reranker（:8002） | ~3–6 GiB（2026-09-27 修复前曾涨至 16.9，见 §10） |
| MinerU VLM（本地验证时） | 5.9 GiB |
| **合计** | **约 96–99 GiB / 可用约 121 GiB** |

### 6.7 主模型生产启动命令（`dgx-vllm.service`，逐字核实）

```bash
vllm serve ~/models/models/Qwen--Qwen3.8-27B-FP8/snapshots/master \
  --served-model-name qwen3.8-27b --host 0.0.0.0 --port 8000 \
  --gpu-memory-utilization 0.75 --max-model-len 262144 \
  --speculative-config '{"method":"mtp","num_speculative_tokens":2}' \
  --max-num-batched-tokens 4096 \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  --reasoning-parser qwen3
```

工具调用（OpenAI function calling、流式、多工具并行、`role:tool` 回注、思考链分离
`reasoning` 字段）均已端到端验证，是三个自研 Skill 的执行底座。

---

## 7. 可靠性与运维

| 机制 | 说明 |
|---|---|
| 进程守护 | systemd `Restart=on-failure` + `StartLimitBurst=5` 熔断；三 GPU 服务 + 六应用服务全部开机自启。**已验证**：`systemd` 自愈把 OOM 事故 RTO 压到分钟级（vs 整机重启 + 人工，见 §10.5） |
| 显存/内存兜底 | reranker/embedding 分块批处理 + `logits_to_keep=1` + `empty_cache()`，消除 CUDA allocator 内存棘轮；`dgx-gpu-services-oom-mitigation.timer` 每日 03:30 重启两服务兜底（见 §10） |
| 隧道保活 | autossh `ServerAliveInterval=30` + systemd `Restart=always`；隧道断开不影响本机内部调用，仅影响小程序入口 |
| 回滚 | 模型服务：`modelteam-services-backup.txt` 存原启动命令，bge-m3/nemotron 权重仍在盘；入口：`switch-nginx.sh --to-vm` 一条命令切回旧生产 |
| 备份 | 知识库每日 02:00 本地备份（7 天滚动）+ 异地 NAS；SQLite WAL 全量迁移时 `integrity_check` 5/5 通过 |
| 健康检查 | 12 项定时体检（网关/Chroma/磁盘/SQLite/GPU 四服务/通知新鲜度） |
| 密钥 | `/etc/sparkpath/gpu-services.env`（600，不入库）；三 GPU 服务统一 API_KEY |

**systemd 排障入口**：`systemctl status dgx-{vllm,embedding,reranker}`；
服务文档统一指向 `deploy/gpu-services/README.md`。

---

## 8. 快速复现（核心路径）

```bash
# 1. venv（aarch64 官方 wheel，无需源码编译）
python3.12 -m venv ~/venvs/vllm
~/venvs/vllm/bin/pip install vllm==0.29.0 accelerate fastapi uvicorn

# 2. 权重（ModelScope 布局落盘 ~/models/models/）
#    Qwen--Qwen3.8-27B-FP8 / Qwen3-Embedding-0.6B / Qwen3-Reranker-0.6B

# 3. GPU 服务（本仓库已含全部 unit 与 wrapper）
#    单元内使用 <DGX_USER> 占位符 → 先替换为本机用户名再安装
for s in deploy/gpu-services/systemd/dgx-*.service deploy/gpu-services/systemd/dgx-*.timer; do
  sed "s#<DGX_USER>#$USER#g" "$s" | sudo tee "/etc/systemd/system/$(basename "$s")" >/dev/null
done
sudo mkdir -p /etc/sparkpath   # 写入 API_KEY（模板见 gpu-services.env.example）
sudo systemctl daemon-reload
sudo systemctl enable --now dgx-vllm dgx-embedding dgx-reranker dgx-gpu-services-oom-mitigation.timer

# 4. 应用层（详见 DGX-SPARK-部署清单.md §4：venv ×2 → 配置 → 数据迁移 → 9 个常驻服务 unit + 5 个 timer，另有反向隧道 unit）

# 5. 验证
curl -s http://127.0.0.1:8000/v1/models        # qwen3.8-27b, max_model_len=262144
curl -s http://127.0.0.1:8001/health           # device: cuda:0
curl -s http://127.0.0.1:8007/api/v2/heartbeat # ChromaDB
bash agent4som/scripts/smoke_test.sh          # 16 项冒烟
```

---

## 9. 边界与已知取舍

| 项 | 现状 | 取舍理由 / 后续 |
|---|---|---|
| 主对话模型默认本地 `qwen3.8-27b` | VL / Step-Back / 压缩同走本地 `:8000` | `config.yaml` 默认 `provider: local`（问答内容不出校）；框架支持按需接入云端 OpenAI 兼容模型（可选） |
| MinerU 走 acc-svr 隧道 | 本地已验证、资产在盘 | 应用侧适配 v4 API（`/v1/parse/jobs`）后切全本地；pdfminer+VL 级联兜底 |
| 单流 tok/s 受 LPDDR 物理限制 | 11–18 tok/s | NVFP4 权重（14 GB，理论 ~19 tok/s）为已规划的升级路径 |
| TTFT 长 prompt 偏高 | 中 prompt 16.6 s | vLLM 0.29 流式聚合所致；升级 vLLM 版本可获真逐 token 首包 |
| bge-m3 仅 dense 模式未启用 | 离线备选 | 需要 multi-vector 检索时用 FlagEmbedding/TEI 补 |

---

## 10. 排障实录：周期性"CPU 100% 卡死"（2026-09-27）

> 现象：机器周期性卡死（整机无响应、CPU 打满），必须重启才能恢复，连续多日每天发作一次。
> 结论先行：**这不是 CPU 问题，是统一内存耗尽的死亡螺旋——CPU 100% 只是临终表现。**
> 根因是 reranker/embedding 服务的 CUDA caching allocator 高水位棘轮 + transformers
> 全位置 logits 瞬时巨张量，叠加 OOM killer 看不见 GPU 侧占用的架构盲区。

### 10.1 现象与时间线

`last reboot` 显示三连击，均为夜间/清晨低负载时段：

| 时间 | 结局 |
|---|---|
| 09-25 21:31 | OOM 雪崩（24 次 kill），重启 |
| 09-26 09:50 | 静默冻死（journal 戛然而止，连 OOM 都没来得及写），重启 |
| 09-27 09:49–09:51 | OOM 雪崩（55 次 kill），重启 |

sar 历史显示死亡前夜内存稳态 **94.85%**、可用仅 ~3 GiB、commit 280%——系统整夜在
零余量边缘运行，任何突发（登录、会话恢复、cron）都会触发雪崩。

死亡瞬间的日志特征（09-27 09:50-09:51，boot -1）：

```
kernel: NVRM: Out of memory [NV_ERR_NO_MEMORY] ... _memdescAllocInternal     ← GPU 侧先报
kernel: Out of memory: Killed process 5426 (opencode) anon-rss:8kB          ← 杀了也拿不回内存
rtkit-daemon: The canary thread is apparently starving. Taking action.      ← 每10秒一次 = CPU 100% 的真相
systemd-resolved: Under memory pressure, flushing caches.                   ← 全系统缺氧
```

被杀名单里 gnome 桌面全家桶、ibus、tracker、training-plan-api 等被反复杀→systemd 反复
拉起→再杀——**"CPU 100%"实际是 kswapd 直接回收 + swap 抖动 + OOM 杀/拉循环的总和**，
发生在内存早已耗尽之后。`opencode` 被杀时 `anon-rss: 8kB`（早被完全换出）证明 OOM
killer 在乱枪打鸟。

### 10.2 根因：三层叠加

**① 架构盲区：GPU 统一内存不进进程 RSS / cgroup。**
vLLM EngineCore 进程 RSS 仅 2.3 GiB，但 nvidia-smi 实际占 73–89 GiB。OOM killer
按 RSS 选牺牲者时根本"看不见"真正的内存大户，只能杀 oom_score_adj=200 的桌面组件。

**② 内存棘轮：reranker/embedding 的 allocator 高水位只增不减。**
`reranker_server.py` 原实现对**整个请求的所有 query-doc 对**pad 到最长序列后一次前向
（无批上限，max_length=8192）。PyTorch caching allocator 按历史峰值扩容、从不归还
OS/GPU。实测轨迹：设计文档 9-21 记录 9.8 GiB → 9-27 跑完一轮 SkillEvaluator 后
**16.9 GiB**（0.6B fp32 权重本身仅 2.4 GiB）；embedding 同理涨到 7.4 GiB。
棘轮 + 每天重启 = 用户观察到的"周期性"。

**③ 瞬时巨张量：transformers 默认计算全部位置的全词表 logits。**
reranker 只用最后一位 logits 做 yes/no 打分，但不传 `logits_to_keep` 时 transformers
为 16 批 × 8192 位置 × 152K 词表 × fp32 生成 **~10 GiB 瞬时张量**——这是压测时把
系统直接推进 OOM 雪崩的最后一根稻草（也是棘轮的主要水源）。

### 10.3 定位过程

1. `ps aux --sort=-%cpu` 找"CPU 大户" → 无异常（当时的卡死无法采样，事后负载正常）
2. **PSI（/proc/pressure）**：CPU 压力为 0，memory/io 的 full avg300=0.56 → 指向内存
3. **`last reboot` + `journalctl --list-boots`**：三天三死，都是清晨 → "周期性"确认
4. **boot -1 尾部日志**：OOM 雪崩 + rtkit starving + resolved flush → 死因是内存
5. **sar -r 历史**：死亡前夜 94.85% 稳态、3 GiB 余量、280% commit → 慢性病而非急症
6. **`nvidia-smi` vs `ps` RSS 对账**：GPU 97.6 GiB vs RSS ~13 GiB → 架构盲区实锤
7. **reranker GPU 占用 16.9 GiB / 权重仅 2.4 GiB** → 棘轮定位到 `compute_scores()`
8. **读代码**：无批上限整批前向 + 无 `logits_to_keep` + 无 `empty_cache` → 三处缺陷
9. **复现实验（受控压测）**：8×60 长文档请求在仅分块修复下仍触发 NVRM OOM →
   证明瞬时 logits 张量是独立杀手，必须 `logits_to_keep=1` 根治

### 10.4 修复（三层防御）

| 层 | 修复 | 文件 |
|---|---|---|
| 代码：分块批处理 | `compute_scores()` 按 `batch_size=16` 分块（对齐 embedding 的既有模式），批间 `del` 临时张量、批后 `torch.cuda.empty_cache()` | `deploy/gpu-services/reranker_server.py` |
| 代码：掐掉巨张量 | 前向传 `"logits_to_keep": 1`——只算最后一位的 logits（打分只用它），消灭 ~10 GiB 瞬时张量 | 同上 |
| 代码：embedding 同治 | 批处理已有（batch_size=16），补 `del` + `empty_cache()` 归还 | `deploy/gpu-services/embedding_server.py` |
| 兜底：夜间定时重启 | `dgx-gpu-services-oom-mitigation.timer`：每日 03:30 restart embedding+reranker（落在 02:00 备份与 04:00 通知同步的空档），防未知增长路径 | `deploy/gpu-services/systemd/` |

### 10.5 验证

| 项 | 结果 |
|---|---|
| 打分一致性（vs 修复前基线，4doc/20doc 两组） | 排序完全一致；最大分数差 6.4e-06 ~ 7.7e-06（fp32 噪声级，与既有验收口径 1.75e-05 同量级）✅ |
| reranker GPU 占用 | 16.9 GiB → **~3 GiB 稳态**（压测后不增长）✅ |
| embedding GPU 占用 | 7.4 GiB → **2.5 GiB 稳态** ✅ |
| 受控压测（3 轮 × 30 长文档 + 后续多轮） | 无 NVRM 报错、无 OOM、内存钉在稳态 ✅ |
| systemd 自愈 | 19:14 压力触发的三服务 OOM 死亡全部被 `Restart=on-failure` 自动拉起（vLLM 冷启 8 分钟内恢复服务）✅ |
| 定时器 | `dgx-gpu-services-oom-mitigation.timer` 已启用，次日 03:30 首触发 ✅ |

### 10.6 经验教训（写进部署 DNA）

1. **统一内存平台必须用 `nvidia-smi` 对账，不能只看 RSS/cgroup**——OOM killer 是瞎的
2. **任何"整批一次前向"的 transformers 服务都是延迟炸弹**：批大小由调用方决定
   = 内存上限由最坏调用方决定
3. **打分/池化类服务永远传 `logits_to_keep`**（或只取所需位置），全词表 logits 是
   152K × 序列长 × 4B 的纯浪费
4. **"CPU 100%"在统一内存机器上先查 PSI 和内存**，CPU 只是症状
5. **systemd `Restart=on-failure` 在这次事故中把 RTO 压到分钟级**（vs 整机重启
   vLLM 冷启 ~8 分钟 + 人工介入），值得写进可靠性章节（§7）作为已验证事实

---

*文档生成：2026-09-27。性能数据实测于 2026-09-21（262K 满窗生产配置、全栈同跑），
服务状态核实于 2026-09-27（`systemctl` / `ss -tlnp` / `/v1/models`）。*
