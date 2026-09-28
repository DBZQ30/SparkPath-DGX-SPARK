"""设备算力指标 HTTP API（FastAPI APIRouter）——执行轨迹遥测条的数据源。

挂载在培养方案解读/学业规划服务（:8009）：`/api/metrics/gpu`。
鉴权沿用 `X-API-Key`（`WARNING_API_KEY` / `TRAINING_PLAN_API_KEY`）。

**为什么在这里、而不是 hermes 网关插件里**：网关的 systemd 单元设了
`PrivateDevices=yes`，其 `/dev` 里没有 nvidia 设备节点，`nvidia-smi` 在网关内
必然失败（实测 `/proc/<gw>/root/dev` 下 nvidia 设备数为 0）。本服务可见设备
（6 个），且已经通过 `/accapi/dgx-plan` 对小程序暴露。

指标全部来自真实来源；**取不到的字段一律 null，绝不造数**：
  · GPU util / temp / power / clock → `nvidia-smi --query-gpu`
  · 显存 → `/proc/meminfo`（GB10 是统一内存，nvidia-smi 的 Memory-Usage 不支持）
  · 引擎态 → vLLM `/metrics`（running / waiting / kv_cache）
  · 吞吐 → `vllm:generation_tokens_total` 的**两次采样增量**（计数器；单次采样
    无法得到速率，首次调用返回 null）
"""
from __future__ import annotations

import os
import subprocess
import time
import urllib.request
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException

_API_KEY = os.environ.get("WARNING_API_KEY") or os.environ.get("TRAINING_PLAN_API_KEY", "")
_VLLM_METRICS_URL = os.environ.get("VLLM_METRICS_URL", "http://127.0.0.1:8000/metrics")
_SMI_TIMEOUT_S = 5          # GPU 繁忙时 nvidia-smi 会变慢，留足余量
_SAMPLE_TTL_S = 0.8         # 同一时刻的多次拉取共用一次采样，避免反复 fork

router = APIRouter(prefix="/api/metrics", tags=["metrics"])

# 进程内采样状态：{cache: {at, data}, tps: (at, tokens)}
_state: dict[str, Any] = {"cache": None, "tps": None}


def _check_key(x_api_key: Optional[str]) -> None:
    if not _API_KEY or x_api_key != _API_KEY:
        raise HTTPException(status_code=401, detail="invalid api key")


def _read_number(value: Any) -> Optional[float]:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _read_nvidia_smi() -> dict[str, Any]:
    empty = {"util": None, "temp_c": None, "power_w": None, "clock_mhz": None}
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=utilization.gpu,temperature.gpu,power.draw,clocks.sm",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=_SMI_TIMEOUT_S,
            check=True,  # 非零退出码按故障处理（except 兜底返回空指标）
        ).stdout.strip().splitlines()
        if not out:
            return empty
        util, temp, power, clock = (out[0].split(",") + [""] * 4)[:4]
        return {
            "util": _read_number(util),
            "temp_c": _read_number(temp),
            "power_w": _read_number(power),
            "clock_mhz": _read_number(clock),
        }
    except Exception:
        return empty


def _read_unified_memory() -> dict[str, Any]:
    """GB10 统一内存：没有独立显存，用系统内存表示。"""
    try:
        info: dict[str, str] = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                parts = rest.strip().split()
                if parts:
                    info[key.strip()] = parts[0]
        total_mb = int(info.get("MemTotal", "0")) // 1024
        avail_mb = int(info.get("MemAvailable", "0")) // 1024
        return {"total_mb": total_mb, "used_mb": max(0, total_mb - avail_mb), "note": "unified"}
    except Exception:
        return {"total_mb": None, "used_mb": None, "note": "unified"}


def _read_vllm_metrics(now: float) -> tuple[dict[str, Any], Optional[float]]:
    engine: dict[str, Any] = {"running": None, "waiting": None, "kv_cache": None}
    tokens: Optional[float] = None
    try:
        with urllib.request.urlopen(_VLLM_METRICS_URL, timeout=3) as resp:
            text = resp.read().decode("utf-8", "replace")
        for raw in text.splitlines():
            if not raw or raw.startswith("#"):
                continue
            name, _, value = raw.partition(" ")
            name = name.split("{", 1)[0].strip()
            number = _read_number(value)
            if number is None:
                continue
            if name == "vllm:num_requests_running":
                engine["running"] = number
            elif name == "vllm:num_requests_waiting":
                engine["waiting"] = number
            elif name == "vllm:kv_cache_usage_perc":
                engine["kv_cache"] = round(number, 4)
            elif name == "vllm:generation_tokens_total":
                tokens = number
    except Exception:
        pass

    tps: Optional[float] = None
    previous = _state.get("tps")
    if tokens is not None:
        _state["tps"] = (now, tokens)
        if previous is not None:
            dt = now - previous[0]
            if dt >= 0.3 and tokens >= previous[1]:
                tps = round((tokens - previous[1]) / dt, 1)
    return engine, tps


def sample_device_metrics() -> dict[str, Any]:
    now = time.monotonic()
    cached = _state.get("cache")
    if cached and now - cached["at"] < _SAMPLE_TTL_S:
        return cached["data"]

    engine, tps = _read_vllm_metrics(now)
    data = {
        "ts": int(time.time() * 1000),
        "gpu": _read_nvidia_smi(),
        "mem": _read_unified_memory(),
        "engine": engine,
        "tps": tps,
    }
    _state["cache"] = {"at": now, "data": data}
    return data


@router.get("/gpu")
def gpu_metrics(x_api_key: Optional[str] = Header(None)) -> dict[str, Any]:
    """执行轨迹遥测：GPU / 统一内存 / 引擎态 / 吞吐。"""
    _check_key(x_api_key)
    return sample_device_metrics()
