import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

import app.main as main


def test_concurrency_environment_limits_fail_closed(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_SLICES", "0")
    with pytest.raises(RuntimeError, match="between 1 and 4"):
        main._bounded_concurrency("MAX_CONCURRENT_SLICES", 1)
    monkeypatch.setenv("MAX_CONCURRENT_SLICES", "5")
    with pytest.raises(RuntimeError, match="between 1 and 4"):
        main._bounded_concurrency("MAX_CONCURRENT_SLICES", 1)


def test_capacity_gate_returns_retryable_429_instead_of_unbounded_queue(monkeypatch):
    monkeypatch.setattr(main, "RESOURCE_CAPACITY_WAIT_SECONDS", 0.01)

    async def exercise():
        semaphore = asyncio.Semaphore(0)
        with pytest.raises(HTTPException) as exc_info:
            await main._acquire_capacity(semaphore, "test workload")
        assert exc_info.value.status_code == 429
        assert exc_info.value.headers == {"Retry-After": "2"}

    asyncio.run(exercise())


def test_health_exposes_resource_caps():
    payload = main.health()
    assert payload["resource_limits"]["max_concurrent_slices"] == main.MAX_CONCURRENT_SLICES
    assert payload["resource_limits"]["max_concurrent_proxy_jobs"] == main.MAX_CONCURRENT_PROXY_JOBS
    assert payload["resource_limits"]["max_upload_bytes"] == main.MAX_UPLOAD_BYTES


def test_profile_registry_snapshots_are_request_isolated(monkeypatch):
    root = Path(__file__).resolve().parents[1] / "profiles"
    monkeypatch.setattr(main, "PROFILE_ROOT", root)
    first = main._registry_snapshot()
    second = main._registry_snapshot()
    assert first is not second

    first.get("printer", "elegoo-mars-2").metadata["request-local-marker"] = True
    assert "request-local-marker" not in second.get("printer", "elegoo-mars-2").metadata
