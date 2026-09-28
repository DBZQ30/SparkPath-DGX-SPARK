---
severity: high
date: 2026-05-09
file: gateway/platforms/base.py
status: fixed
---

# GLM-5 MEDIA: 路径与中文文本粘连导致文件发送失败

## 问题描述

GLM-5模型生成`MEDIA:`标签时，将中文文本直接拼接在文件路径后，无换行/空格分割：

```
MEDIA:/path/to/文件.docx以上内容已完整。议程文档已发送
```

`extract_media()`的正则将`.docx以上内容已完整...`整体匹配为路径，导致`os.path.isfile()`失败，文件无法发送。

## 影响范围

- 所有CJK语言模型（GLM-5、Qwen等）通过gateway向WeCom/Weixin发送文件时均受影响
- 英语模型无此问题（英文文本前必有空格）
- errors.log中记录："Failed to send media (.docx以上内容...): Media file not found: /path/to/文件.docx以上内容..."

## 根因

`base.py`第1960行`extract_media()`正则的lookahead边界仅包含ASCII空白和标点：

```python
(?=[\s`"',;:)\]}]|$)
```

中文/日文/韩文字符不在此范围内，正则继续贪婪匹配，将中文文本吞入路径。

## 修复

1. **`extract_media()`正则**（第1959-1964行）：在lookahead中增加CJK字符范围作为边界标记：
   ```python
   _CJK_BOUNDARY = r'[\u3000-\u303f\uff00-\uffef\u4e00-\u9fff\uac00-\ud7af]'
   (?=[\s`"',;:)\]}]|''' + _CJK_BOUNDARY + r'''|$)
   ```

2. **response文本清理正则**（第2899行）：同步增加对粘连中文的剥离，防止用户收到残留的文件路径文本。

## 验证

- 正则单元测试通过：`.docx中文`→正确截断为`.docx`
- 英文空格分隔、正常换行等既有场景不受影响
- 网关编译通过，已重启

## 次生问题：execute_code沙箱文件未持久化

agent在execute_code沙箱中生成docx/xlsx文件后，文件可能仅存在于临时目录，未写入`cache/documents/`。agent误报"已发送完毕"但文件不在磁盘上。

- 状态：未修复（需agent侧改进：execute_code生成文件后应显式copy到cache/documents/）
- Workaround：重新生成文件并确保写入cache/documents/
