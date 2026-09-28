"""academicwarning 测试隔离（2026-09-06）。

run_selection_check 会自动导出检查报告 xlsx（build_report_xlsx 输出到模块常量
OUT_DIR = academicwarning/ 生产目录）——测试跑 run 会把测试产物写进生产目录
（曾出现 2024 级/无年级空报告堆积）。本 fixture 全局把 OUT_DIR 指到 tmp，
生产目录只留真实运行产物。
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_report_dir(tmp_path, monkeypatch):
    import academicwarning.export as ex
    monkeypatch.setattr(ex, "OUT_DIR", str(tmp_path / "reports"))
