---
severity: high
date: 2026-05-09
file: knowledge-base/scripts/ingest_v2.py  # 历史模块，已移除
status: fixed
---

# body-only DOCX 文档内容全部丢失

## 问题描述

通知/说明类DOCX文档无标题样式（Heading 1/2/3），正文也不匹配正则（如"第X章"），所有段落被识别为body级别。`_build_chunks()`的flush逻辑中`if flush_title:`在该条件下为False，导致body内容在flush时被全部丢弃。

实测15/37个文档因此出现"空文件，跳过"。

## 根因

`ingest_v2.py`（历史模块，已移除）中`_build_chunks()`方法：
```python
if flush_title:
    chunks.append(...)
```

当文档既无chapter也无section时，`flush_title`始终为None/False，body段落在轮转时被静默丢弃。

## 修复

```python
flush_title = section or chapter or self.doc_name
```

以文件名兜底，确保即使无任何标题层级，body内容也能以文件名为标题正常入库。

## 验证

- 原15个空文件重新摄入后全部产生有效chunks
- 有标题的文档不受影响
