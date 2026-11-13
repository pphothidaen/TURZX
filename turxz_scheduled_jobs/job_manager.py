"""
TURXZ Job Manager - Manages job definitions, registration, and lifecycle.
"""

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Union

from ._logger import get_logger

logger = get_logger(__name__)


class JobStatus(str, Enum):
    """Job execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class JobPriority(str, Enum):
    """Job priority levels."""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class TaskPowerCategory(str, Enum):
    """Categorization of jobs by power consumption."""
    POWER_CRITICAL = "power_critical"
    MODERATE = "moderate"
    LOW = "low"


class TaskUrgency(str, Enum):
    """Task urgency levels for resource allocation."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class JobDefinition:
    """Definition of a job to be executed."""
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    handler: Callable = field(default=lambda: None)
    args: tuple = field(default_factory=tuple)
    kwargs: dict[str, Any] = field(default_factory=dict)
    timeout: float = 300.0  # 5 minutes default
    priority: JobPriority = JobPriority.NORMAL
    power_category: Union[TaskPowerCategory, str] = TaskPowerCategory.MODERATE
    urgency: Union[TaskUrgency, str] = TaskUrgency.MEDIUM
    retries: int = 0
    retry_delay: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    tags: list[str] = field(default_factory=list)


@dataclass
class ExecutionResult:
    """Result of a job execution."""
    job_id: str
    success: bool
    result: Any = None
    error: str | None = None
    duration: float = 0.0
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    retries_attempted: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "success": self.success,
            "result": self.result,
            "error": self.error,
            "duration": self.duration,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "retries_attempted": self.retries_attempted,
            "metadata": self.metadata,
        }


class TURXZJobManager:
    """
    Manages job definitions, registration, and tracking.
    
    Provides a registry for job definitions and tracks execution history.
    """
    
    def __init__(self, max_history: int = 1000):
        self._jobs: dict[str, JobDefinition] = {}
        self._execution_history: list[ExecutionResult] = []
        self._max_history = max_history
        self._lock = threading.RLock()
        self._job_callbacks: dict[str, list[Callable]] = {}
        
        logger.info("TURXZJobManager initialized", max_history=max_history)

    def register_job(self, job: JobDefinition) -> str:
        """Register a job definition."""
        with self._lock:
            self._jobs[job.job_id] = job
            logger.info("Job registered", job_id=job.job_id, name=job.name)
        return job.job_id

    def unregister_job(self, job_id: str) -> bool:
        """Unregister a job definition."""
        with self._lock:
            if job_id in self._jobs:
                del self._jobs[job_id]
                logger.info("Job unregistered", job_id=job_id)
                return True
        return False

    def get_job(self, job_id: str) -> JobDefinition | None:
        """Get a job definition by ID."""
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, tags: list[str] | None = None) -> list[JobDefinition]:
        """List all registered jobs, optionally filtered by tags."""
        with self._lock:
            jobs = list(self._jobs.values())
        
        if tags:
            jobs = [j for j in jobs if any(tag in j.tags for tag in tags)]
        
        return jobs

    def get_jobs_by_priority(self, priority: JobPriority) -> list[JobDefinition]:
        """Get jobs by priority."""
        with self._lock:
            return [j for j in self._jobs.values() if j.priority == priority]

    def add_execution_callback(self, job_id: str, callback: Callable) -> None:
        """Add a callback for job execution events."""
        with self._lock:
            if job_id not in self._job_callbacks:
                self._job_callbacks[job_id] = []
            self._job_callbacks[job_id].append(callback)

    def record_execution(self, result: ExecutionResult) -> None:
        """Record an execution result."""
        with self._lock:
            self._execution_history.append(result)
            # Trim history if needed
            if len(self._execution_history) > self._max_history:
                self._execution_history = self._execution_history[-self._max_history:]
            
            # Trigger callbacks
            callbacks = self._job_callbacks.get(result.job_id, [])
            for callback in callbacks:
                try:
                    callback(result)
                except Exception as e:
                    logger.error("Execution callback failed", job_id=result.job_id, error=str(e))

    def get_execution_history(
        self, 
        job_id: str | None = None,
        limit: int = 100,
        success_only: bool = False,
    ) -> list[ExecutionResult]:
        """Get execution history, optionally filtered."""
        with self._lock:
            history = list(self._execution_history)
        
        if job_id:
            history = [r for r in history if r.job_id == job_id]
        
        if success_only:
            history = [r for r in history if r.success]
        
        return history[-limit:]

    def get_job_stats(self, job_id: str) -> dict[str, Any]:
        """Get statistics for a job."""
        with self._lock:
            history = [r for r in self._execution_history if r.job_id == job_id]
        
        if not history:
            return {
                "job_id": job_id,
                "total_executions": 0,
                "successful": 0,
                "failed": 0,
                "success_rate": 0.0,
                "avg_duration": 0.0,
                "last_execution": None,
            }
        
        successful = sum(1 for r in history if r.success)
        failed = len(history) - successful
        avg_duration = sum(r.duration for r in history) / len(history)
        
        return {
            "job_id": job_id,
            "total_executions": len(history),
            "successful": successful,
            "failed": failed,
            "success_rate": successful / len(history) if history else 0.0,
            "avg_duration": avg_duration,
            "last_execution": history[-1].completed_at.isoformat() if history[-1].completed_at else None,
        }

    def clear_history(self, job_id: str | None = None) -> int:
        """Clear execution history."""
        with self._lock:
            if job_id:
                before = len(self._execution_history)
                self._execution_history = [r for r in self._execution_history if r.job_id != job_id]
                return before - len(self._execution_history)
            else:
                count = len(self._execution_history)
                self._execution_history.clear()
                return count

    def get_status(self) -> dict[str, Any]:
        """Get job manager status."""
        with self._lock:
            return {
                "registered_jobs": len(self._jobs),
                "execution_history_size": len(self._execution_history),
                "jobs_by_priority": self._count_by_priority(),
                "jobs_by_tag": self._count_by_tag(),
            }

    def _count_by_priority(self) -> dict[str, int]:
        """Count jobs by priority."""
        counts = {}
        for job in self._jobs.values():
            counts[job.priority.value] = counts.get(job.priority.value, 0) + 1
        return counts

    def _count_by_tag(self) -> dict[str, int]:
        """Count jobs by tag."""
        counts = {}
        for job in self._jobs.values():
            for tag in job.tags:
                counts[tag] = counts.get(tag, 0) + 1
        return counts