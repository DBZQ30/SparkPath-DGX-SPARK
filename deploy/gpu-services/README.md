# GPU 模型服务（从生产 GPU 主机 acc-svr 取回）

> 来源：**acc-svr = `<GPU_HOST_IP>`**（XJTU 侧跳板机/GPU 主机）上的 `/usr/local/bin/`
> 取回日期：2026-09-21
> 为什么一开始不在仓库里：这三个子项目是从 **acc-helper-vm** 的 `/home/<DEPLOY_USER>/H-agent/`
> 迁移过来的，而这两个包装器属于 **GPU 主机**的运维层，从未纳入任何仓库（文件头注明
> `Based on bigtt's embedding_server_cpu.py`，由模型/运维组手工部署）。
>
> **本文档聚焦生产 acc-svr 的 wrapper，以及 dgx 上 Embedding / Reranker 的等价复刻；
> dgx 主模型（Qwen3.8-27B-FP8 + vLLM 0.29）与整机模型栈的选型、性能与部署见
> [`../../docs/DGX-SPARK-模型部署方案.md`](../../docs/DGX-SPARK-模型部署方案.md)。**

---

## 1. 生产实际运行清单（实测）

| 端口 | systemd 单元 | 实现 | 模型权重 |
|---|---|---|---|
| `:8001` | `embedding.service` | `embedding_server.py`（transformers + FastAPI，**FP32 / CPU**） | `models--Qwen--Qwen3-Embedding-0.6B` |
| `:8002` | `reranker.service` | `reranker_server.py`（transformers + FastAPI，**FP32 / CPU**） | `models--Qwen--Qwen3-Reranker-0.6B` |
| `:8006` | `vllm.service` | `start-vllm.sh` → vLLM | `models--cyankiwi--Qwen3-VL-8B-Instruct-AWQ-4bit`（服务名 `qwen3-vl`，需 `--api-key`） |
| `:8005` | `mineru.service` | `start-mineru.sh` → `mineru.cli.fast_api`（**旧版 API，有 `/file_parse`**） | MinerU |

统一 API Key：由环境变量 `API_KEY` 提供（**不入库**）。
- systemd 单元通过 `EnvironmentFile=-/etc/sparkpath/gpu-services.env` 读取（模板见 `gpu-services.env.example`）；
- `ref/start-*.sh` 要求调用方预先 `export API_KEY`（或 `MINERU_API_KEY`），未设置会直接报错退出。

---

## 2. 这两个包装器最关键的实现细节（**必须原样保留**）

### 2.1 `embedding_server.py` —— 服务端统一加 instruction 前缀

```python
task_desc = "Given a web search query, retrieve relevant passages that answer the query"
texts = [get_detailed_instruct(task_desc, t) for t in texts]   # 即 f"Instruct: {task_desc}\nQuery: {t}"
```

- **查询和文档都加同一前缀**（不区分）
- 分词：`padding_side="left"`、`truncation=True`、`max_length=512`
- 池化：**last-token pooling**（左 padding 时取末位，否则按 attention_mask 取最后一个有效位）
- 归一化：`torch.nn.functional.normalize(p=2, dim=1)` → 输出模长 1.0
- 输出维度 **1024**

> ⚠️ 该前缀是**向量兼容性的关键**。少了它，即使模型权重完全相同，向量余弦也只有 ~0.88。

### 2.2 `reranker_server.py` —— 打分公式

```python
pair = f"<Instruct>: {instruction}\n<Query>: {query}\n<Document>: {doc}"
# instruction 默认 "Given a web search query, retrieve relevant passages that answer the query"
# 输入构造：prefix_tokens = [bos] + encode("yes/no") ; suffix_tokens = [eos]
# 取最后一位 logits，对 [no, yes] 做 log_softmax，score = exp(logprob_yes)
```

按 `relevance_score` 降序排序，`top_n` 截断。

> **内存安全（2026-09-27 修复）**：`compute_scores()` 改为按 `batch_size=16` **分块前向**，并传入
> `logits_to_keep=1`（只算末位 logits——打分只用它，消灭「全词表 × 全序列」的 ~10 GiB 瞬时张量），
> 批间 `del`、批后 `torch.cuda.empty_cache()`；embedding 侧同样补 `del + empty_cache()`。
> 由此消除 CUDA caching allocator 的**内存棘轮**（reranker 峰值 16.9→~3 GiB、embedding 7.4→~2.5 GiB），
> 根治周期性整机 OOM。完整排障见 [`../../docs/DGX-SPARK-模型部署方案.md`](../../docs/DGX-SPARK-模型部署方案.md) §10。

---

## 3. 本仓库对该脚本做的**最小改动**

以下是本仓库相对生产的改动（保留生产语义，打分/归一化结果与生产一致）：

| 文件 | 改动 | 原因 |
|---|---|---|
| `embedding_server.py` | 新增 `HOST = os.getenv("HOST", "<GPU_HOST_IP>")`，`uvicorn.run` 用它 | 生产绑 `<GPU_HOST_IP>`；dgx 上需绑 `127.0.0.1`，用环境变量传入而不改代码 |
| `embedding_server.py` | `/health` 的 `device` 字段改为反映实际设备（原为写死的 `"cpu"`） | 原实现在 GPU 上也报 `cpu`，会误导排障 |
| `embedding_server.py` | 批处理后补 `del` + `torch.cuda.empty_cache()` | 统一内存平台 allocator 高水位只增不减（曾涨至 7.4 GiB），批后归还临时块 |
| `reranker_server.py` | 新增 `HOST = os.getenv("HOST", "<GPU_HOST_IP>")`，`uvicorn.run` 用它 | 同上 |
| `reranker_server.py` | `compute_scores()` 分块批（`batch_size=16`）+ `logits_to_keep=1` + `empty_cache()` | 消除内存棘轮与 ~10 GiB 瞬时 logits 张量（曾 16.9 GiB 诱发整机 OOM），见 §2.2 与模型部署方案 §10 |

---

## 4. 目录说明

```
deploy/gpu-services/
├── README.md                    本文件
├── embedding_server.py          :8001 向量服务（含 instruction 前缀逻辑）
├── reranker_server.py           :8002 重排服务
├── systemd/
│   ├── embedding.service        生产 acc-svr 原样（供比对）
│   ├── reranker.service         生产 acc-svr 原样（供比对）
│   ├── vllm.service             生产 acc-svr 原样（VL 8B，供比对）
│   ├── mineru.service           生产 acc-svr 原样（供比对）
│   ├── dgx-embedding.service    dgx 版（走 GPU + 本地权重）
│   ├── dgx-reranker.service     dgx 版（走 GPU + 本地权重）
│   ├── dgx-vllm.service         dgx 版主模型（Qwen3.8-27B-FP8，:8000）
│   ├── dgx-gpu-services-oom-mitigation.service  每日 03:30 重启 embedding/reranker 兜底
│   └── dgx-gpu-services-oom-mitigation.timer    上者的定时器
└── ref/                         生产 vLLM/MinerU 启动脚本（仅供参考，未用于 dgx）
    ├── start-embedding.sh
    ├── start-reranker.sh
    ├── start-vllm.sh
    └── start-mineru.sh
```

> `ref/start-embedding.sh` 与 `ref/start-reranker.sh` 是用 **vLLM** 起同样模型的**另一条路径**，
> 生产上**并未使用**（实际生效的是上面的 CPU FastAPI 包装器）。保留仅为存档。

---

## 5. dgx 上的部署方式（与生产的差异）

| 项 | 生产 acc-svr | dgx |
|---|---|---|
| 权重路径 | `/home/models/models--Qwen--…` | `/home/<DGX_USER>/models/models/Qwen3-Embedding-0.6B`、`Qwen3-Reranker-0.6B`（权重 sha256 已验证与生产一致） |
| 运行设备 | CPU（`CUDA_VISIBLE_DEVICES=` 置空） | **GPU**（放开 `CUDA_VISIBLE_DEVICES`，保 `torch_dtype=float32` 以对齐精度） |
| 监听 | `<GPU_HOST_IP>:8001/8002` | `127.0.0.1:8001/8002`（`HOST` 环境变量） |
| Python 环境 | conda `ai`（python3.11） | `/home/<DGX_USER>/venvs/vllm`（torch 2.13.0+cu130，sm121 可用）；需额外 `pip install accelerate`（脚本用了 `device_map="auto"`） |
| 归一/打分逻辑 | —— | **完全一致**（同代码；打分排序与生产一致） |
| 内存行为 | —— | 分块批 + `logits_to_keep=1` + `empty_cache()`，稳态不增长（reranker ~3 GiB / embedding ~2.5 GiB）；`dgx-gpu-services-oom-mitigation.timer` 每日 03:30 兜底重启（见模型部署方案 §10） |

### 验收标准（已实测）

与生产对同一文本取向量，余弦相似度 ≥ 0.9999；rerank 分数与生产排序一致。
（阶段 1 用 bf16-vLLM 近似实现时 cos=0.9998；改用本包装器 FP32 后应更高。）

### ⚠️ systemd 单元注意：**不要加 `PrivateDevices=yes`**

`PrivateDevices=yes` 会重建一个只含 `null/zero/random/tty` 等最小设备的私有 `/dev`，
**不含 `/dev/nvidia*`**，导致 CUDA 初始化失败。GPU 服务必须省略该指令
（本目录 `dgx-*.service` 已按此处理；`dgx-embedding.service` 实测 `device: cuda:0` 正常）。
