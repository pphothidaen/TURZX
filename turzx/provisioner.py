"""
TURZX Agent Provisioner and Dynamic Registry Manager.
Provides workspace auto-provisioning for AGY (~/.ai-accounts/agy/accountN)
and Codex (~/.ai-accounts/codex/accountN), dynamic sub-quota configuration,
and synchronized state persistence for Hermes Agent Desktop UI and TURZX display.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import sqlite3
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("turzx.provisioner")

# Preset definitions matching Hermes Plugin UI/UX spec
PRESETS = {
    "gemini_focus": {
        "name": "Gemini Focus",
        "description": "Optimized for multimodal high-throughput reasoning",
        "gemini_tokens": 1_000_000,
        "claude_reqs": 20,
        "argon_units": 5,
    },
    "claude_heavy": {
        "name": "Claude Heavy",
        "description": "Optimized for deep synthesis and precision coding",
        "gemini_tokens": 200_000,
        "claude_reqs": 100,
        "argon_units": 10,
    },
    "full_stack": {
        "name": "Full-Stack Balanced",
        "description": "Balanced multi-agent orchestration tier",
        "gemini_tokens": 500_000,
        "claude_reqs": 50,
        "argon_units": 25,
    },
}

DEFAULT_REGISTRY_PATH = os.path.expanduser(
    "~/.hermes/plugins/turzx-quota-dashboard/dashboard/scripts/state/agent_registry.json"
)

DEFAULT_AI_ACCOUNTS_ROOT = os.path.expanduser("~/.ai-accounts")

DEFAULT_CAROUSEL_PID_FILE = "/tmp/turzx-carousel.pid"

SEED_AGENT_IDS = frozenset({
    "agy1", "agy2", "agy3", "agy4", "agy5",
    "codex1", "codex2", "codex3",
})


def notify_carousel_reload(pid_file: str = DEFAULT_CAROUSEL_PID_FILE) -> bool:
    """
    Notify running carousel process via SIGUSR1 to trigger immediate reload.
    Reads PID from pid_file if it exists, checks process liveness with os.kill(pid, 0),
    and sends os.kill(pid, signal.SIGUSR1). Handles exceptions gracefully without crashing.
    """
    pid_path = Path(pid_file)
    try:
        if not pid_path.is_file():
            return False
        content = pid_path.read_text(encoding="utf-8").strip()
        if not content:
            return False
        pid = int(content)
        if pid <= 0:
            return False
        # Check process liveness
        os.kill(pid, 0)
        # Send SIGUSR1 if supported
        sig = getattr(signal, "SIGUSR1", None)
        if sig is not None:
            os.kill(pid, sig)
            return True
        return False
    except ProcessLookupError:
        # Process is dead - clean up stale PID file safely
        try:
            pid_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    except (FileNotFoundError, PermissionError, ValueError, OSError):
        return False
    except Exception:
        return False


@dataclass
class AgentItem:
    id: str
    name: str
    cluster: str  # "agy" or "codex"
    account_idx: int
    status: str = "healthy"  # "healthy", "warning", "critical", "idle"
    usage_pct: float = 0.0
    preset: str = "full_stack"
    gemini_tokens_used: int = 0
    gemini_tokens_limit: int = 500_000
    claude_reqs_used: int = 0
    claude_reqs_limit: int = 50
    argon_units_used: int = 0
    argon_units_limit: int = 25
    turns: str = "0"
    items: str = "0"
    is_dynamic: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def get_default_seed_agents() -> Dict[str, List[Dict[str, Any]]]:
    """Return default baseline agents (agy1..agy5 and codex1..codex3)."""
    return {
        "agy": [
            {
                "id": "agy1",
                "name": "AGY1 (Primary)",
                "cluster": "agy",
                "account_idx": 1,
                "status": "healthy",
                "usage_pct": 25.5,
                "preset": "full_stack",
                "gemini_tokens_used": 255_000,
                "gemini_tokens_limit": 1_000_000,
                "claude_reqs_used": 18,
                "claude_reqs_limit": 100,
                "argon_units_used": 4,
                "argon_units_limit": 25,
                "turns": "0",
                "items": "0",
                "is_dynamic": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "id": "agy2",
                "name": "AGY2 (Research)",
                "cluster": "agy",
                "account_idx": 2,
                "status": "healthy",
                "usage_pct": 42.0,
                "preset": "full_stack",
                "gemini_tokens_used": 550_000,
                "gemini_tokens_limit": 1_000_000,
                "claude_reqs_used": 35,
                "claude_reqs_limit": 100,
                "argon_units_used": 8,
                "argon_units_limit": 25,
                "turns": "0",
                "items": "0",
                "is_dynamic": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "id": "agy3",
                "name": "AGY3 (Worker)",
                "cluster": "agy",
                "account_idx": 3,
                "status": "healthy",
                "usage_pct": 15.0,
                "preset": "gemini_focus",
                "gemini_tokens_used": 180_000,
                "gemini_tokens_limit": 1_000_000,
                "claude_reqs_used": 12,
                "claude_reqs_limit": 100,
                "argon_units_used": 5,
                "argon_units_limit": 25,
                "turns": "0",
                "items": "0",
                "is_dynamic": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "id": "agy4",
                "name": "AGY4 (Reviewer)",
                "cluster": "agy",
                "account_idx": 4,
                "status": "warning",
                "usage_pct": 78.5,
                "preset": "claude_heavy",
                "gemini_tokens_used": 850_000,
                "gemini_tokens_limit": 1_000_000,
                "claude_reqs_used": 72,
                "claude_reqs_limit": 100,
                "argon_units_used": 40,
                "argon_units_limit": 50,
                "turns": "0",
                "items": "0",
                "is_dynamic": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "id": "agy5",
                "name": "AGY5 (Standby)",
                "cluster": "agy",
                "account_idx": 5,
                "status": "healthy",
                "usage_pct": 10.0,
                "preset": "gemini_focus",
                "gemini_tokens_used": 120_000,
                "gemini_tokens_limit": 1_000_000,
                "claude_reqs_used": 8,
                "claude_reqs_limit": 100,
                "argon_units_used": 0,
                "argon_units_limit": 25,
                "turns": "0",
                "items": "0",
                "is_dynamic": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        ],
        "codex": [
            {
                "id": "codex1",
                "name": "CODEX1 (Backend Core)",
                "cluster": "codex",
                "account_idx": 1,
                "status": "critical",
                "usage_pct": 88.0,
                "preset": "claude_heavy",
                "gemini_tokens_used": 350_000,
                "gemini_tokens_limit": 1_000_000,
                "claude_reqs_used": 92,
                "claude_reqs_limit": 100,
                "argon_units_used": 65,
                "argon_units_limit": 100,
                "turns": "1,357",
                "items": "52,027",
                "is_dynamic": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "id": "codex2",
                "name": "CODEX2 (UI / Frontend)",
                "cluster": "codex",
                "account_idx": 2,
                "status": "healthy",
                "usage_pct": 54.0,
                "preset": "full_stack",
                "gemini_tokens_used": 200_000,
                "gemini_tokens_limit": 1_000_000,
                "claude_reqs_used": 58,
                "claude_reqs_limit": 100,
                "argon_units_used": 40,
                "argon_units_limit": 100,
                "turns": "925",
                "items": "29,955",
                "is_dynamic": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "id": "codex3",
                "name": "CODEX3 (Infra / CI)",
                "cluster": "codex",
                "account_idx": 3,
                "status": "healthy",
                "usage_pct": 31.0,
                "preset": "gemini_focus",
                "gemini_tokens_used": 100_000,
                "gemini_tokens_limit": 1_000_000,
                "claude_reqs_used": 35,
                "claude_reqs_limit": 100,
                "argon_units_used": 25,
                "argon_units_limit": 100,
                "turns": "562",
                "items": "24,499",
                "is_dynamic": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        ],
    }


class AgentProvisioner:
    """Manages agent registry persistence and directory workspace provisioning."""

    def __init__(
        self,
        registry_path: str = DEFAULT_REGISTRY_PATH,
        accounts_root: str = DEFAULT_AI_ACCOUNTS_ROOT,
        pid_file: str = DEFAULT_CAROUSEL_PID_FILE,
    ):
        self.registry_path = Path(registry_path)
        self.accounts_root = Path(accounts_root)
        self.pid_file = pid_file
        self._ensure_storage()

    def _ensure_storage(self) -> None:
        """Ensure parent directories and registry file exist with secure permissions."""
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.registry_path.exists():
            seeds = get_default_seed_agents()
            self._save_registry_data(seeds)

    def _load_registry_data(self) -> Dict[str, List[Dict[str, Any]]]:
        try:
            with open(self.registry_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "agy" in data and "codex" in data:
                    return data
        except Exception:
            pass
        return get_default_seed_agents()

    def _save_registry_data(self, data: Dict[str, List[Dict[str, Any]]]) -> None:
        """Atomic write to prevent race conditions or partial files."""
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix="registry_", suffix=".tmp", dir=str(self.registry_path.parent)
        )
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, self.registry_path)
        try:
            os.chmod(self.registry_path, 0o600)
        except OSError:
            pass

    def get_all_agents(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get all agents categorized by cluster."""
        return self._load_registry_data()

    def get_cluster_agents(self, cluster: str) -> List[Dict[str, Any]]:
        """Get agents for a specific cluster ('agy' or 'codex')."""
        data = self._load_registry_data()
        return data.get(cluster.lower(), [])

    def get_next_agent_index(self, cluster: str) -> int:
        """Calculate next available account index for cluster."""
        agents = self.get_cluster_agents(cluster)
        max_idx = 0
        for a in agents:
            idx = a.get("account_idx", 0)
            if idx > max_idx:
                max_idx = idx
        return max_idx + 1

    def provision_workspace(self, cluster: str, account_idx: int, preset: str = "full_stack") -> Path:
        """
        Auto-provision filesystem directory for the given agent account.
        Creates ~/.ai-accounts/<cluster>/account<idx> and seeds configs.
        """
        cluster_clean = cluster.lower().strip()
        if cluster_clean not in ("agy", "codex"):
            raise ValueError(f"Invalid cluster type: {cluster}. Must be 'agy' or 'codex'.")

        target_dir = self.accounts_root / cluster_clean / f"account{account_idx}"
        target_dir.mkdir(parents=True, exist_ok=True)

        # Scaffolding per cluster type
        if cluster_clean == "agy":
            gemini_dir = target_dir / ".gemini"
            gemini_dir.mkdir(parents=True, exist_ok=True)
            settings_file = gemini_dir / "settings.json"
            if not settings_file.exists():
                settings_file.write_text(
                    json.dumps(
                        {
                            "agent_id": f"agy{account_idx}",
                            "cluster": "agy",
                            "preset": preset,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "status": "active",
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
        else:  # codex
            codex_dir = target_dir / ".codex"
            codex_dir.mkdir(parents=True, exist_ok=True)
            config_file = target_dir / "config.toml"
            if not config_file.exists():
                config_file.write_text(
                    f'account_id = "codex{account_idx}"\n'
                    f'cluster = "codex"\n'
                    f'preset = "{preset}"\n'
                    f'created_at = "{datetime.now(timezone.utc).isoformat()}"\n',
                    encoding="utf-8",
                )
            # Create lightweight SQLite database if missing
            db_path = target_dir / "state.db"
            if not db_path.exists():
                with sqlite3.connect(db_path) as conn:
                    conn.execute("CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, turns INTEGER);")
                    conn.execute("CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, type TEXT);")
                    conn.commit()

        # Common metadata.json
        meta_file = target_dir / "metadata.json"
        if not meta_file.exists():
            meta_file.write_text(
                json.dumps(
                    {
                        "agent_id": f"{cluster_clean}{account_idx}",
                        "cluster": cluster_clean,
                        "account_idx": account_idx,
                        "preset": preset,
                        "status": "active",
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

        return target_dir

    def purge_workspace(self, cluster: str, account_idx: int) -> bool:
        """
        Securely purge filesystem workspace directory for the given agent account.
        Removes ~/.ai-accounts/<cluster>/account<idx> using shutil.rmtree with
        path traversal guards ensuring resolved directory is strictly under
        accounts_root / cluster / account<idx>.
        """
        cluster_clean = cluster.lower().strip()
        if cluster_clean not in ("agy", "codex"):
            raise ValueError(f"Invalid cluster type: {cluster}. Must be 'agy' or 'codex'.")

        try:
            idx = int(account_idx)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid account index: {account_idx}") from e

        if idx <= 0:
            raise ValueError(f"Invalid account index: account_idx must be a positive integer (got {account_idx})")

        cluster_dir = self.accounts_root / cluster_clean
        target_dir = cluster_dir / f"account{idx}"

        # If target doesn't exist at all (and isn't a dangling symlink)
        if not target_dir.exists() and not target_dir.is_symlink():
            return False

        # If target is a symlink or single file, safely unlink without following
        if target_dir.is_symlink() or target_dir.is_file():
            target_dir.unlink()
            return True

        # Target is a directory: verify strict containment under accounts_root / cluster / account<idx>
        target_resolved = target_dir.resolve()
        cluster_resolved = cluster_dir.resolve()

        try:
            rel = target_resolved.relative_to(cluster_resolved)
            if rel.parts != (f"account{idx}",):
                raise ValueError(f"Path traversal detected: {target_dir}")
        except ValueError as e:
            raise ValueError(f"Path traversal detected or invalid workspace target: {target_dir}") from e

        if target_dir.is_dir():
            shutil.rmtree(target_dir)
            return True
        return False

    def add_agent(
        self,
        cluster: str,
        name: Optional[str] = None,
        preset: str = "full_stack",
        custom_quotas: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Any]:
        """
        Dynamically add and auto-provision an agent (e.g. agy6, codex4).
        """
        cluster_clean = cluster.lower().strip()
        if cluster_clean not in ("agy", "codex"):
            raise ValueError(f"Invalid cluster type: {cluster}. Must be 'agy' or 'codex'.")

        next_idx = self.get_next_agent_index(cluster_clean)
        agent_id = f"{cluster_clean}{next_idx}"

        if not name:
            name = f"{cluster_clean.upper()}{next_idx} (Dynamic)"

        # Resolve quota limits from preset or custom values
        p_cfg = PRESETS.get(preset, PRESETS["full_stack"])
        g_limit = p_cfg["gemini_tokens"]
        c_limit = p_cfg["claude_reqs"]
        a_limit = p_cfg["argon_units"]

        if custom_quotas:
            g_limit = custom_quotas.get("gemini_tokens", g_limit)
            c_limit = custom_quotas.get("claude_reqs", c_limit)
            a_limit = custom_quotas.get("argon_units", a_limit)

        # 1. Auto-provision disk workspace
        self.provision_workspace(cluster_clean, next_idx, preset=preset)

        # 2. Construct agent model
        new_agent = {
            "id": agent_id,
            "name": name,
            "cluster": cluster_clean,
            "account_idx": next_idx,
            "status": "healthy",
            "usage_pct": 0.0,
            "preset": preset,
            "gemini_tokens_used": 0,
            "gemini_tokens_limit": g_limit,
            "claude_reqs_used": 0,
            "claude_reqs_limit": c_limit,
            "argon_units_used": 0,
            "argon_units_limit": a_limit,
            "turns": "0",
            "items": "0",
            "is_dynamic": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        # 3. Persist into registry
        data = self._load_registry_data()
        data[cluster_clean].append(new_agent)
        self._save_registry_data(data)

        # 4. Trigger carousel reload
        notify_carousel_reload(self.pid_file)

        return new_agent

    def remove_agent(self, cluster: str, agent_id: str) -> bool:
        """
        Remove a dynamic agent from registry and purge its workspace directory.
        Seed agents (agy1..5, codex1..3) cannot be permanently removed.
        """
        cluster_clean = cluster.lower().strip()
        if cluster_clean not in ("agy", "codex"):
            return False

        agent_id_clean = agent_id.strip()
        # Deny removal of seed agents
        if agent_id_clean.lower() in SEED_AGENT_IDS:
            return False

        data = self._load_registry_data()
        cluster_agents = data.get(cluster_clean, [])

        target = None
        for a in cluster_agents:
            if a.get("id", "").lower() == agent_id_clean.lower():
                target = a
                break

        if not target:
            return False

        # Disallow removal of seed / non-dynamic agents
        if not target.get("is_dynamic", False):
            return False

        account_idx = target.get("account_idx")

        # Atomic registry update with 0600 mode
        initial_len = len(cluster_agents)
        data[cluster_clean] = [
            a for a in cluster_agents if a.get("id", "").lower() != agent_id_clean.lower()
        ]

        if len(data[cluster_clean]) < initial_len:
            self._save_registry_data(data)

            # Purge workspace from disk
            if account_idx is not None:
                try:
                    self.purge_workspace(cluster_clean, account_idx)
                except Exception as e:
                    logger.error("Failed to purge workspace for %s/account%s: %s", cluster_clean, account_idx, e)

            # Trigger carousel reload
            notify_carousel_reload(self.pid_file)
            return True

        return False

    def update_agent_quotas(self, agent_id: str, quotas: Dict[str, int]) -> bool:
        """Update quota ceilings for an agent."""
        data = self._load_registry_data()
        updated = False
        for cluster_agents in data.values():
            for a in cluster_agents:
                if a.get("id") == agent_id:
                    if "gemini_tokens" in quotas:
                        a["gemini_tokens_limit"] = int(quotas["gemini_tokens"])
                    if "claude_reqs" in quotas:
                        a["claude_reqs_limit"] = int(quotas["claude_reqs"])
                    if "argon_units" in quotas:
                        a["argon_units_limit"] = int(quotas["argon_units"])
                    updated = True
                    break

        if updated:
            self._save_registry_data(data)
            return True
        return False

    def get_reset_schedule_info(self) -> Dict[str, Any]:
        """
        Calculate next automated reset schedule (00:00 UTC).
        """
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        next_reset = today_midnight + timedelta(days=1)

        diff = next_reset - now
        total_seconds = max(0, int(diff.total_seconds()))
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60

        return {
            "policy": "Pure Automated Cron Reset",
            "schedule": "0 0 * * *",
            "target_epoch": "00:00 UTC",
            "next_reset_iso": next_reset.isoformat(),
            "time_remaining": f"{hours:02d}h {minutes:02d}m",
            "seconds_remaining": total_seconds,
        }
