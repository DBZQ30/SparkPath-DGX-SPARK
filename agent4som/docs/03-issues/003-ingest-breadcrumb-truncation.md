---
severity: high
date: 2026-05-11
file: knowledge-base/scripts/ingest_v2.py  # 历史模块，已移除
status: done
related_features:
  - FEAT-010 (RAG知识库系统)
  - FEAT-011 (本科教务知识库)
---

# ingest_v2 chunk 切分 breadcrumb 残句 + 粒度过细

## 问题发现

用户查询本科知识库时，检索结果breadcrumb显示断句错误：

```
[育"的人才培养模式改革，结合我校实际，制订本方案。 > 一、意义与要求]
```

"教育"被截断成"育"，breadcrumb取的是上一段落末尾的残句而非章节标题。

## 根因分析

### Bug 1: breadcrumb取段落残句（已修复）

**影响范围：** 330/869 (38%) 的chunks breadcrumb含残句

**原因：** 旧版`_md_to_chunks()`逻辑中，当遇到`## 一、意义与要求`触发flush时，`article_body`里积攒的前言段落（标题与章节之间的正文）被append进了chunk的body，而body的最后一行文本被错误地作为breadcrumb的前缀拼接。

**MinerU返回的Markdown是正确的：**
```markdown
# 西安交通大学基础通识类课程建设方案

...支撑"通识教育+宽口径专业教育"的人才培养模式改革，结合我校实
际，制订本方案。

## 一、意义与要求
```

**旧版代码处理流程：**
1. `# ` → `chapter = "西安交通大学基础通识类课程建设方案"`
2. 前言段落 → `article_body.append("...制订本方案。")`
3. `## 一、意义与要求` → flush → breadcrumb取了article_body尾部残句

**当前版代码已修复：** breadcrumb正确取chapter/section标题 → `西安交通大学基础通识类课程建设方案 > 一、意义与要求`

### Bug 2: chunk切分粒度过细——按"条"逐条切（旧版行为）

**同一PDF的chunks对比：**

| 版本 | chunks数 | 粒度 | breadcrumb质量 | 每chunk字数 |
|------|----------|------|---------------|------------|
| 旧版 | 21 | 按条(1. 2. 3.…) | 残句截断 | 30-50 |
| 当前版 | 8 | 按section(一、二、三…) | 正确标题 | 80-200 |

旧版把每个"条"切成独立chunk，导致：
- 同一节下的条目丧失上下文关联
- 每个chunk仅30-50字，语义信息不足
- 检索时召回碎片化，无法理解完整语义

### Bug 3: ingest不支持增量写入

`ingest_directory()`在第1403行才一次性`json.dump`写出，中途崩溃则丢失所有进度。
98个文件+MinerU+VL调用约需10-15分钟，期间的任何中断（网络超时、VL 400错误、进程kill）都会导致从头再来。

## 修复措施

### 已完成
- [x] `_md_to_chunks()` breadcrumb逻辑修复（当前版代码已正确）
- [x] 切分粒度从"条"调整为"节(section)"级别（当前版已生效）
- [x] 用当前版ingest_v2.py（历史模块，已移除）重新生成undergraduate_chunks.json — 产出1167个chunks（旧869）
- [x] 重新入库到ChromaDB kb_undergraduate集合 — 1167条记录

### 待完成
- [ ] 增量写入支持：每处理完一个文件就append到json，避免全量丢失
- [ ] VL描述失败时skip而非阻塞整个ingest流程
- [ ] 回归验证：330/869残句率降至0

## 验证方法

```bash
# 重新ingest后验证
python3 -c "
import json
with open('data/undergraduate_chunks.json') as f:
    data = json.load(f)
problems = 0
for c in data['chunks']:
    bc = c.get('breadcrumb', '')
    first_part = bc.split(' > ')[0]
    if any(ch in first_part for ch in '。，、；：！？'):
        problems += 1
print(f'Breadcrumb残句: {problems}/{len(data[\"chunks\"])}')
"
```

## 教训

1. **breadcrumb必须取结构标题而非正文片段** — 结构感知分块的核心是"结构"，breadcrumb应仅由heading层级标题组成
2. **chunk粒度以section节为最佳** — 过细(条)丧失上下文，过粗(全文)损失精度
3. **长时任务必须支持增量持久化** — 文档摄入涉及外部API调用（MinerU/VL），网络不稳定时全量重跑代价大
4. **ingest结果必须验证才能入库** — chunks.json生成后应检查breadcrumb质量、chunk粒度分布，避免脏数据污染向量库
