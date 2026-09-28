#!/bin/bash
# Agent for SOM — 临时文件清理脚本
# 清理 data/ 下一次性操作遗留的临时文件（JSON/JSONL 报告、备份、RAGAS 结果等）
# 不触碰 chroma/、quota.db、audit.db、jxtz_notices.jsonl 等运行时数据。
#
# 部署: sudo cp infra/temp-cleanup.service /etc/systemd/system/
#       sudo cp infra/temp-cleanup.timer /etc/systemd/system/
#       sudo systemctl enable --now temp-cleanup.timer
set -euo pipefail

DATA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/data"
KEEP_DAYS=7

echo "[$(date)] 开始清理 data/ 临时文件..."

cleaned=0

# 1. 批量摄入结果报告（一次性产物，保留 N 天）
for pattern in "ingest_success_*.json" "ingest_failed_*.json"; do
    for f in "$DATA_DIR"/$pattern; do
        if [ -f "$f" ]; then
            # 检查文件年龄
            if [ "$(find "$f" -mtime +"$KEEP_DAYS" 2>/dev/null)" ]; then
                echo "  删除: $(basename "$f")"
                rm -f "$f"
                cleaned=$((cleaned + 1))
            fi
        fi
    done
done

# 2. JSONL 分类中间产物（数据清洗后遗留）
for name in excluded_notices.jsonl kept_notices.jsonl to_delete.jsonl to_keep.jsonl; do
    f="$DATA_DIR/$name"
    if [ -f "$f" ]; then
        echo "  删除: $name"
        rm -f "$f"
        cleaned=$((cleaned + 1))
    fi
done

# 3. RAGAS 评估结果（可重新生成）
for name in ragas_detail.json ragas_scores.json; do
    f="$DATA_DIR/$name"
    if [ -f "$f" ]; then
        echo "  删除: $name"
        rm -f "$f"
        cleaned=$((cleaned + 1))
    fi
done

# 4. JSONL 备份文件（jxtz_notices.jsonl.bak）
for f in "$DATA_DIR"/*.bak; do
    if [ -f "$f" ]; then
        # 保留 7 天内修改过的 bak 文件
        if [ "$(find "$f" -mtime +"$KEEP_DAYS" 2>/dev/null)" ]; then
            echo "  删除: $(basename "$f")"
            rm -f "$f"
            cleaned=$((cleaned + 1))
        fi
    fi
done

# 5. jxtz_tmp 目录中超过 1 天的临时文件
if [ -d "$DATA_DIR/jxtz_tmp" ]; then
    count=$(find "$DATA_DIR/jxtz_tmp" -type f -mtime +1 2>/dev/null | wc -l)
    if [ "$count" -gt 0 ]; then
        echo "  清理 jxtz_tmp/: $count 个过期临时文件"
        find "$DATA_DIR/jxtz_tmp" -type f -mtime +1 -delete
        cleaned=$((cleaned + count))
    fi
fi

echo "[$(date)] 清理完成: $cleaned 个文件"
