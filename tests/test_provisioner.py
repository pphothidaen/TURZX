"""
Unit tests for TURZX Agent Provisioner and Dynamic Registry.
"""

import json
import os
import signal
from pathlib import Path
import pytest

from turzx.provisioner import (
    AgentProvisioner,
    PRESETS,
    SEED_AGENT_IDS,
    get_default_seed_agents,
    notify_carousel_reload,
)


def test_seed_agents_structure():
    seeds = get_default_seed_agents()
    assert "agy" in seeds
    assert "codex" in seeds
    assert len(seeds["agy"]) == 5
    assert len(seeds["codex"]) == 3
    assert seeds["agy"][0]["id"] == "agy1"
    assert seeds["codex"][0]["id"] == "codex1"


def test_provisioner_initialization(tmp_path: Path):
    reg_file = tmp_path / "registry.json"
    acc_dir = tmp_path / "accounts"
    prov = AgentProvisioner(registry_path=str(reg_file), accounts_root=str(acc_dir))

    assert reg_file.exists()
    agents = prov.get_all_agents()
    assert len(agents["agy"]) == 5
    assert len(agents["codex"]) == 3


def test_next_agent_index(tmp_path: Path):
    reg_file = tmp_path / "registry.json"
    acc_dir = tmp_path / "accounts"
    prov = AgentProvisioner(registry_path=str(reg_file), accounts_root=str(acc_dir))

    assert prov.get_next_agent_index("agy") == 6
    assert prov.get_next_agent_index("codex") == 4


def test_add_agy_agent_dynamic(tmp_path: Path):
    reg_file = tmp_path / "registry.json"
    acc_dir = tmp_path / "accounts"
    prov = AgentProvisioner(registry_path=str(reg_file), accounts_root=str(acc_dir))

    new_agy = prov.add_agent(
        cluster="agy",
        name="AGY6 (Automation)",
        preset="gemini_focus",
    )

    assert new_agy["id"] == "agy6"
    assert new_agy["is_dynamic"] is True
    assert new_agy["gemini_tokens_limit"] == PRESETS["gemini_focus"]["gemini_tokens"]

    # Verify workspace directory provisioned
    ws_dir = acc_dir / "agy" / "account6"
    assert ws_dir.exists()
    settings_file = ws_dir / ".gemini" / "settings.json"
    assert settings_file.exists()
    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    assert settings["agent_id"] == "agy6"


def test_add_codex_agent_dynamic(tmp_path: Path):
    reg_file = tmp_path / "registry.json"
    acc_dir = tmp_path / "accounts"
    prov = AgentProvisioner(registry_path=str(reg_file), accounts_root=str(acc_dir))

    new_codex = prov.add_agent(
        cluster="codex",
        name="CODEX4 (Performance)",
        preset="codex_standard",
    )

    assert new_codex["id"] == "codex4"
    assert new_codex["is_dynamic"] is True
    assert new_codex["claude_reqs_limit"] == 0
    assert new_codex["gemini_tokens_limit"] == 0
    assert new_codex["argon_units_limit"] == 0
    assert new_codex["model"] == "gpt-5.6-terra"
    assert new_codex["preset"] == "codex_standard"

    # Verify workspace directory and SQLite DB provisioned
    ws_dir = acc_dir / "codex" / "account4"
    assert ws_dir.exists()
    assert (ws_dir / "config.toml").exists()
    assert (ws_dir / "state.db").exists()


def test_codex_agent_cannot_have_claude_argon_gemini_even_if_requested(tmp_path: Path):
    """Verify that even if an invalid preset or custom quotas specifying claude/gemini are passed,
    Codex agent strictly enforces zero claude, argon, gemini and OpenAI model."""
    reg_file = tmp_path / "registry.json"
    acc_dir = tmp_path / "accounts"
    prov = AgentProvisioner(registry_path=str(reg_file), accounts_root=str(acc_dir))

    cdx = prov.add_agent(
        cluster="codex",
        name="CODEX4 (Secured)",
        preset="claude_heavy",
        custom_quotas={"claude_reqs": 100, "gemini_tokens": 500_000, "argon_units": 50, "model": "claude-opus-5-5"},
    )
    assert cdx["claude_reqs_limit"] == 0
    assert cdx["gemini_tokens_limit"] == 0
    assert cdx["argon_units_limit"] == 0
    assert cdx["preset"] == "codex_standard"
    assert cdx["model"] == "gpt-5.6-terra"

    # Test update_agent_quotas prevents setting claude/argon/gemini
    prov.update_agent_quotas(cdx["id"], {"claude_reqs": 99, "gemini_tokens": 999, "argon_units": 99, "model": "gemini-pro"})
    updated = [a for a in prov.get_cluster_agents("codex") if a["id"] == cdx["id"]][0]
    assert updated["claude_reqs_limit"] == 0
    assert updated["gemini_tokens_limit"] == 0
    assert updated["argon_units_limit"] == 0
    assert updated["model"] == "gpt-5.6-terra"


def test_custom_quotas(tmp_path: Path):
    reg_file = tmp_path / "registry.json"
    acc_dir = tmp_path / "accounts"
    prov = AgentProvisioner(registry_path=str(reg_file), accounts_root=str(acc_dir))

    new_agent = prov.add_agent(
        cluster="agy",
        preset="full_stack",
        custom_quotas={
            "gemini_tokens": 1_500_000,
            "claude_reqs": 150,
            "argon_units": 60,
        },
    )

    assert new_agent["gemini_tokens_limit"] == 1_500_000
    assert new_agent["claude_reqs_limit"] == 150
    assert new_agent["argon_units_limit"] == 60


def test_update_agent_quotas(tmp_path: Path):
    reg_file = tmp_path / "registry.json"
    acc_dir = tmp_path / "accounts"
    prov = AgentProvisioner(registry_path=str(reg_file), accounts_root=str(acc_dir))

    ok = prov.update_agent_quotas("agy1", {"gemini_tokens": 800_000})
    assert ok is True

    agy1 = [a for a in prov.get_cluster_agents("agy") if a["id"] == "agy1"][0]
    assert agy1["gemini_tokens_limit"] == 800_000


def test_remove_dynamic_agent(tmp_path: Path):
    reg_file = tmp_path / "registry.json"
    acc_dir = tmp_path / "accounts"
    prov = AgentProvisioner(registry_path=str(reg_file), accounts_root=str(acc_dir))

    # Try removing non-dynamic agent (should fail)
    ok_seed = prov.remove_agent("agy", "agy1")
    assert ok_seed is False

    # Add dynamic agent
    dyn = prov.add_agent(cluster="agy", name="AGY6")
    assert len(prov.get_cluster_agents("agy")) == 6

    # Remove dynamic agent
    ok_dyn = prov.remove_agent("agy", dyn["id"])
    assert ok_dyn is True
    assert len(prov.get_cluster_agents("agy")) == 5


def test_reset_schedule_info(tmp_path: Path):
    reg_file = tmp_path / "registry.json"
    acc_dir = tmp_path / "accounts"
    prov = AgentProvisioner(registry_path=str(reg_file), accounts_root=str(acc_dir))

    sched = prov.get_reset_schedule_info()
    assert sched["target_epoch"] == "00:00 UTC"
    assert "h" in sched["time_remaining"]
    assert sched["seconds_remaining"] > 0


def test_remove_dynamic_agent_purges_directory(tmp_path: Path, monkeypatch):
    """Verify that removing a dynamic agent purges its workspace directory on disk and enforces 0600."""
    reg_file = tmp_path / "registry.json"
    acc_dir = tmp_path / "accounts"
    pid_file = tmp_path / "carousel.pid"
    prov = AgentProvisioner(registry_path=str(reg_file), accounts_root=str(acc_dir), pid_file=str(pid_file))

    notifications = []
    monkeypatch.setattr(
        "turzx.provisioner.notify_carousel_reload",
        lambda p=None: notifications.append(p) or True,
    )

    # Add dynamic AGY agent (account6)
    dyn_agy = prov.add_agent(cluster="agy", name="AGY6 Dynamic", preset="gemini_focus")
    agy_ws = acc_dir / "agy" / f"account{dyn_agy['account_idx']}"
    assert agy_ws.exists()
    assert (agy_ws / ".gemini" / "settings.json").exists()
    assert str(pid_file) in notifications

    # Add dynamic Codex agent (account4)
    dyn_cdx = prov.add_agent(cluster="codex", name="CODEX4 Dynamic", preset="codex_standard")
    cdx_ws = acc_dir / "codex" / f"account{dyn_cdx['account_idx']}"
    assert cdx_ws.exists()
    assert (cdx_ws / "config.toml").exists()
    assert (cdx_ws / "state.db").exists()

    # Verify atomic 0600 mode on registry
    mode = reg_file.stat().st_mode & 0o777
    assert mode == 0o600, f"Registry file permissions must be 0600, got {oct(mode)}"

    notifications.clear()

    # Remove AGY dynamic agent
    ok_agy = prov.remove_agent("agy", dyn_agy["id"])
    assert ok_agy is True
    assert not agy_ws.exists(), "Dynamic AGY workspace directory must be deleted on disk"
    assert str(pid_file) in notifications

    # Remove Codex dynamic agent
    notifications.clear()
    ok_cdx = prov.remove_agent("codex", dyn_cdx["id"])
    assert ok_cdx is True
    assert not cdx_ws.exists(), "Dynamic Codex workspace directory must be deleted on disk"
    assert str(pid_file) in notifications

    # Verify 0600 mode maintained after removals
    mode_after = reg_file.stat().st_mode & 0o777
    assert mode_after == 0o600


def test_remove_seed_agent_prevented(tmp_path: Path):
    """Verify that removing any seed agent (agy1..5, codex1..3) is strictly prevented."""
    reg_file = tmp_path / "registry.json"
    acc_dir = tmp_path / "accounts"
    prov = AgentProvisioner(registry_path=str(reg_file), accounts_root=str(acc_dir))

    # Verify all seed IDs are denied
    for sid in ["agy1", "agy2", "agy3", "agy4", "agy5"]:
        assert prov.remove_agent("agy", sid) is False
        assert prov.remove_agent("agy", sid.upper()) is False

    for sid in ["codex1", "codex2", "codex3"]:
        assert prov.remove_agent("codex", sid) is False
        assert prov.remove_agent("codex", sid.upper()) is False

    # Check SEED_AGENT_IDS constant matches
    assert SEED_AGENT_IDS == {
        "agy1", "agy2", "agy3", "agy4", "agy5",
        "codex1", "codex2", "codex3",
    }

    # Verify registry data remained untouched
    all_agents = prov.get_all_agents()
    assert len(all_agents["agy"]) == 5
    assert len(all_agents["codex"]) == 3


def test_notify_carousel_reload_sigusr1(tmp_path: Path, monkeypatch):
    """Verify that notify_carousel_reload reads PID and sends SIGUSR1 to live process."""
    pid_file = tmp_path / "carousel.pid"

    # Non-existent PID file returns False gracefully
    assert notify_carousel_reload(str(pid_file)) is False

    target_pid = 42424
    pid_file.write_text(str(target_pid), encoding="utf-8")

    signals_sent = []

    def mock_kill(pid, sig):
        signals_sent.append((pid, sig))
        return None

    monkeypatch.setattr(os, "kill", mock_kill)

    success = notify_carousel_reload(str(pid_file))
    assert success is True
    # Checked process liveness
    assert (target_pid, 0) in signals_sent
    # Sent SIGUSR1
    assert (target_pid, signal.SIGUSR1) in signals_sent


def test_notify_carousel_reload_resilience(tmp_path: Path, monkeypatch):
    """Verify notify_carousel_reload exception handling (dead process, invalid PID)."""
    pid_file = tmp_path / "carousel.pid"

    # 1. Dead process raises ProcessLookupError and cleans up stale PID file
    pid_file.write_text("88888", encoding="utf-8")

    def mock_kill_dead(pid, sig):
        raise ProcessLookupError("No such process")

    monkeypatch.setattr(os, "kill", mock_kill_dead)
    assert notify_carousel_reload(str(pid_file)) is False
    assert not pid_file.exists(), "Stale PID file for dead process must be cleaned up"

    # 2. Corrupt / empty PID file
    pid_file.write_text("not_a_number", encoding="utf-8")
    assert notify_carousel_reload(str(pid_file)) is False

    pid_file.write_text("", encoding="utf-8")
    assert notify_carousel_reload(str(pid_file)) is False

    # 3. Non-positive PID guard
    pid_file.write_text("0", encoding="utf-8")
    assert notify_carousel_reload(str(pid_file)) is False

    pid_file.write_text("-1", encoding="utf-8")
    assert notify_carousel_reload(str(pid_file)) is False


def test_purge_workspace_traversal_guards(tmp_path: Path):
    """Verify path traversal guards, symlink handling, and validation in purge_workspace."""
    reg_file = tmp_path / "registry.json"
    acc_dir = tmp_path / "accounts"
    prov = AgentProvisioner(registry_path=str(reg_file), accounts_root=str(acc_dir))

    # Invalid cluster
    with pytest.raises(ValueError, match="Invalid cluster type"):
        prov.purge_workspace("invalid_cluster", 1)

    # Invalid account index
    with pytest.raises(ValueError, match="positive integer"):
        prov.purge_workspace("agy", 0)

    with pytest.raises(ValueError, match="positive integer"):
        prov.purge_workspace("agy", -5)

    # Purging non-existent workspace returns False without error
    assert prov.purge_workspace("agy", 999) is False

    # Symlink pointing outside workspace: unlinks symlink without deleting target
    outside_dir = tmp_path / "outside_secret"
    outside_dir.mkdir()
    (outside_dir / "secret.txt").write_text("secret_data")

    cluster_agy = acc_dir / "agy"
    cluster_agy.mkdir(parents=True, exist_ok=True)
    symlink_acc = cluster_agy / "account7"
    symlink_acc.symlink_to(outside_dir)

    assert symlink_acc.is_symlink()
    assert prov.purge_workspace("agy", 7) is True
    assert not symlink_acc.exists()
    assert outside_dir.exists(), "Target directory outside workspace must NOT be deleted"
    assert (outside_dir / "secret.txt").exists()

    # Broken symlink handling: unlinked cleanly without crashing
    broken_symlink = cluster_agy / "account8"
    broken_symlink.symlink_to(cluster_agy / "nonexistent_dir")
    assert broken_symlink.is_symlink()
    assert prov.purge_workspace("agy", 8) is True
    assert not broken_symlink.exists()

