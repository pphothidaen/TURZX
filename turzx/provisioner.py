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
    "codex_standard": {
        "name": "Codex Standard (OpenAI)",
        "description": "OpenAI Codex engine with GPT-5.6 / medium reasoning effort",
        "model": "gpt-5.6-terra",
        "reasoning_effort": "medium",
        "gemini_tokens": 0,
        "claude_reqs": 0,
        "argon_units": 0,
    },
    "codex_heavy": {
        "name": "Codex High-Reasoning",
        "description": "OpenAI Codex engine with GPT-5.6 / high reasoning effort",
        "model": "gpt-5.6-terra",
        "reasoning_effort": "high",
        "gemini_tokens": 0,
        "claude_reqs": 0,
        "argon_units": 0,
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

DEFAULT_AGENT_EMAILS: Dict[str, str] = {
    "agy1": "pansakorn@gmail.com",
    "agy2": "pansakorn.pho@gmail.com",
    "agy3": "kimlenglim.work@gmail.com",
    "agy4": "kimlenglimp11@gmail.com",
    "agy5": "emkimlenglim@gmail.com",
    "codex1": "kimlenglimp11@gmail.com",
    "codex2": "pansakorn@gmail.com",
    "codex3": "taijustarrett417@gmail.com",
}


def resolve_account_email(accounts_root: Path | str, cluster: str, account_idx: int) -> str:
    """
    Attempt to discover account email from filesystem workspace, auth tokens, gitconfig,
    or default seeded mapping.
    """
    import base64
    cluster_clean = str(cluster).lower().strip()
    agent_id = f"{cluster_clean}{account_idx}"

    acc_path = Path(accounts_root) / cluster_clean / f"account{account_idx}"

    # 1. Check metadata.json for explicit override
    if acc_path.exists():
        meta_path = acc_path / "metadata.json"
        if meta_path.is_file():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                    if isinstance(meta, dict) and meta.get("email"):
                        return str(meta["email"]).strip()
            except Exception:
                pass

    # 2. Check canonical seed mapping
    if agent_id in DEFAULT_AGENT_EMAILS:
        return DEFAULT_AGENT_EMAILS[agent_id]

    # 3. For dynamic accounts: check auth.json (JWT payload or plaintext profile)
    if acc_path.exists():
        auth_path = acc_path / "auth.json"
        if auth_path.is_file():
            try:
                auth_data = json.loads(auth_path.read_text(encoding="utf-8"))
                profile = auth_data.get("tokens", {}).get("profile") or auth_data.get("profile")
                if isinstance(profile, dict) and profile.get("email"):
                    return str(profile["email"]).strip()
                id_token = auth_data.get("tokens", {}).get("id_token")
                if id_token and isinstance(id_token, str) and "." in id_token:
                    parts = id_token.split(".")
                    if len(parts) >= 2:
                        payload_b64 = parts[1]
                        payload_b64 += "=" * (-len(payload_b64) % 4)
                        payload_json = json.loads(base64.urlsafe_b64decode(payload_b64))
                        if payload_json.get("email"):
                            return str(payload_json["email"]).strip()
            except Exception:
                pass

        # 4. Check .gitconfig
        gitconfig_path = acc_path / ".gitconfig"
        if gitconfig_path.is_file():
            try:
                for line in gitconfig_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("email") and "=" in line:
                        em = line.split("=", 1)[1].strip()
                        if "@" in em:
                            return em
            except Exception:
                pass

    return f"{cluster_clean}{account_idx}@agent.local"


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
    email: str = ""
    status: str = "healthy"  # "healthy", "warning", "critical", "idle"
    usage_pct: float = 0.0
    preset: str = "full_stack"
    gemini_tokens_used: int = 0
    gemini_tokens_limit: int = 0
    claude_reqs_used: int = 0
    claude_reqs_limit: int = 0
    argon_units_used: int = 0
    argon_units_limit: int = 0
    turns: str = "0"
    items: str = "0"
    primary_used_pct: float = 0.0
    primary_reset: str = "2026-11-17 14:53"
    secondary_used_pct: float = 0.0
    secondary_reset: str = "05h 20m"
    model: str = "gpt-5.6-terra"
    reasoning_effort: str = "medium"
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
                "email": DEFAULT_AGENT_EMAILS.get("agy1", "pansakorn@gmail.com"),
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
                "email": DEFAULT_AGENT_EMAILS.get("agy2", "pansakorn.pho@gmail.com"),
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
                "email": DEFAULT_AGENT_EMAILS.get("agy3", "kimlenglim.work@gmail.com"),
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
                "email": DEFAULT_AGENT_EMAILS.get("agy4", "kimlenglimp11@gmail.com"),
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
                "email": DEFAULT_AGENT_EMAILS.get("agy5", "emkimlenglim@gmail.com"),
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
                "email": DEFAULT_AGENT_EMAILS.get("codex1", "kimlenglimp11@gmail.com"),
                "status": "healthy",
                "usage_pct": 23.0,
                "preset": "codex_standard",
                "primary_used_pct": 23.0,
                "primary_reset": "2026-11-17 14:53",
                "secondary_used_pct": 5.0,
                "secondary_reset": "05h 20m",
                "model": "gpt-5.6-terra",
                "reasoning_effort": "medium",
                "gemini_tokens_used": 0,
                "gemini_tokens_limit": 0,
                "claude_reqs_used": 0,
                "claude_reqs_limit": 0,
                "argon_units_used": 0,
                "argon_units_limit": 0,
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
                "email": DEFAULT_AGENT_EMAILS.get("codex2", "pansakorn@gmail.com"),
                "status": "healthy",
                "usage_pct": 45.0,
                "preset": "codex_standard",
                "primary_used_pct": 45.0,
                "primary_reset": "2026-11-18 10:30",
                "secondary_used_pct": 12.0,
                "secondary_reset": "05h 20m",
                "model": "gpt-5.6-terra",
                "reasoning_effort": "high",
                "gemini_tokens_used": 0,
                "gemini_tokens_limit": 0,
                "claude_reqs_used": 0,
                "claude_reqs_limit": 0,
                "argon_units_used": 0,
                "argon_units_limit": 0,
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
                "email": DEFAULT_AGENT_EMAILS.get("codex3", "taijustarrett417@gmail.com"),
                "status": "healthy",
                "usage_pct": 12.0,
                "preset": "codex_standard",
                "primary_used_pct": 12.0,
                "primary_reset": "2026-11-19 08:15",
                "secondary_used_pct": 0.0,
                "secondary_reset": "05h 20m",
                "model": "gpt-5.6-terra",
                "reasoning_effort": "medium",
                "gemini_tokens_used": 0,
                "gemini_tokens_limit": 0,
                "claude_reqs_used": 0,
                "claude_reqs_limit": 0,
                "argon_units_used": 0,
                "argon_units_limit": 0,
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
                    needs_save = False
                    for cluster_key in ("agy", "codex"):
                        for ag in data.get(cluster_key, []):
                            aid = ag.get("id", "").lower()
                            # If email is missing or generic git committer email, assign canonical account email
                            if not ag.get("email") or ag.get("email") == "pansakorn@hotmail.com":
                                if aid in DEFAULT_AGENT_EMAILS:
                                    ag["email"] = DEFAULT_AGENT_EMAILS[aid]
                                    needs_save = True
                                else:
                                    ag["email"] = resolve_account_email(
                                        self.accounts_root,
                                        cluster_key,
                                        ag.get("account_idx", 1),
                                    )
                                    needs_save = True

                    # Sanitize codex agents defensively against legacy Claude/Argon/Gemini fields
                    for cdx in data.get("codex", []):
                        cdx["gemini_tokens_used"] = 0
                        cdx["gemini_tokens_limit"] = 0
                        cdx["claude_reqs_used"] = 0
                        cdx["claude_reqs_limit"] = 0
                        cdx["argon_units_used"] = 0
                        cdx["argon_units_limit"] = 0
                        m = str(cdx.get("model", "gpt-5.6-terra"))
                        if not m or any(bad in m.lower() for bad in ("claude", "gemini", "argon")):
                            cdx["model"] = "gpt-5.6-terra"
                        p = str(cdx.get("preset", "codex_standard"))
                        if not p or any(bad in p.lower() for bad in ("claude", "gemini", "argon")):
                            cdx["preset"] = "codex_standard"

                    if needs_save:
                        self._save_registry_data(data)
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

    def provision_workspace(
        self, cluster: str, account_idx: int, preset: str = "full_stack", email: Optional[str] = None
    ) -> Path:
        """
        Auto-provision filesystem directory for the given agent account.
        Creates ~/.ai-accounts/<cluster>/account<idx> and seeds configs.
        """
        cluster_clean = cluster.lower().strip()
        if cluster_clean not in ("agy", "codex"):
            raise ValueError(f"Invalid cluster type: {cluster}. Must be 'agy' or 'codex'.")

        target_dir = self.accounts_root / cluster_clean / f"account{account_idx}"
        target_dir.mkdir(parents=True, exist_ok=True)

        assigned_email = email or resolve_account_email(self.accounts_root, cluster_clean, account_idx)

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
                            "email": assigned_email,
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
                    f'email = "{assigned_email}"\n'
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
                        "email": assigned_email,
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
        preset: Optional[str] = None,
        custom_quotas: Optional[Dict[str, Any]] = None,
        email: Optional[str] = None,
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

        assigned_email = email or resolve_account_email(self.accounts_root, cluster_clean, next_idx)

        # Set appropriate cluster-specific preset defaults
        if cluster_clean == "codex":
            if not preset or preset not in ("codex_standard", "codex_heavy"):
                preset = "codex_standard"
        else:
            if not preset or preset not in PRESETS:
                preset = "full_stack"

        # Resolve quota limits from preset or custom values
        p_cfg = PRESETS.get(preset, PRESETS["codex_standard" if cluster_clean == "codex" else "full_stack"])

        if cluster_clean == "codex":
            # Codex agents have NO Gemini, Claude, or Argon
            g_limit = 0
            c_limit = 0
            a_limit = 0
            model = "gpt-5.6-terra"
            reasoning_effort = p_cfg.get("reasoning_effort", "medium")
            primary_used = 0.0
            primary_reset = "2026-11-17 14:53"
            secondary_used = 0.0
            secondary_reset = "05h 20m"

            if custom_quotas:
                if "model" in custom_quotas:
                    m = str(custom_quotas["model"])
                    if not any(bad in m.lower() for bad in ("claude", "gemini", "argon")):
                        model = m
                if "reasoning_effort" in custom_quotas:
                    reasoning_effort = str(custom_quotas["reasoning_effort"])
                if "primary_used_pct" in custom_quotas:
                    primary_used = float(custom_quotas["primary_used_pct"])
                if "primary_reset" in custom_quotas:
                    primary_reset = str(custom_quotas["primary_reset"])
                if "secondary_used_pct" in custom_quotas:
                    secondary_used = float(custom_quotas["secondary_used_pct"])
                if "secondary_reset" in custom_quotas:
                    secondary_reset = str(custom_quotas["secondary_reset"])
                if "email" in custom_quotas and not email:
                    assigned_email = str(custom_quotas["email"])
        else:
            g_limit = p_cfg.get("gemini_tokens", 500_000)
            c_limit = p_cfg.get("claude_reqs", 50)
            a_limit = p_cfg.get("argon_units", 25)

            if custom_quotas:
                g_limit = custom_quotas.get("gemini_tokens", g_limit)
                c_limit = custom_quotas.get("claude_reqs", c_limit)
                a_limit = custom_quotas.get("argon_units", a_limit)
                if "email" in custom_quotas and not email:
                    assigned_email = str(custom_quotas["email"])

        # 1. Auto-provision disk workspace
        self.provision_workspace(cluster_clean, next_idx, preset=preset, email=assigned_email)

        # 2. Construct agent model
        new_agent = {
            "id": agent_id,
            "name": name,
            "cluster": cluster_clean,
            "account_idx": next_idx,
            "email": assigned_email,
            "status": "healthy",
            "usage_pct": primary_used if cluster_clean == "codex" else 0.0,
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

        if cluster_clean == "codex":
            new_agent["model"] = model
            new_agent["reasoning_effort"] = reasoning_effort
            new_agent["primary_used_pct"] = primary_used
            new_agent["primary_reset"] = primary_reset
            new_agent["secondary_used_pct"] = secondary_used
            new_agent["secondary_reset"] = secondary_reset

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

    def update_agent_quotas(self, agent_id: str, quotas: Dict[str, Any]) -> bool:
        """Update quota ceilings for an agent."""
        data = self._load_registry_data()
        updated = False
        for cluster_agents in data.values():
            for a in cluster_agents:
                if a.get("id") == agent_id:
                    is_codex = a.get("cluster") == "codex" or agent_id.startswith("codex")
                    if is_codex:
                        # Strictly prevent Claude, Argon, Gemini on Codex
                        a["gemini_tokens_used"] = 0
                        a["gemini_tokens_limit"] = 0
                        a["claude_reqs_used"] = 0
                        a["claude_reqs_limit"] = 0
                        a["argon_units_used"] = 0
                        a["argon_units_limit"] = 0
                        if "model" in quotas:
                            m = str(quotas["model"])
                            if not any(bad in m.lower() for bad in ("claude", "gemini", "argon")):
                                a["model"] = m
                            else:
                                a["model"] = "gpt-5.6-terra"
                        if "reasoning_effort" in quotas:
                            a["reasoning_effort"] = str(quotas["reasoning_effort"])
                        if "primary_used_pct" in quotas:
                            a["primary_used_pct"] = float(quotas["primary_used_pct"])
                            a["usage_pct"] = float(quotas["primary_used_pct"])
                        if "secondary_used_pct" in quotas:
                            a["secondary_used_pct"] = float(quotas["secondary_used_pct"])
                        if "primary_reset" in quotas:
                            a["primary_reset"] = str(quotas["primary_reset"])
                        if "secondary_reset" in quotas:
                            a["secondary_reset"] = str(quotas["secondary_reset"])
                    else:
                        if "gemini_tokens" in quotas:
                            a["gemini_tokens_limit"] = int(quotas["gemini_tokens"])
                        if "claude_reqs" in quotas:
                            a["claude_reqs_limit"] = int(quotas["claude_reqs"])
                        if "argon_units" in quotas:
                            a["argon_units_limit"] = int(quotas["argon_units"])
                    if "email" in quotas:
                        a["email"] = str(quotas["email"])
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
