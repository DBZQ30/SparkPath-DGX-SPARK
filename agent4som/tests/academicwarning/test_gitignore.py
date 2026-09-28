"""B2：academicwarning/ 代码可提交、样本文件仍被忽略。"""
import subprocess


def _check_ignore(path: str) -> bool:
    r = subprocess.run(["git", "check-ignore", path], capture_output=True,
                       check=False)  # 退出码 1 = 未被忽略，属正常判定分支
    return r.returncode == 0


def test_code_not_ignored():
    assert not _check_ignore("academicwarning/models.py")
    assert not _check_ignore("academicwarning/parsers.py")


def test_samples_still_ignored():
    # 2026-09-06 样本移入 academicwarning/docs/（整目录忽略）；根级报告 xlsx 由
    # academicwarning/*.xlsx 规则忽略（export 落盘目录不变）；代码不忽略见上
    assert _check_ignore("academicwarning/docs/2023版工商管理专业培养方案.docx")
    assert _check_ignore("academicwarning/docs/2023级26-27学年第一学期的选课结果.xlsx")
    assert _check_ignore("academicwarning/docs/2023级学籍信息.xls")
    assert _check_ignore("academicwarning/result/选课检查名单-2023级-20260906-000000.xlsx")
