"""
Unit tests for Hermes Desktop Plugin Backend API (plugin_api.py).
"""

import sys
from pathlib import Path

# Add plugin dashboard directory to sys.path
plugin_dashboard_dir = Path("/Users/kimlenglim/.hermes/plugins/turzx-quota-dashboard/dashboard")
if str(plugin_dashboard_dir) not in sys.path:
    sys.path.insert(0, str(plugin_dashboard_dir))

from plugin_api import handle_request, turzx_api  # noqa: E402


def test_plugin_status_endpoint():
    res = handle_request("api/quota/status")
    assert res["status"] == "success"
    assert "overall_usage_percentage" in res
    assert "reset_schedule" in res
    assert "total_agents" in res


def test_plugin_agents_endpoint():
    res = handle_request("api/plugins/turzx-quota-dashboard/agents")
    assert res["success"] is True
    assert "clusters" in res
    assert "agy" in res["clusters"]
    assert "codex" in res["clusters"]
    assert len(res["clusters"]["agy"]) >= 5
    assert len(res["clusters"]["codex"]) >= 3


def test_plugin_add_and_remove_agent_lifecycle(tmp_path: Path):
    # Add agent
    add_payload = {
        "cluster": "agy",
        "name": "AGY Test Dynamic",
        "preset": "gemini_focus",
        "quotas": {
            "gemini_tokens": 1_200_000,
            "claude_reqs": 25,
            "argon_units": 6,
        },
    }
    res_add = handle_request(
        "api/plugins/turzx-quota-dashboard/agents/add",
        method="POST",
        body=add_payload,
    )
    assert res_add["success"] is True
    agent_id = res_add["agent"]["id"]
    assert "agy" in agent_id
    assert res_add["agent"]["gemini_tokens_limit"] == 1_200_000

    # Remove dynamic agent
    res_del = handle_request(
        "api/plugins/turzx-quota-dashboard/agents/remove",
        method="POST",
        body={"cluster": "agy", "id": agent_id},
    )
    assert res_del["success"] is True


def test_plugin_update_quota():
    res = handle_request(
        "api/plugins/turzx-quota-dashboard/agents/update-quota",
        method="POST",
        body={
            "id": "agy1",
            "quotas": {
                "gemini_tokens": 900_000,
            },
        },
    )
    assert res["success"] is True


def test_plugin_telemetry_endpoint():
    res = handle_request("api/plugins/turzx-quota-dashboard/telemetry")
    assert res["success"] is True
    assert res["vendor_id"] == "0x1A86"
    assert res["product_id"] == "0x5722"
    assert res["model"] == "3.5-inch IPS (320x480)"
    assert res["max_fps"] == 30
