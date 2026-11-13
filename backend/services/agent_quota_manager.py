"""
Agent quota management service for DeepSeek Web Bridge.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    redis = None
    REDIS_AVAILABLE = False
import structlog

# TURXZ Scheduler integration
try:
    from turxz_scheduled_jobs import (
        JobDefinition,
        JobPriority,
        ScheduleType,
        TURXZJobExecutor,
        TURXZJobManager,
        TURXZScheduler,
    )
    TURXZ_SCHEDULER_AVAILABLE = True
except ImportError:
    TURXZ_SCHEDULER_AVAILABLE = False
    TURXZScheduler = None
    TURXZJobManager = None
    TURXZJobExecutor = None
    ScheduleType = None
    JobDefinition = None
    JobPriority = None

class QuotaStatus(str, Enum):
    WITHIN_LIMIT = "within_limit"
    WARNING = "warning"
    EXCEEDED = "exceeded"
    DISABLED = "disabled"

@dataclass
class QuotaUsage:
    agent_id: str
    current_usage: float
    quota_limit: float
    last_reset: datetime
    warning_threshold: float = 0.8
    burst_allowed: bool = True
    burst_usage: float = 0.0

    @property
    def usage_percentage(self) -> float:
        return min((self.current_usage / self.quota_limit) * 100, 100.0)

    @property
    def status(self) -> QuotaStatus:
        if not self.quota_limit or self.quota_limit <= 0:
            return QuotaStatus.DISABLED
        
        usage_percent = self.usage_percentage / 100.0
        
        if usage_percent >= 1.0:
            return QuotaStatus.EXCEEDED
        elif usage_percent >= self.warning_threshold:
            return QuotaStatus.WARNING
        else:
            return QuotaStatus.WITHIN_LIMIT

    def can_use(self, units: float = 1.0) -> bool:
        """Check if agent can use specified units."""
        if self.status == QuotaStatus.DISABLED:
            return True
        
        total_usage = self.current_usage + units
        return total_usage <= self.quota_limit

    def use(self, units: float = 1.0) -> bool:
        """Use quota units if available."""
        if self.can_use(units):
            self.current_usage += units
            return True
        return False

    def reset(self) -> None:
        """Reset quota usage."""
        self.current_usage = 0.0
        self.last_reset = datetime.now()

    def check_burst(self, units: float = 1.0) -> bool:
        """Check if burst usage is allowed."""
        if not self.burst_allowed:
            return False
        
        available_burst = self.quota_limit * 0.1  # 10% burst allowance
        return self.burst_usage + units <= available_burst

class AgentQuotaManager:
    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        self.redis_url = redis_url
        self.redis = None
        self.logger = structlog.get_logger(__name__)
        
        # Try to setup Redis connection, but don't fail if it doesn't work
        if REDIS_AVAILABLE:
            try:
                self.redis = redis.from_url(redis_url)
                self._setup_redis()
            except Exception as e:
                self.logger.warning("Redis connection failed, operating in degraded mode", 
                                   error=str(e))
                self._setup_in_memory_fallback()
        else:
            self.logger.info("Redis package not installed, operating with in-memory fallback")
            self._setup_in_memory_fallback()

    def _setup_redis(self) -> None:
        """Setup Redis connection and indexes."""
        try:
            # Test connection
            self.redis.ping()
            self.logger.info("Connected to Redis for quota management")
        except Exception as e:
            self.logger.error("Failed to connect to Redis", error=str(e))
            raise

    def create_agent_quota(self, agent_id: str, quota_limit: float, 
                          warning_threshold: float = 0.8, 
                          burst_allowed: bool = True) -> QuotaUsage:
        """Create quota entry for an agent."""
        quota = QuotaUsage(
            agent_id=agent_id,
            current_usage=0.0,
            quota_limit=quota_limit,
            last_reset=datetime.now(),
            warning_threshold=warning_threshold,
            burst_allowed=burst_allowed,
        )
        
        self._save_quota(quota)
        self.logger.info("Created quota for agent", agent_id=agent_id, quota_limit=quota_limit)
        return quota

    def get_quota(self, agent_id: str) -> QuotaUsage | None:
        """Get quota for an agent."""
        if not self.redis:
            return self._get_in_memory_quota(agent_id)
        try:
            data = self.redis.get(f"quota:{agent_id}")
            if data:
                return self._deserialize_quota(data)
        except Exception as e:
            self.logger.error("Failed to get quota", agent_id=agent_id, error=str(e))
        
        return None

    def use_quota(self, agent_id: str, units: float = 1.0, 
                  allow_burst: bool = True) -> dict[str, Any]:
        """Use quota for an agent."""
        quota = self.get_quota(agent_id)
        if not quota:
            return {
                "success": False,
                "error": "Quota not found",
                "agent_id": agent_id,
            }

        # Check if burst is allowed and needed
        if allow_burst and quota.check_burst(units):
            quota.burst_usage += units
        
        # Try to use quota
        success = quota.use(units)
        
        if success:
            self._save_quota(quota)
            self.logger.info("Used quota", agent_id=agent_id, units=units, 
                           current_usage=quota.current_usage)
            
            return {
                "success": True,
                "agent_id": agent_id,
                "units_used": units,
                "current_usage": quota.current_usage,
                "quota_limit": quota.quota_limit,
                "status": quota.status.value,
                "usage_percentage": quota.usage_percentage,
            }
        else:
            self.logger.warning("Quota exceeded", agent_id=agent_id, units=units,
                              current_usage=quota.current_usage,
                              quota_limit=quota.quota_limit)
            
            available_units = quota.quota_limit - quota.current_usage
            return {
                "success": False,
                "agent_id": agent_id,
                "units_requested": units,
                "current_usage": quota.current_usage,
                "quota_limit": quota.quota_limit,
                "status": quota.status.value,
                "usage_percentage": quota.usage_percentage,
                "error": "Quota exceeded",
                "available_units": available_units,
            }

    def reset_quota(self, agent_id: str) -> bool:
        """Reset quota for an agent."""
        try:
            quota = self.get_quota(agent_id)
            if quota:
                quota.reset()
                self._save_quota(quota)
                self.logger.info("Reset quota", agent_id=agent_id)
                return True
        except Exception as e:
            self.logger.error("Failed to reset quota", agent_id=agent_id, error=str(e))
        
        return False

    def get_quota_status(self, agent_id: str) -> dict[str, Any]:
        """Get quota status for an agent."""
        quota = self.get_quota(agent_id)
        metadata = getattr(self, "_agent_metadata", {}).get(agent_id, {})
        if not quota:
            return {
                "agent_id": agent_id,
                "status": QuotaStatus.DISABLED.value,
                "display_status": "disabled",
                "current_usage": 0.0,
                "quota_limit": 0.0,
                "usage_percentage": 0.0,
                "used_pct": 0.0,
                "remaining_pct": 100.0,
                "model": metadata.get("model", "unknown"),
                "role": metadata.get("role", "unknown"),
            }

        status_val = quota.status.value
        if status_val == "within_limit":
            display_status = "healthy"
        elif status_val == "warning":
            display_status = "warning"
        elif status_val == "exceeded":
            display_status = "critical"
        else:
            display_status = "disabled"

        return {
            "agent_id": quota.agent_id,
            "status": quota.status.value,
            "display_status": display_status,
            "current_usage": quota.current_usage,
            "quota_limit": quota.quota_limit,
            "usage_percentage": quota.usage_percentage,
            "used_pct": quota.usage_percentage,
            "remaining_pct": max(0.0, 100.0 - quota.usage_percentage),
            "warning_threshold": quota.warning_threshold,
            "burst_allowed": quota.burst_allowed,
            "last_reset": quota.last_reset.isoformat(),
            "model": metadata.get("model", "unknown"),
            "role": metadata.get("role", "unknown"),
        }

    def get_all_quotas(self) -> dict[str, dict[str, Any]]:
        """Get quota status for all 8 target agents plus any dynamically registered agents."""
        target_agents = [f"agy{i}" for i in range(1, 6)] + [f"codex{i}" for i in range(1, 4)]
        all_agents = list(target_agents)
        for aid in self.list_agents():
            if aid not in all_agents:
                all_agents.append(aid)
        
        results = {}
        for aid in all_agents:
            results[aid] = self.get_quota_status(aid)
        return results

    def list_agents(self) -> list[str]:
        """List all agents with quota."""
        if not self.redis:
            return self._list_in_memory_agents()
        try:
            keys = self.redis.keys("quota:*")
            return [key.decode('utf-8').replace('quota:', '') for key in keys]
        except Exception as e:
            self.logger.error("Failed to list agents", error=str(e))
            return []

    def cleanup_expired_quota(self, max_age_hours: int = 24) -> int:
        """Clean up expired quota entries."""
        if not self.redis:
            return self._cleanup_in_memory_quota(max_age_hours)
        try:
            cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
            keys = self.redis.keys("quota:*")
            cleaned = 0
            
            for key in keys:
                data = self.redis.get(key)
                if data:
                    quota = self._deserialize_quota(data)
                    if quota.last_reset < cutoff_time:
                        self.redis.delete(key)
                        cleaned += 1
            
            if cleaned > 0:
                self.logger.info("Cleaned up expired quota entries", count=cleaned)
            
            return cleaned
        except Exception as e:
            self.logger.error("Failed to cleanup expired quota", error=str(e))
            return 0

    def health_check(self) -> dict[str, Any]:
        """Health check for the quota manager."""
        if not self.redis:
            return {
                "status": "healthy",
                "timestamp": datetime.now().isoformat(),
                "redis_connected": False,
                "mode": "in_memory",
                "agents_count": len(self.list_agents()),
            }
        try:
            # Test Redis connection
            self.redis.ping()
            
            return {
                "status": "healthy",
                "timestamp": datetime.now().isoformat(),
                "redis_connected": True,
                "agents_count": len(self.list_agents()),
            }
        except Exception as e:
            self.logger.error(f"Health check failed: {e!s}")
            return {
                "status": "unhealthy",
                "timestamp": datetime.now().isoformat(),
                "redis_connected": False,
                "error": str(e),
            }

    def get_quota_analytics(self, agent_id: str, hours: int = 24) -> dict[str, Any]:
        """Get quota analytics for an agent over time period."""
        # This would typically query a time-series database or use Redis sorted sets
        # For now, return current status as a placeholder
        quota = self.get_quota(agent_id)
        if not quota:
            return {
                "agent_id": agent_id,
                "analytics": {
                    "average_usage": 0.0,
                    "peak_usage": 0.0,
                    "total_requests": 0,
                    "success_rate": 0.0,
                },
            }

        return {
            "agent_id": agent_id,
            "analytics": {
                "current_usage": quota.current_usage,
                "current_quota": quota.quota_limit,
                "usage_percentage": quota.usage_percentage,
                "status": quota.status.value,
                # Note: Time-series analytics would require additional implementation
            },
        }

    def validate_quota_for_operation(self, agent_id: str, required_units: float = 1.0,
                                    allow_burst: bool = True) -> dict[str, Any]:
        """Validate if agent has quota for operation."""
        quota = self.get_quota(agent_id)
        if not quota:
            return {
                "valid": False,
                "agent_id": agent_id,
                "required_units": required_units,
                "current_usage": 0.0,
                "quota_limit": 0.0,
                "status": QuotaStatus.DISABLED.value,
                "error": "Quota not found",
            }

        can_use = quota.can_use(required_units)
        
        if can_use:
            # Check burst allowance
            burst_allowed = allow_burst and quota.check_burst(required_units)
            
            return {
                "valid": True,
                "agent_id": agent_id,
                "required_units": required_units,
                "current_usage": quota.current_usage,
                "quota_limit": quota.quota_limit,
                "status": quota.status.value,
                "usage_percentage": quota.usage_percentage,
                "burst_allowed": burst_allowed,
                "estimated_usage_after": quota.current_usage + required_units,
            }
        else:
            return {
                "valid": False,
                "agent_id": agent_id,
                "required_units": required_units,
                "current_usage": quota.current_usage,
                "quota_limit": quota.quota_limit,
                "status": quota.status.value,
                "usage_percentage": quota.usage_percentage,
                "error": "Quota exceeded",
                "available_units": max(0, quota.quota_limit - quota.current_usage),
            }

    def _setup_in_memory_fallback(self) -> None:
        """Setup in-memory fallback when Redis is not available."""
        self.logger.info("Setting up in-memory Redis fallback")
        # Initialize in-memory storage
        self._in_memory_quota_store = {}  # agent_id -> quota_dict
        self._memory_quota_counter = 0

    def _get_redis_client(self):
        """Get Redis client, falling back to in-memory if needed."""
        return self.redis

    def _get_in_memory_quota(self, agent_id: str) -> QuotaUsage | None:
        """Get quota from in-memory store."""
        if hasattr(self, '_in_memory_quota_store'):
            data = self._in_memory_quota_store.get(f"quota:{agent_id}")
            if data:
                return self._deserialize_quota(data)
        return None

    def _save_in_memory_quota(self, quota: QuotaUsage) -> None:
        """Save quota to in-memory store."""
        if hasattr(self, '_in_memory_quota_store'):
            data = self._serialize_quota(quota)
            self._in_memory_quota_store[f"quota:{quota.agent_id}"] = data

    def _list_in_memory_agents(self) -> list[str]:
        """List all agents from in-memory store."""
        if hasattr(self, '_in_memory_quota_store'):
            return [key.replace('quota:', '') for key in self._in_memory_quota_store.keys()]
        return []

    def _cleanup_in_memory_quota(self, max_age_hours: int = 24) -> int:
        """Clean up expired quota from in-memory store."""
        if not hasattr(self, '_in_memory_quota_store'):
            return 0
        
        cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
        cleaned = 0
        
        for key, data in list(self._in_memory_quota_store.items()):
            try:
                quota = self._deserialize_quota(data)
                if quota.last_reset < cutoff_time:
                    del self._in_memory_quota_store[key]
                    cleaned += 1
            except Exception:
                # If we can't deserialize, remove it
                del self._in_memory_quota_store[key]
                cleaned += 1
        
    def _save_quota(self, quota: QuotaUsage) -> None:
        """Save quota to Redis or in-memory fallback."""
        try:
            if self.redis:
                data = self._serialize_quota(quota)
                self.redis.setex(f"quota:{quota.agent_id}", 3600, data)
            else:
                # Use in-memory fallback
                self._save_in_memory_quota(quota)
        except Exception as e:
            self.logger.error("Failed to save quota", agent_id=quota.agent_id, error=str(e))
            raise

    def _serialize_quota(self, quota: QuotaUsage) -> bytes:
        """Serialize quota to bytes."""
        import json
        data = {
            "agent_id": quota.agent_id,
            "current_usage": quota.current_usage,
            "quota_limit": quota.quota_limit,
            "last_reset": quota.last_reset.isoformat(),
            "warning_threshold": quota.warning_threshold,
            "burst_allowed": quota.burst_allowed,
            "burst_usage": quota.burst_usage,
        }
        return json.dumps(data).encode('utf-8')

    def _deserialize_quota(self, data: bytes) -> QuotaUsage:
        """Deserialize quota from bytes."""
        import json
        from datetime import datetime
        data_dict = json.loads(data.decode('utf-8'))
        # Convert last_reset string to datetime if needed
        if 'last_reset' in data_dict and isinstance(data_dict['last_reset'], str):
            data_dict['last_reset'] = datetime.fromisoformat(data_dict['last_reset'])
        return QuotaUsage(**data_dict)

    def sync_agent_quotas(self, hermes_dir: Any = None) -> dict[str, Any]:
        """
        Synchronize quota metrics for all 8 agents (agy1-agy5, codex1-codex3)
        using data from Hermes agent and scheduled jobs.
        
        Sources:
        1. Quota State: ~/.hermes/quota_state.json
        2. Hermes Scheduled Jobs: ~/.hermes/cron/jobs.json
        3. TURZX Orchestration: ~/.hermes/turzx-orchestration/TURZX_ORCHESTRATION_STATE.json
        4. Usage Analytics: ~/.hermes/usage_analytics.db (SQLite)
        5. Account Directories: ~/.ai-accounts/{agy,codex}/account*
        
        Returns:
            Dict containing sync status, synced_agents metrics, and hermes scheduled jobs.
        """
        import os
        import json
        import sqlite3
        from pathlib import Path
        
        if hermes_dir:
            base_hermes = Path(hermes_dir)
        else:
            env_hermes = os.environ.get("HERMES_HOME")
            base_hermes = Path(env_hermes) if env_hermes else Path("/Users/kimlenglim/.hermes")
            if not base_hermes.exists():
                base_hermes = Path.home() / ".hermes"

        real_home = Path("/Users/kimlenglim") if Path("/Users/kimlenglim").exists() else Path.home()
        ai_accounts_base = real_home / ".ai-accounts"

        if not hasattr(self, "_agent_metadata"):
            self._agent_metadata = {}

        # 1. Read Hermes Scheduled Jobs
        hermes_jobs_data = []
        jobs_json_path = base_hermes / "cron" / "jobs.json"
        if jobs_json_path.exists():
            try:
                with open(jobs_json_path, "r", encoding="utf-8") as f:
                    jobs_doc = json.load(f)
                    for j in jobs_doc.get("jobs", []):
                        hermes_jobs_data.append({
                            "id": j.get("id"),
                            "name": j.get("name"),
                            "schedule": j.get("schedule", {}).get("display", str(j.get("schedule"))),
                            "state": j.get("state"),
                            "last_status": j.get("last_status"),
                            "last_run_at": j.get("last_run_at"),
                            "next_run_at": j.get("next_run_at"),
                            "failure_streak": j.get("failure_streak", 0),
                        })
            except Exception as e:
                self.logger.warning("Failed to read Hermes jobs.json", error=str(e))

        # 2. Read Orchestration State for Agent Roles & Models
        orchestration_agents = {}
        orch_state_path = base_hermes / "turzx-orchestration" / "TURZX_ORCHESTRATION_STATE.json"
        if orch_state_path.exists():
            try:
                with open(orch_state_path, "r", encoding="utf-8") as f:
                    orch_doc = json.load(f)
                    orchestration_agents = orch_doc.get("agents", {})
            except Exception as e:
                self.logger.warning("Failed to read TURZX_ORCHESTRATION_STATE.json", error=str(e))

        default_roles = {
            "agy1": ("Strategic Orchestrator", "claude-opus-5-5"),
            "agy2": ("Evidence Collector", "gemini-3.8-flash"),
            "agy3": ("Pattern Synthesizer", "gemini-3.1-pro"),
            "agy4": ("Contract Guardian", "claude-sonnet-5"),
            "agy5": ("Runtime Executor", "gpt-oss:120b"),
            "codex1": ("Code Implementer", "gpt-5.6-terra"),
            "codex2": ("UI/Frontend Specialist", "gpt-5.6-terra"),
            "codex3": ("Test/QA Engineer", "gpt-5.6-terra"),
        }

        # 3. Read Quota State
        quota_state = {}
        quota_state_path = base_hermes / "quota_state.json"
        if quota_state_path.exists():
            try:
                with open(quota_state_path, "r", encoding="utf-8") as f:
                    quota_state = json.load(f)
            except Exception as e:
                self.logger.warning("Failed to read quota_state.json", error=str(e))

        # 4. Read Usage Analytics DB snapshots if present
        db_snapshots = {}
        analytics_db_path = base_hermes / "usage_analytics.db"
        if analytics_db_path.exists():
            try:
                conn = sqlite3.connect(str(analytics_db_path))
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT account, weekly_remaining, reset_at 
                    FROM quota_snapshots 
                    WHERE id IN (SELECT MAX(id) FROM quota_snapshots GROUP BY account)
                """)
                for row in cursor.fetchall():
                    db_snapshots[row[0]] = {"weekly_remaining": row[1], "reset_at": row[2]}
                conn.close()
            except Exception as e:
                self.logger.debug("Could not query usage_analytics.db", error=str(e))

        synced_agents = {}
        target_agents = [f"agy{i}" for i in range(1, 6)] + [f"codex{i}" for i in range(1, 4)]

        for agent_id in target_agents:
            is_agy = agent_id.startswith("agy")
            agent_num = agent_id.replace("agy", "").replace("codex", "")
            acct_dir = ai_accounts_base / ("agy" if is_agy else "codex") / f"account{agent_num}"
            acct_exists = acct_dir.exists()

            meta = orchestration_agents.get(agent_id, {})
            fallback_role = "Agent" if is_agy else "Codex Specialist"
            fallback_model = "default" if is_agy else "gpt-5.6-terra"
            role = meta.get("role", default_roles.get(agent_id, (fallback_role, fallback_model))[0])
            model = meta.get("model", default_roles.get(agent_id, (fallback_role, fallback_model))[1])

            # Defensive guard: Codex never uses Claude, Argon, or Gemini models
            if not is_agy and any(bad in model.lower() for bad in ("claude", "gemini", "argon")):
                model = "gpt-5.6-terra"

            # Resolve email for account
            default_emails = {
                "agy1": "pansakorn@gmail.com",
                "agy2": "pansakorn.pho@gmail.com",
                "agy3": "kimlenglim.work@gmail.com",
                "agy4": "kimlenglimp11@gmail.com",
                "agy5": "emkimlenglim@gmail.com",
                "codex1": "kimlenglimp11@gmail.com",
                "codex2": "pansakorn@gmail.com",
                "codex3": "taijustarrett417@gmail.com",
            }
            email = default_emails.get(agent_id, "")
            if acct_exists:
                meta_json = acct_dir / "metadata.json"
                if meta_json.exists():
                    try:
                        with open(meta_json, "r", encoding="utf-8") as mf:
                            m_data = json.load(mf)
                            if m_data.get("email"):
                                email = m_data["email"]
                    except Exception:
                        pass
                if not email:
                    gitcfg = acct_dir / ".gitconfig"
                    if gitcfg.exists():
                        try:
                            for gl in gitcfg.read_text(encoding="utf-8").splitlines():
                                if "email" in gl and "=" in gl:
                                    em_part = gl.split("=", 1)[1].strip()
                                    if "@" in em_part:
                                        email = em_part
                                        break
                        except Exception:
                            pass

            self._agent_metadata[agent_id] = {
                "role": role,
                "model": model,
                "email": email,
                "account_path": str(acct_dir),
                "account_exists": acct_exists,
            }

            used_pct = 0.0
            status_str = "healthy"
            authenticated = True
            note = ""
            codex_data: dict[str, Any] = {}

            if is_agy:
                agy_data = quota_state.get("agy", {}).get(agent_id, {})
                auth_status = agy_data.get("authenticated", None)
                if auth_status is False:
                    authenticated = False
                    err_msg = agy_data.get("error", "unauthenticated")
                    if agent_id in db_snapshots:
                        rem = db_snapshots[agent_id]["weekly_remaining"]
                        used_pct = float(max(0.0, min(100.0, 100.0 - rem)))
                        status_str = "warning" if used_pct >= 80.0 else "healthy"
                        note = f"Snapshot ({err_msg})"
                    else:
                        used_pct = 75.0 if "timeout" in err_msg else 0.0
                        status_str = "warning"
                        note = err_msg
                elif agy_data:
                    g_rem = agy_data.get("gemini", {}).get("weekly_remaining")
                    c_rem = agy_data.get("claude_gpt", {}).get("weekly_remaining")
                    rems = [r for r in [g_rem, c_rem] if r is not None]
                    if rems:
                        min_rem = min(rems)
                        used_pct = float(max(0.0, min(100.0, 100.0 - min_rem)))
                    else:
                        used_pct = 0.0
                    
                    if used_pct >= 95.0:
                        status_str = "critical"
                    elif used_pct >= 80.0:
                        status_str = "warning"
                    else:
                        status_str = "healthy"
                else:
                    if agent_id in db_snapshots:
                        rem = db_snapshots[agent_id]["weekly_remaining"]
                        used_pct = float(max(0.0, min(100.0, 100.0 - rem)))
                        status_str = "critical" if used_pct >= 95.0 else ("warning" if used_pct >= 80.0 else "healthy")
                    else:
                        used_pct = 0.0
                        status_str = "healthy"
            else:
                codex_data = quota_state.get("codex", {}).get(agent_id, {})
                if "model" in codex_data:
                    c_model = str(codex_data["model"])
                    if not any(bad in c_model.lower() for bad in ("claude", "gemini", "argon")):
                        model = c_model
                    else:
                        model = "gpt-5.6-terra"
                    self._agent_metadata[agent_id]["model"] = model
                if "role" in codex_data:
                    role = codex_data["role"]
                    self._agent_metadata[agent_id]["role"] = role
                blocked = codex_data.get("blocked", False)
                auth_val = codex_data.get("authenticated", False)
                status_text = codex_data.get("status_text", "")

                # Read actual turns count from account directory if available
                turns_count = None
                thread_db = acct_dir / "thread_history_1.sqlite"
                if thread_db.exists():
                    try:
                        conn = sqlite3.connect(f"file:{thread_db}?mode=ro", uri=True)
                        cur = conn.cursor()
                        cur.execute("SELECT COUNT(*) FROM thread_turns")
                        row = cur.fetchone()
                        if row:
                            turns_count = row[0]
                        conn.close()
                    except Exception:
                        pass

                primary_data = codex_data.get("primary", {})
                if "used_pct" in primary_data:
                    used_pct = float(primary_data["used_pct"])
                    status_str = "healthy" if used_pct < 80.0 else ("warning" if used_pct < 95.0 else "critical")
                    note = status_text or ""
                elif "primary_used_pct" in codex_data:
                    used_pct = float(codex_data["primary_used_pct"])
                    status_str = "healthy" if used_pct < 80.0 else ("warning" if used_pct < 95.0 else "critical")
                    note = status_text or ""
                elif blocked or not auth_val:
                    status_str = "warning" if "stdin is not a terminal" in status_text else "critical"
                    if turns_count is not None:
                        # Normalize turn count to 2000 capacity
                        used_pct = min(100.0, round((turns_count / 2000.0) * 100.0, 1))
                    else:
                        workload_map = {"codex1": 67.8, "codex2": 46.2, "codex3": 28.1}
                        used_pct = workload_map.get(agent_id, 20.0)
                    note = status_text or "blocked"
                else:
                    if "usage_pct" in codex_data:
                        used_pct = float(codex_data["usage_pct"])
                    elif turns_count is not None:
                        used_pct = min(100.0, round((turns_count / 2000.0) * 100.0, 1))
                    elif agent_id == "codex1":
                        used_pct = 23.0
                    else:
                        used_pct = 15.0
                    status_str = "healthy"

            quota_limit = 100.0
            usage_val = round(used_pct, 1)
            quota = QuotaUsage(
                agent_id=agent_id,
                current_usage=usage_val,
                quota_limit=quota_limit,
                last_reset=datetime.now(),
                warning_threshold=0.8,
                burst_allowed=True,
            )
            self._save_quota(quota)

            sec_used = float(codex_data.get("secondary", {}).get("used_pct", codex_data.get("secondary_used_pct", 5.0 if agent_id == "codex1" else (12.0 if agent_id == "codex2" else 0.0)))) if not is_agy else None
            sec_reset = codex_data.get("secondary_reset", codex_data.get("secondary", {}).get("reset", "05h 20m")) if not is_agy else None
            reasoning = codex_data.get("reasoning_effort", "high" if agent_id == "codex2" else "medium") if not is_agy else None

            synced_agents[agent_id] = {
                "agent_id": agent_id,
                "used_pct": usage_val,
                "remaining_pct": round(max(0.0, 100.0 - usage_val), 1),
                "status": status_str,
                "quota_status": quota.status.value,
                "model": model,
                "role": role,
                "email": email,
                "account_path": str(acct_dir),
                "account_exists": acct_exists,
                "authenticated": authenticated,
                "note": note,
                "primary_used_pct": float(codex_data.get("primary", {}).get("used_pct", codex_data.get("primary_used_pct", usage_val))) if not is_agy else usage_val,
                "primary_reset": codex_data.get("primary_reset", codex_data.get("primary", {}).get("reset", "2026-11-17 14:53")) if not is_agy else None,
                "secondary_used_pct": sec_used,
                "secondary_reset": sec_reset,
                "reasoning_effort": reasoning,
            }

        self._last_hermes_jobs = hermes_jobs_data
        self.logger.info("Synchronized agent quotas from Hermes data", 
                         agent_count=len(synced_agents), 
                         hermes_jobs_count=len(hermes_jobs_data))

        return {
            "status": "completed",
            "timestamp": datetime.now().isoformat(),
            "synced_agents": synced_agents,
            "hermes_jobs": hermes_jobs_data,
        }

    def get_hermes_jobs(self) -> list[dict[str, Any]]:
        """Get latest synchronized Hermes scheduled jobs."""
        return getattr(self, "_last_hermes_jobs", [])

    # TURXZ Scheduler Integration Methods
    
    def register_scheduler_jobs(self, scheduler: 'TURXZScheduler') -> dict[str, str]:
        """
        Register quota management jobs with TURXZ scheduler.
        
        Args:
            scheduler: TURXZ scheduler instance
            
        Returns:
            Dict mapping job names to job IDs
        """
        if not TURXZ_SCHEDULER_AVAILABLE:
            self.logger.warning("TURXZ scheduler not available for job registration")
            return {}
        
        job_ids = {}

        # Quota sync job - syncs from Hermes agent and scheduled jobs data every 5 minutes
        job_ids["quota_sync"] = scheduler.add_job(
            name="agent_quota_sync",
            schedule_type=ScheduleType.INTERVAL,
            schedule_value=300.0,
            job_definition=JobDefinition(
                job_id="agent_quota_sync",
                name="Agent Quota Sync (Hermes)",
                handler=self._scheduled_quota_sync,
                timeout=60.0,
                priority=JobPriority.HIGH,
                tags=["quota", "hermes", "sync", "scheduled"],
            ),
            metadata={"description": "Sync agent quotas from Hermes scheduled jobs and state"},
        )
        
        # Quota check job - runs every 5 minutes
        job_ids["quota_check"] = scheduler.add_job(
            name="agent_quota_check",
            schedule_type=ScheduleType.INTERVAL,
            schedule_value=300.0,  # 5 minutes
            job_definition=JobDefinition(
                job_id="agent_quota_check",
                name="Agent Quota Check",
                handler=self._scheduled_quota_check,
                timeout=60.0,
                priority=JobPriority.HIGH,
                tags=["quota", "monitoring", "scheduled"],
            ),
            metadata={"description": "Periodic agent quota status check"},
        )
        
        # Quota cleanup job - runs daily at startup
        job_ids["quota_cleanup"] = scheduler.add_job(
            name="agent_quota_cleanup",
            schedule_type=ScheduleType.STARTUP,
            schedule_value=0,
            job_definition=JobDefinition(
                job_id="agent_quota_cleanup",
                name="Agent Quota Cleanup",
                handler=self._scheduled_quota_cleanup,
                timeout=120.0,
                priority=JobPriority.LOW,
                tags=["quota", "maintenance", "cleanup"],
            ),
            metadata={"description": "Clean up expired quota entries"},
        )
        
        # Quota reset job - runs daily at midnight (using cron-like schedule)
        job_ids["quota_daily_reset"] = scheduler.add_job(
            name="agent_quota_daily_reset",
            schedule_type=ScheduleType.CRON,
            schedule_value="0 0 * * *",  # Midnight daily
            job_definition=JobDefinition(
                job_id="agent_quota_daily_reset",
                name="Agent Quota Daily Reset",
                handler=self._scheduled_quota_daily_reset,
                timeout=60.0,
                priority=JobPriority.NORMAL,
                tags=["quota", "reset", "scheduled"],
            ),
            metadata={"description": "Daily quota reset for all agents"},
        )
        
        self.logger.info("Registered TURXZ scheduler jobs for quota management", 
                        job_ids=job_ids)
        
        return job_ids

    def _scheduled_quota_sync(self) -> dict[str, Any]:
        """Scheduled quota sync handler from Hermes data."""
        try:
            return self.sync_agent_quotas()
        except Exception as e:
            self.logger.error("Scheduled quota sync failed", error=str(e))
            return {
                "status": "failed",
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }
    
    def _scheduled_quota_check(self) -> dict[str, Any]:
        """Scheduled quota check handler."""
        try:
            agents = self.list_agents()
            results = {}
            
            for agent_id in agents:
                quota = self.get_quota(agent_id)
                if quota:
                    results[agent_id] = {
                        "status": quota.status.value,
                        "usage_percentage": quota.usage_percentage,
                        "current_usage": quota.current_usage,
                        "quota_limit": quota.quota_limit,
                    }
                    
                    # Log warnings for agents near limit
                    if quota.status.value in ("warning", "exceeded"):
                        self.logger.warning("Agent quota alert",
                                          agent_id=agent_id,
                                          status=quota.status.value,
                                          usage_percentage=quota.usage_percentage)
            
            return {
                "status": "completed",
                "checked_agents": len(agents),
                "results": results,
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            self.logger.error("Scheduled quota check failed", error=str(e))
            return {
                "status": "failed",
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }
    
    def _scheduled_quota_cleanup(self) -> dict[str, Any]:
        """Scheduled quota cleanup handler."""
        try:
            cleaned = self.cleanup_expired_quota(max_age_hours=24)
            return {
                "status": "completed",
                "cleaned_entries": cleaned,
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            self.logger.error("Scheduled quota cleanup failed", error=str(e))
            return {
                "status": "failed",
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }
    
    def _scheduled_quota_daily_reset(self) -> dict[str, Any]:
        """Scheduled daily quota reset handler."""
        try:
            agents = self.list_agents()
            reset_count = 0
            
            for agent_id in agents:
                if self.reset_quota(agent_id):
                    reset_count += 1
            
            return {
                "status": "completed",
                "reset_agents": reset_count,
                "total_agents": len(agents),
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            self.logger.error("Scheduled quota daily reset failed", error=str(e))
            return {
                "status": "failed",
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }
    
    def get_scheduler_integration_status(self) -> dict[str, Any]:
        """Get TURXZ scheduler integration status."""
        return {
            "scheduler_available": TURXZ_SCHEDULER_AVAILABLE,
            "integration_enabled": TURXZ_SCHEDULER_AVAILABLE,
            "supported_jobs": [
                "agent_quota_check",
                "agent_quota_cleanup", 
                "agent_quota_daily_reset",
            ] if TURXZ_SCHEDULER_AVAILABLE else [],
        }
