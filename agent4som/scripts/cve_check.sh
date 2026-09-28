#!/bin/bash
# Agent for SOM — CVE 漏洞扫描
# 扫描项目依赖中的已知安全漏洞。
# 用法: bash scripts/cve_check.sh
set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== CVE Scan: requirements.lock ==="
source venv/bin/activate
pip-audit -r requirements.lock --strict

echo ""
echo "=== CVE Scan: 当前 venv ==="
pip-audit --strict
