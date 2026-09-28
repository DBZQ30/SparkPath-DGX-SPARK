"""设备算力指标接口（/api/metrics/gpu）单元测试。

不触发真实 `nvidia-smi` / 网络：外部依赖一律 monkeypatch。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import gpu_metrics

_EMPTY_ENGINE = {"running": None, "waiting": None, "kv_cache": None}


@pytest.fixture(autouse=True)
def _reset_state():
    gpu_metrics._state["cache"] = None
    gpu_metrics._state["tps"] = None
    yield
    gpu_metrics._state["cache"] = None
    gpu_metrics._state["tps"] = None


def _fake_urlopen(holder):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return holder["text"].encode("utf-8")

    return lambda url, timeout=0: _Resp()


def test_tps_is_counter_delta_not_invented(monkeypatch):
    """吞吐必须来自 generation_tokens_total 的真实增量；首次无速率 → None。"""
    holder = {"text": ""}
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(holder))

    holder["text"] = (
        'vllm:num_requests_running{engine="0"} 2.0\n'
        'vllm:num_requests_waiting{engine="0"} 1.0\n'
        'vllm:kv_cache_usage_perc{engine="0"} 0.42\n'
        'vllm:generation_tokens_total{engine="0"} 1000.0\n'
    )
    engine, tps = gpu_metrics._read_vllm_metrics(100.0)
    assert (engine["running"], engine["waiting"], engine["kv_cache"]) == (2.0, 1.0, 0.42)
    assert tps is None                    # 单次采样得不到速率

    holder["text"] = 'vllm:generation_tokens_total{engine="0"} 1200.0\n'
    _, tps = gpu_metrics._read_vllm_metrics(102.0)
    assert tps == 100.0                   # (1200-1000) / 2s

    holder["text"] = 'vllm:generation_tokens_total{engine="0"} 1200.0\n'
    _, tps = gpu_metrics._read_vllm_metrics(104.0)
    assert tps == 0.0                     # 计数未变 → 空闲，而不是编一个数字


def test_vllm_unreachable_degrades_to_none(monkeypatch):
    def boom(url, timeout=0):
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    engine, tps = gpu_metrics._read_vllm_metrics(1.0)
    assert engine == _EMPTY_ENGINE
    assert tps is None


def test_device_sample_is_cached_within_ttl(monkeypatch):
    calls = {"smi": 0}

    def fake_smi():
        calls["smi"] += 1
        return {"util": 1.0, "temp_c": 40.0, "power_w": 10.0, "clock_mhz": 2400.0}

    monkeypatch.setattr(gpu_metrics, "_read_nvidia_smi", fake_smi)
    monkeypatch.setattr(
        gpu_metrics, "_read_unified_memory",
        lambda: {"total_mb": 128000, "used_mb": 40000, "note": "unified"},
    )
    monkeypatch.setattr(gpu_metrics, "_read_vllm_metrics", lambda now: (dict(_EMPTY_ENGINE), None))

    first = gpu_metrics.sample_device_metrics()
    second = gpu_metrics.sample_device_metrics()

    assert calls["smi"] == 1               # 第二次命中 TTL 缓存
    assert first is second
    assert first["mem"]["note"] == "unified"


def test_read_number_tolerates_nvidia_smi_na():
    """GB10 上部分字段是 [N/A]：必须降级为 None，不能抛错或变 0。"""
    assert gpu_metrics._read_number("12.5") == 12.5
    assert gpu_metrics._read_number(" [N/A] ") is None
    assert gpu_metrics._read_number("") is None
    assert gpu_metrics._read_number(None) is None


def test_endpoint_requires_api_key(monkeypatch):
    app = FastAPI()
    app.include_router(gpu_metrics.router)
    monkeypatch.setattr(gpu_metrics, "_API_KEY", "test-key")
    monkeypatch.setattr(gpu_metrics, "sample_device_metrics",
                        lambda: {"ts": 1, "gpu": {"util": 7.0}, "tps": None})

    client = TestClient(app)
    assert client.get("/api/metrics/gpu").status_code == 401
    assert client.get("/api/metrics/gpu", headers={"X-API-Key": "wrong"}).status_code == 401

    ok = client.get("/api/metrics/gpu", headers={"X-API-Key": "test-key"})
    assert ok.status_code == 200
    assert ok.json()["gpu"]["util"] == 7.0
