"""
Agent quota management service for DeepSeek Web Bridge.
"""

import redis
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import structlog
from dataclasses import dataclass
from enum import Enum

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
        try:
            self.redis = redis.from_url(redis_url)
            self._setup_redis()
        except Exception as e:
            self.logger.warning("Redis connection failed, operating in degraded mode", 
                               error=str(e))
            # In degraded mode, we'll simulate Redis operations in memory
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

    def get_quota(self, agent_id: str) -> Optional[QuotaUsage]:
        """Get quota for an agent."""
        try:
            data = self.redis.get(f"quota:{agent_id}")
            if data:
                return self._deserialize_quota(data)
        except Exception as e:
            self.logger.error("Failed to get quota", agent_id=agent_id, error=str(e))
        
        return None

    def use_quota(self, agent_id: str, units: float = 1.0, 
                  allow_burst: bool = True) -> Dict[str, Any]:
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

    def get_quota_status(self, agent_id: str) -> Dict[str, Any]:
        """Get quota status for an agent."""
        quota = self.get_quota(agent_id)
        if not quota:
            return {
                "agent_id": agent_id,
                "status": QuotaStatus.DISABLED.value,
                "current_usage": 0.0,
                "quota_limit": 0.0,
                "usage_percentage": 0.0,
            }

        return {
            "agent_id": quota.agent_id,
            "status": quota.status.value,
            "current_usage": quota.current_usage,
            "quota_limit": quota.quota_limit,
            "usage_percentage": quota.usage_percentage,
            "warning_threshold": quota.warning_threshold,
            "burst_allowed": quota.burst_allowed,
            "last_reset": quota.last_reset.isoformat(),
        }

    def list_agents(self) -> List[str]:
        """List all agents with quota."""
        try:
            keys = self.redis.keys("quota:*")
            return [key.decode('utf-8').replace('quota:', '') for key in keys]
        except Exception as e:
            self.logger.error("Failed to list agents", error=str(e))
            return []

    def cleanup_expired_quota(self, max_age_hours: int = 24) -> int:
        """Clean up expired quota entries."""
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

    def health_check(self) -> Dict[str, Any]:
        """Health check for the quota manager."""
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
            self.logger.error(f"Health check failed: {str(e)}")
            return {
                "status": "unhealthy",
                "timestamp": datetime.now().isoformat(),
                "redis_connected": False,
                "error": str(e),
            }

    def get_quota_analytics(self, agent_id: str, hours: int = 24) -> Dict[str, Any]:
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
                                    allow_burst: bool = True) -> Dict[str, Any]:
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

    def _get_in_memory_quota(self, agent_id: str) -> Optional[QuotaUsage]:
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

    def _list_in_memory_agents(self) -> List[str]:
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
