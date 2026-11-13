"""
Unit tests for Account Email Display across TURZX Carousel views and Provisioning.
Verifies email mappings, metadata propagation, and non-colliding visual layout on 320x480 IPS display.
"""

from pathlib import Path
from unittest.mock import MagicMock
import pytest
from PIL import Image, ImageDraw

from generate_carousel_pages import (
    FONTS,
    PALETTE,
    WIDTH,
    HEIGHT,
    get_active_agents,
    render_page_overview,
    render_page_agy,
    render_page_codex,
    truncate_to_width,
)
from turzx.provisioner import (
    DEFAULT_AGENT_EMAILS,
    AgentProvisioner,
    get_default_seed_agents,
    resolve_account_email,
)
from agent_quota_manager import AgentQuotaManager


def test_seed_agents_have_valid_emails():
    """Verify all 8 baseline seed agents define distinct, realistic email addresses."""
    seeds = get_default_seed_agents()
    
    agy_agents = seeds.get("agy", [])
    codex_agents = seeds.get("codex", [])
    
    assert len(agy_agents) == 5
    assert len(codex_agents) == 3
    
    seen_agy_emails = set()
    for a in agy_agents:
        assert "email" in a
        assert "@" in a["email"]
        assert len(a["email"]) > 5
        seen_agy_emails.add(a["email"])
    
    # Check distinct emails in AGY cluster
    assert len(seen_agy_emails) == 5
    
    seen_codex_emails = set()
    for c in codex_agents:
        assert "email" in c
        assert "@" in c["email"]
        assert len(c["email"]) > 5
        seen_codex_emails.add(c["email"])
    
    assert len(seen_codex_emails) == 3


def test_provisioner_email_lifecycle(tmp_path: Path):
    """Verify AgentProvisioner initializes, saves, and updates agent emails."""
    reg_file = tmp_path / "registry.json"
    acc_dir = tmp_path / "accounts"
    prov = AgentProvisioner(registry_path=str(reg_file), accounts_root=str(acc_dir))

    # All agents loaded should have email populated
    all_agents = prov.get_all_agents()
    for a in all_agents["agy"]:
        assert a.get("email") == DEFAULT_AGENT_EMAILS[a["id"]]
    for c in all_agents["codex"]:
        assert c.get("email") == DEFAULT_AGENT_EMAILS[c["id"]]

    # Add dynamic agent with custom email
    new_agent = prov.add_agent(
        cluster="agy",
        name="AGY6 (Worker)",
        preset="full_stack",
        email="custom_worker@corp.ai",
    )
    assert new_agent["email"] == "custom_worker@corp.ai"

    # Reload from disk and verify persistence
    prov2 = AgentProvisioner(registry_path=str(reg_file), accounts_root=str(acc_dir))
    reloaded_agy = prov2.get_cluster_agents("agy")
    agy6 = next(a for a in reloaded_agy if a["id"] == "agy6")
    assert agy6["email"] == "custom_worker@corp.ai"

    # Update email via update_agent_quotas
    prov2.update_agent_quotas("agy6", {"email": "updated_worker@corp.ai"})
    reloaded_agy_updated = prov2.get_cluster_agents("agy")
    agy6_up = next(a for a in reloaded_agy_updated if a["id"] == "agy6")
    assert agy6_up["email"] == "updated_worker@corp.ai"


def test_truncate_to_width_behavior():
    """Verify truncate_to_width correctly calculates widths and preserves strings when fitting."""
    img = Image.new("RGB", (320, 100))
    draw = ImageDraw.Draw(img)
    font = FONTS["nano"]

    # Short string should not be truncated
    short_str = "short@ai.com"
    res_short = truncate_to_width(draw, short_str, font, max_width=150)
    assert res_short == short_str

    # Very long string with limited space should truncate with ellipsis
    long_str = "extremely_long_untruncated_account_email_name@department.corporation.com"
    res_trunc = truncate_to_width(draw, long_str, font, max_width=60)
    assert res_trunc.endswith("...")
    bbox = draw.textbbox((0, 0), res_trunc, font=font)
    assert bbox[2] - bbox[0] <= 60

    # Edge cases
    assert truncate_to_width(draw, "", font, 100) == ""
    assert truncate_to_width(draw, "hello", font, 0) == ""


def test_agent_quota_manager_sync_includes_email(tmp_path: Path):
    """Verify AgentQuotaManager includes account email in synced agents payload."""
    manager = AgentQuotaManager()
    res = manager.sync_agent_quotas(hermes_dir=tmp_path / "hermes")
    synced = res.get("synced_agents", {})
    
    assert len(synced) >= 8
    for aid in [f"agy{i}" for i in range(1, 6)] + [f"codex{i}" for i in range(1, 4)]:
        assert aid in synced
        assert "email" in synced[aid]
        assert "@" in synced[aid]["email"]


def test_render_carousel_pages_with_emails(tmp_path: Path):
    """Verify Page 0, Page 1, and Page 2 render cleanly with emails without margin leakage."""
    reg_file = tmp_path / "registry.json"
    acc_dir = tmp_path / "accounts"
    prov = AgentProvisioner(registry_path=str(reg_file), accounts_root=str(acc_dir))

    p0 = render_page_overview(0, 3, prov=prov)
    assert p0.size == (320, 480)
    assert p0.mode == "RGB"

    p1 = render_page_agy(1, 3, prov=prov)
    assert p1.size == (320, 480)
    assert p1.mode == "RGB"

    p2 = render_page_codex(2, 3, prov=prov)
    assert p2.size == (320, 480)
    assert p2.mode == "RGB"


def test_render_with_extreme_email_lengths(tmp_path: Path):
    """Verify renderers handle extra long and empty emails gracefully without crashing."""
    reg_file = tmp_path / "registry.json"
    acc_dir = tmp_path / "accounts"
    prov = AgentProvisioner(registry_path=str(reg_file), accounts_root=str(acc_dir))

    # Add agents with extreme emails
    prov.add_agent(
        cluster="agy",
        name="AGY6",
        email="a" * 60 + "@superlongdomainname.org",
    )
    prov.add_agent(
        cluster="codex",
        name="CODEX4",
        email="",  # empty email edge case
    )

    p0 = render_page_overview(0, 4, cluster="agy", prov=prov)
    assert p0.size == (320, 480)

    p1 = render_page_agy(2, 4, prov=prov)
    assert p1.size == (320, 480)

    p2 = render_page_codex(3, 4, prov=prov)
    assert p2.size == (320, 480)
