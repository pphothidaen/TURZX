"""
Tests for Hermes Agent Quota Sync and Scheduled Jobs TURZX Display Integration.
Verifies agy1-agy5 and codex1-codex3 quota tracking, rendering, and scheduler pipeline.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from agent_quota_manager import AgentQuotaManager, QuotaStatus, QuotaUsage
from turzx_display_service import TURZXDisplayService
from turxz_scheduled_jobs import (
    JobPriority,
    ScheduleType,
    TURXZJobExecutor,
    TURXZJobManager,
    TURXZScheduler,
)


@pytest.fixture
def mock_hermes_env(tmp_path):
    """Create a temporary mock Hermes environment with quota_state, cron jobs, and orchestration state."""
    hermes_dir = tmp_path / ".hermes"
    hermes_dir.mkdir(parents=True)
    cron_dir = hermes_dir / "cron"
    cron_dir.mkdir(parents=True)
    orch_dir = hermes_dir / "turzx-orchestration"
    orch_dir.mkdir(parents=True)

    # 1. quota_state.json
    quota_state = {
        "timestamp": "2026-11-12T10:00:00Z",
        "agy": {
            "agy1": {"authenticated": False, "error": "timeout (auth or network)"},
            "agy2": {
                "gemini": {"weekly_remaining": 90, "five_hour_remaining": 90},
                "claude_gpt": {"weekly_remaining": 80, "five_hour_remaining": 80},
                "authenticated": True,
            },
            "agy3": {
                "gemini": {"weekly_remaining": 100, "five_hour_remaining": 100},
                "authenticated": True,
            },
            "agy4": {
                "gemini": {"weekly_remaining": 15, "five_hour_remaining": 15},
                "authenticated": True,
            },
            "agy5": {
                "gemini": {"weekly_remaining": 0, "five_hour_remaining": 0},
                "authenticated": True,
            },
        },
        "codex": {
            "codex1": {
                "authenticated": False,
                "blocked": True,
                "status_text": "Error: stdin is not a terminal",
                "model": "gpt-5.6-terra",
            },
            "codex2": {
                "authenticated": True,
                "blocked": False,
                "status_text": "Logged in",
                "model": "gpt-5.6-terra",
            },
            "codex3": {
                "authenticated": True,
                "blocked": False,
                "status_text": "Logged in",
                "model": "gpt-5.6-terra",
            },
        },
    }
    with open(hermes_dir / "quota_state.json", "w", encoding="utf-8") as f:
        json.dump(quota_state, f)

    # 2. cron/jobs.json
    jobs_doc = {
        "jobs": [
            {
                "id": "job_quota_mon",
                "name": "quota-monitor",
                "schedule": {"display": "every 15m"},
                "state": "scheduled",
                "last_status": "ok",
                "last_run_at": "2026-11-12T09:45:00Z",
                "next_run_at": "2026-11-12T10:00:00Z",
                "failure_streak": 0,
            },
            {
                "id": "job_daily_rep",
                "name": "daily-usage-report",
                "schedule": {"display": "0 0 * * *"},
                "state": "scheduled",
                "last_status": "ok",
                "last_run_at": "2026-11-12T00:00:00Z",
                "next_run_at": "2026-11-13T00:00:00Z",
                "failure_streak": 0,
            },
        ]
    }
    with open(cron_dir / "jobs.json", "w", encoding="utf-8") as f:
        json.dump(jobs_doc, f)

    # 3. TURZX_ORCHESTRATION_STATE.json
    orch_doc = {
        "orchestration_id": "TURZX_TEST_V1",
        "agents": {
            "agy1": {"role": "Strategic Orchestrator", "model": "claude-opus-5-5@azure"},
            "agy2": {"role": "Evidence Collector", "model": "gemini-3.8-flash"},
            "agy3": {"role": "Pattern Synthesizer", "model": "gemini-3.1-pro"},
            "agy4": {"role": "Contract Guardian", "model": "claude-sonnet-5@azure"},
            "agy5": {"role": "Runtime Executor", "model": "gpt-oss:120b"},
            "codex1": {"role": "Code Implementer", "model": "gpt-5.6-terra"},
            "codex2": {"role": "UI/Frontend Specialist", "model": "gpt-5.6-terra"},
            "codex3": {"role": "Test/QA Engineer", "model": "gpt-5.6-terra"},
        },
    }
    with open(orch_dir / "TURZX_ORCHESTRATION_STATE.json", "w", encoding="utf-8") as f:
        json.dump(orch_doc, f)

    return hermes_dir


class TestAgentQuotaManagerSync:
    """Test AgentQuotaManager synchronization with Hermes data."""

    def test_sync_agent_quotas_all_8_agents(self, mock_hermes_env):
        """Verify that all 8 agents (agy1-agy5 and codex1-codex3) are tracked and synchronized."""
        manager = AgentQuotaManager()
        res = manager.sync_agent_quotas(hermes_dir=mock_hermes_env)

        assert res["status"] == "completed"
        synced = res["synced_agents"]
        assert len(synced) == 8

        # Target 8 agents presence
        expected_agents = [f"agy{i}" for i in range(1, 6)] + [f"codex{i}" for i in range(1, 4)]
        for aid in expected_agents:
            assert aid in synced
            agent_info = synced[aid]
            assert "used_pct" in agent_info
            assert "remaining_pct" in agent_info
            assert "status" in agent_info
            assert "model" in agent_info
            assert "role" in agent_info
            assert 0.0 <= agent_info["used_pct"] <= 100.0
            assert round(agent_info["used_pct"] + agent_info["remaining_pct"], 1) == 100.0

        # Specific calculations verification
        # agy2: min(90, 80) remaining = 80 -> used_pct = 20.0
        assert synced["agy2"]["used_pct"] == 20.0
        assert synced["agy2"]["status"] == "healthy"

        # agy3: 100 remaining -> used_pct = 0.0
        assert synced["agy3"]["used_pct"] == 0.0
        assert synced["agy3"]["status"] == "healthy"

        # agy4: 15 remaining -> used_pct = 85.0 -> warning
        assert synced["agy4"]["used_pct"] == 85.0
        assert synced["agy4"]["status"] == "warning"

        # agy5: 0 remaining -> used_pct = 100.0 -> critical
        assert synced["agy5"]["used_pct"] == 100.0
        assert synced["agy5"]["status"] == "critical"

        # codex1: blocked -> warning status
        assert synced["codex1"]["status"] in ("warning", "critical")

        # codex2 & codex3: authenticated
        assert synced["codex2"]["status"] == "healthy"
        assert synced["codex3"]["status"] == "healthy"

        # Hermes jobs parsed
        assert len(res["hermes_jobs"]) == 2
        assert res["hermes_jobs"][0]["name"] == "quota-monitor"

    def test_get_all_quotas_method(self, mock_hermes_env):
        """Verify get_all_quotas returns comprehensive dictionary."""
        manager = AgentQuotaManager()
        manager.sync_agent_quotas(hermes_dir=mock_hermes_env)
        all_quotas = manager.get_all_quotas()

        assert len(all_quotas) >= 8
        for i in range(1, 6):
            assert f"agy{i}" in all_quotas
            assert "used_pct" in all_quotas[f"agy{i}"]
        for i in range(1, 4):
            assert f"codex{i}" in all_quotas
            assert "used_pct" in all_quotas[f"codex{i}"]

        # Verify get_hermes_jobs retrieves the synced jobs
        hermes_jobs = manager.get_hermes_jobs()
        assert len(hermes_jobs) == 2
        assert hermes_jobs[0]["name"] == "quota-monitor"

    def test_sync_fallback_on_missing_dir(self, tmp_path):
        """Verify fail-safe fallback when hermes directory does not exist."""
        empty_dir = tmp_path / "non_existent"
        manager = AgentQuotaManager()
        res = manager.sync_agent_quotas(hermes_dir=empty_dir)

        assert res["status"] == "completed"
        assert len(res["synced_agents"]) == 8
        for aid in [f"agy{i}" for i in range(1, 6)] + [f"codex{i}" for i in range(1, 4)]:
            assert aid in res["synced_agents"]

    def test_scheduler_jobs_registration(self):
        """Verify registration of scheduled quota jobs."""
        scheduler = TURXZScheduler(
            job_manager=TURXZJobManager(),
            executor=TURXZJobExecutor(),
        )
        manager = AgentQuotaManager()
        job_ids = manager.register_scheduler_jobs(scheduler)

        assert "quota_sync" in job_ids
        assert "quota_check" in job_ids
        assert "quota_cleanup" in job_ids
        assert "quota_daily_reset" in job_ids

        # Trigger scheduled handler directly
        sync_res = manager._scheduled_quota_sync()
        assert sync_res["status"] == "completed"
        assert "synced_agents" in sync_res


class TestTURZXDisplayServiceDashboard:
    """Test TURZXDisplayService rendering and dashboard display."""

    def test_render_quota_display_portrait(self):
        """Verify rendering 320x480 RGB565 quota dashboard with all 8 agents."""
        display = TURZXDisplayService(display_width=320, display_height=480)
        display._serial_driver = MagicMock()
        display._serial_driver.display_width = 320
        display._serial_driver.display_height = 480
        display._serial_driver.display_image.return_value = True

        sample_data = {
            f"agy{i}": {"used_pct": i * 15.0, "status": "healthy", "role": f"Role{i}"}
            for i in range(1, 6)
        }
        sample_data.update({
            f"codex{i}": {"used_pct": i * 25.0, "status": "warning" if i > 1 else "healthy", "role": f"CodexRole{i}"}
            for i in range(1, 4)
        })

        result = display.render_quota_display(sample_data, timestamp="12:34:56")
        assert result["success"] is True
        assert result["elements_drawn"] == 8
        assert result["image_size"] == 320 * 480 * 2  # exactly 307,200 bytes

        # Check driver was called with correct byte count
        display._serial_driver.display_image.assert_called_once()
        sent_bytes = display._serial_driver.display_image.call_args[0][0]
        assert len(sent_bytes) == 307200

    def test_render_quota_display_landscape(self):
        """Verify rendering 480x320 RGB565 quota dashboard (2-column layout)."""
        display = TURZXDisplayService(display_width=480, display_height=320)
        display._serial_driver = MagicMock()
        display._serial_driver.display_width = 480
        display._serial_driver.display_height = 320
        display._serial_driver.display_image.return_value = True

        sample_data = {
            f"agy{i}": {"used_pct": 10.0 * i, "status": "healthy"} for i in range(1, 6)
        }
        sample_data.update({
            f"codex{i}": {"used_pct": 30.0 * i, "status": "critical"} for i in range(1, 4)
        })

        result = display.render_quota_display(sample_data)
        assert result["success"] is True
        assert result["elements_drawn"] == 8
        assert result["image_size"] == 480 * 320 * 2  # 307,200 bytes

    def test_fail_closed_buffer_verification(self, monkeypatch):
        """Verify Fail-Closed abort if generated buffer length is corrupted."""
        display = TURZXDisplayService(display_width=320, display_height=480)
        display._serial_driver = MagicMock()
        display._serial_driver.display_width = 320
        display._serial_driver.display_height = 480

        # Simulate corrupted image_to_rgb565 producing truncated buffer
        with patch("turzx_display_service.image_to_rgb565", return_value=b"truncated"):
            result = display.render_quota_display({"agy1": {"used_pct": 10.0, "status": "healthy"}})
            assert result["success"] is False
            assert "Buffer length mismatch" in result["error"]
            # Ensure corrupted buffer was NEVER sent to display
            display._serial_driver.display_image.assert_not_called()

    def test_update_display_protocol(self):
        """Verify update_display_protocol pipeline with AgentQuotaManager."""
        display = TURZXDisplayService(display_width=320, display_height=480)
        display._serial_driver = MagicMock()
        display._serial_driver.display_width = 320
        display._serial_driver.display_height = 480
        display._serial_driver.connect.return_value = True
        display._serial_driver.initialize.return_value = True
        display._serial_driver.display_image.return_value = True

        mock_aqm = MagicMock()
        mock_aqm.get_all_quotas.return_value = {
            f"agy{i}": {"used_pct": 10.0 * i, "status": "healthy"} for i in range(1, 6)
        }

        res = display.update_display_protocol(agent_quota_manager=mock_aqm)
        assert res["success"] is True
        assert display._hardware_initialized is True

    def test_update_display_protocol_fail_closed_on_hardware_failure(self):
        """Verify update_display_protocol aborts fail-closed if hardware fails."""
        display = TURZXDisplayService(display_width=320, display_height=480)
        display._hardware_initialized = False
        display._serial_driver = MagicMock()
        display._serial_driver.connect.return_value = False

        res = display.update_display_protocol(agent_data={"agy1": {"used_pct": 10.0}}, force_hardware_init=True)
        assert res["success"] is False
        assert "Display hardware initialization failed" in res["error"]

    def test_render_quota_display_with_hermes_jobs_and_no_collision(self):
        """Verify rendering with Hermes jobs metadata and long role titles without overlap."""
        display = TURZXDisplayService(display_width=320, display_height=480)
        display._serial_driver = MagicMock()
        display._serial_driver.display_width = 320
        display._serial_driver.display_height = 480
        display._serial_driver.display_image.return_value = True

        sample_data = {
            "codex2": {
                "used_pct": 45.0,
                "remaining_pct": 55.0,
                "status": "warning",
                "role": "UI/Frontend Specialist",
                "model": "gpt-5.6-terra",
                "current_usage": 45.0,
                "quota_limit": 100.0,
            }
        }
        mock_jobs = [
            {"id": "j1", "name": "quota-monitor", "last_status": "ok"},
            {"id": "j2", "name": "daily-usage-report", "last_status": "ok"},
        ]

        result = display.render_quota_display(sample_data, hermes_jobs=mock_jobs)
        assert result["success"] is True

    def test_display_service_context_manager(self):
        """Verify context manager properly disconnects on exit."""
        with TURZXDisplayService() as display:
            display._serial_driver = MagicMock()
            mock_drv = display._serial_driver
        mock_drv.disconnect.assert_called_once()

    def test_scheduled_display_quota_update_raises_on_failure(self):
        """Verify _scheduled_display_quota_update raises RuntimeError on failure."""
        display = TURZXDisplayService()
        display.update_display_protocol = MagicMock(return_value={"success": False, "error": "USB failed"})

        with pytest.raises(RuntimeError) as exc_info:
            display._scheduled_display_quota_update()
        assert "Display update failed" in str(exc_info.value)

    def test_display_scheduler_registration(self):
        """Verify registration of display quota update job with TURXZScheduler."""
        scheduler = TURXZScheduler(
            job_manager=TURXZJobManager(),
            executor=TURXZJobExecutor(),
        )
        display = TURZXDisplayService()
        job_ids = display.register_scheduler_jobs(scheduler)

        assert "display_quota_update" in job_ids
        assert "display_health_check" in job_ids
        assert "session_cleanup" in job_ids or "display_session_cleanup" in job_ids

        status = display.get_scheduler_integration_status()
        assert "display_quota_update" in status["supported_jobs"]


class TestBackendDashboardAPI:
    """Test FastAPI dashboard endpoints with quota integration."""

    def test_agent_dashboard_endpoint(self):
        """Verify GET /api/v1/agent/dashboard returns all 8 agents and quota statuses."""
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient
        from backend.api.main import create_app

        app = create_app(start_render_loop=False)
        client = TestClient(app)

        response = client.get("/api/v1/agent/dashboard")
        assert response.status_code == 200
        data = response.json()

        assert "agents" in data
        assert len(data["agents"]) == 8
        assert data["agents"] == [f"agy{i}" for i in range(1, 6)] + [f"codex{i}" for i in range(1, 4)]
        assert "quota_statuses" in data
        for aid in data["agents"]:
            assert aid in data["quota_statuses"]
        assert "hermes_jobs" in data

    def test_quota_sync_endpoint(self):
        """Verify POST /api/v1/agent/quota/sync triggers sync successfully."""
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient
        from backend.api.main import create_app

        app = create_app(start_render_loop=False)
        client = TestClient(app)

        response = client.post("/api/v1/agent/quota/sync")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "result" in data
        assert "synced_agents" in data["result"]
        assert len(data["result"]["synced_agents"]) == 8
