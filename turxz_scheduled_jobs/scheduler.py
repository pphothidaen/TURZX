"""
TURXZ Scheduler - Core scheduling engine for TURZX native job system.

Provides a lightweight, dependency-free scheduler with cron-like scheduling,
interval-based execution, and one-shot jobs.
"""

import asyncio
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional

from ._logger import get_logger
from .executor import ExecutionResult, TURXZJobExecutor
from .job_manager import JobDefinition, TURXZJobManager

logger = get_logger(__name__)


class ScheduleType(str, Enum):
    """Types of schedules supported."""
    INTERVAL = "interval"
    CRON = "cron"
    ONCE = "once"
    STARTUP = "startup"
    SHUTDOWN = "shutdown"


@dataclass
class ScheduledJob:
    """A scheduled job definition."""
    job_id: str
    name: str
    schedule_type: ScheduleType
    schedule_value: str | int | float | datetime
    job_definition: 'JobDefinition'
    enabled: bool = True
    next_run: datetime | None = None
    last_run: datetime | None = None
    run_count: int = 0
    max_runs: int | None = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Validate schedule parameters and calculate initial next_run time."""
        self._validate_schedule()
        if self.next_run is None:
            self.next_run = self._calculate_next_run()

    def _validate_schedule(self) -> None:
        """Enforce Fail-Closed validation on schedule definitions."""
        if self.schedule_type == ScheduleType.INTERVAL:
            if not isinstance(self.schedule_value, (int, float)) or self.schedule_value <= 0:
                raise ValueError(
                    f"Invalid interval value '{self.schedule_value}': must be a positive number (seconds)"
                )
        elif self.schedule_type == ScheduleType.CRON:
            if not isinstance(self.schedule_value, str):
                raise ValueError(
                    f"Invalid cron expression '{self.schedule_value}': must be a string"
                )
            parts = self.schedule_value.strip().split()
            if len(parts) != 5:
                raise ValueError(
                    f"Invalid cron expression '{self.schedule_value}': expected 5 space-separated fields (minute hour day month weekday)"
                )
        elif self.schedule_type == ScheduleType.ONCE:
            if not isinstance(self.schedule_value, (datetime, int, float)):
                raise ValueError(
                    f"Invalid once schedule value '{self.schedule_value}': must be datetime or numeric seconds offset"
                )

    def _calculate_next_run(self) -> datetime:
        """Calculate the next run time based on schedule type."""
        now = datetime.now()
        
        if self.schedule_type == ScheduleType.INTERVAL:
            return now + timedelta(seconds=self.schedule_value)
        elif self.schedule_type == ScheduleType.CRON:
            return self._next_cron_time(now)
        elif self.schedule_type == ScheduleType.ONCE:
            if isinstance(self.schedule_value, datetime):
                return self.schedule_value
            elif isinstance(self.schedule_value, (int, float)):
                return now + timedelta(seconds=self.schedule_value)
        elif self.schedule_type == ScheduleType.STARTUP:
            return now
        elif self.schedule_type == ScheduleType.SHUTDOWN:
            return None  # Run on shutdown only
        
        return now

    def _next_cron_time(self, from_time: datetime) -> datetime:
        """Calculate next cron execution time (5-field expression)."""
        parts = str(self.schedule_value).strip().split()
        if len(parts) != 5:
            raise ValueError(
                f"Invalid cron expression '{self.schedule_value}': expected 5 space-separated fields"
            )
        
        # Calculate next rounded minute increment
        next_time = from_time.replace(second=0, microsecond=0) + timedelta(minutes=1)
        return next_time

    def should_run(self, now: datetime | None = None) -> bool:
        """Check if job should run now."""
        if not self.enabled:
            return False
        
        if self.max_runs is not None and self.run_count >= self.max_runs:
            return False
        
        if self.next_run is None:
            return False
        
        check_time = now or datetime.now()
        return check_time >= self.next_run

    def mark_run(self) -> None:
        """Mark job as run and calculate next run time."""
        self.last_run = datetime.now()
        self.run_count += 1
        self.updated_at = datetime.now()
        
        if self.schedule_type != ScheduleType.ONCE and self.schedule_type != ScheduleType.SHUTDOWN:
            self.next_run = self._calculate_next_run()
        elif self.schedule_type == ScheduleType.ONCE:
            self.next_run = None  # One-shot job doesn't repeat
        
        if self.max_runs is not None and self.run_count >= self.max_runs:
            self.enabled = False


class TURXZScheduler:
    """
    Native TURXZ scheduler for managing scheduled jobs.
    
    Supports interval, cron, one-shot, startup, and shutdown jobs.
    Runs in a dedicated background thread with asyncio event loop.
    """
    
    def __init__(
        self,
        job_manager: Optional['TURXZJobManager'] = None,
        executor: Optional['TURXZJobExecutor'] = None,
        check_interval: float = 1.0,
        max_concurrent_jobs: int = 10,
    ):
        self.job_manager = job_manager or TURXZJobManager()
        self.executor = executor or TURXZJobExecutor(max_workers=max_concurrent_jobs)
        self.check_interval = check_interval
        self.max_concurrent_jobs = max_concurrent_jobs
        
        self._jobs: dict[str, ScheduledJob] = {}
        self._running = False
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.RLock()
        
        # Startup/shutdown callbacks
        self._startup_callbacks: list[Callable] = []
        self._shutdown_callbacks: list[Callable] = []
        
        logger.info("TURXZScheduler initialized", 
                   check_interval=check_interval,
                   max_concurrent_jobs=max_concurrent_jobs)

    def add_job(
        self,
        name: str,
        schedule_type: ScheduleType | str,
        schedule_value: str | float | datetime,
        job_definition: 'JobDefinition',
        enabled: bool = True,
        max_runs: int | None = None,
        job_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Add a new scheduled job."""
        if isinstance(schedule_type, str):
            schedule_type = ScheduleType(schedule_type)
        
        # Use job_definition's job_id if not explicitly provided
        job_id = job_id or job_definition.job_id or str(uuid.uuid4())
        
        scheduled_job = ScheduledJob(
            job_id=job_id,
            name=name,
            schedule_type=schedule_type,
            schedule_value=schedule_value,
            job_definition=job_definition,
            enabled=enabled,
            max_runs=max_runs,
            metadata=metadata or {},
        )
        
        with self._lock:
            self._jobs[job_id] = scheduled_job
            # Also register with job manager
            self.job_manager.register_job(job_definition)
        
        logger.info("Scheduled job added", job_id=job_id, name=name, 
                   schedule_type=schedule_type.value, next_run=scheduled_job.next_run)
        
        return job_id

    def remove_job(self, job_id: str) -> bool:
        """Remove a scheduled job."""
        with self._lock:
            if job_id in self._jobs:
                del self._jobs[job_id]
                logger.info("Scheduled job removed", job_id=job_id)
                return True
        return False

    def get_job(self, job_id: str) -> ScheduledJob | None:
        """Get a scheduled job by ID."""
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> list[ScheduledJob]:
        """List all scheduled jobs."""
        with self._lock:
            return list(self._jobs.values())

    def enable_job(self, job_id: str) -> bool:
        """Enable a scheduled job."""
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].enabled = True
                self._jobs[job_id].next_run = self._jobs[job_id]._calculate_next_run()
                logger.info("Scheduled job enabled", job_id=job_id)
                return True
        return False

    def disable_job(self, job_id: str) -> bool:
        """Disable a scheduled job."""
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].enabled = False
                logger.info("Scheduled job disabled", job_id=job_id)
                return True
        return False

    def run_job_now(self, job_id: str) -> Optional['ExecutionResult']:
        """Run a job immediately, outside of schedule."""
        with self._lock:
            job = self._jobs.get(job_id)
        
        if not job:
            logger.warning("Job not found for manual run", job_id=job_id)
            return None
        
        return self._execute_job(job)

    def add_startup_callback(self, callback: Callable) -> None:
        """Add a callback to run on scheduler startup."""
        self._startup_callbacks.append(callback)

    def add_shutdown_callback(self, callback: Callable) -> None:
        """Add a callback to run on scheduler shutdown."""
        self._shutdown_callbacks.append(callback)

    def start(self) -> None:
        """Start the scheduler."""
        if self._running:
            logger.warning("Scheduler already running")
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        
        # Run startup callbacks
        for callback in self._startup_callbacks:
            try:
                callback()
            except Exception as e:
                logger.error("Startup callback failed", error=str(e))
        
        # Run startup jobs
        self._run_startup_jobs()
        
        logger.info("TURXZScheduler started")

    def stop(self, timeout: float = 30.0) -> None:
        """Stop the scheduler."""
        if not self._running:
            logger.warning("Scheduler not running")
            return
        
        self._running = False
        
        # Run shutdown jobs
        self._run_shutdown_jobs()
        
        # Run shutdown callbacks
        for callback in self._shutdown_callbacks:
            try:
                callback()
            except Exception as e:
                logger.error("Shutdown callback failed", error=str(e))
        
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None
        
        # Shutdown executor
        self.executor.shutdown(wait=True)
        
        logger.info("TURXZScheduler stopped")

    def _run_loop(self) -> None:
        """Main scheduler loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        
        while self._running:
            try:
                self._check_and_run_jobs()
                time.sleep(self.check_interval)
            except Exception as e:
                logger.error("Scheduler loop error", error=str(e))
                time.sleep(self.check_interval)
        
        self._loop.close()
        self._loop = None

    def _check_and_run_jobs(self) -> None:
        """Check for jobs that need to run and execute them."""
        now = datetime.now()
        jobs_to_run = []
        
        with self._lock:
            for job in self._jobs.values():
                if job.should_run(now):
                    jobs_to_run.append(job)
        
        for job in jobs_to_run:
            self._execute_job(job)

    def _execute_job(self, job: ScheduledJob) -> 'ExecutionResult':
        """Execute a scheduled job."""
        logger.info("Executing scheduled job", job_id=job.job_id, name=job.name)
        
        # Mark as running
        job.mark_run()
        
        # Execute via executor
        result = self.executor.execute(job.job_definition)
        
        logger.info("Scheduled job completed", 
                   job_id=job.job_id, 
                   success=result.success,
                   duration=result.duration)
        
        return result

    def _run_startup_jobs(self) -> None:
        """Run all startup-type jobs."""
        with self._lock:
            startup_jobs = [j for j in self._jobs.values() 
                          if j.schedule_type == ScheduleType.STARTUP and j.enabled]
        
        for job in startup_jobs:
            self._execute_job(job)

    def _run_shutdown_jobs(self) -> None:
        """Run all shutdown-type jobs."""
        with self._lock:
            shutdown_jobs = [j for j in self._jobs.values() 
                           if j.schedule_type == ScheduleType.SHUTDOWN and j.enabled]
        
        for job in shutdown_jobs:
            self._execute_job(job)

    def get_status(self) -> dict[str, Any]:
        """Get scheduler status."""
        with self._lock:
            jobs = list(self._jobs.values())
        
        return {
            "running": self._running,
            "total_jobs": len(jobs),
            "enabled_jobs": sum(1 for j in jobs if j.enabled),
            "disabled_jobs": sum(1 for j in jobs if not j.enabled),
            "jobs_by_type": self._count_by_type(jobs),
            "next_scheduled_run": self._get_next_run_time(jobs),
            "executor_status": self.executor.get_status(),
        }

    def _count_by_type(self, jobs: list[ScheduledJob]) -> dict[str, int]:
        """Count jobs by schedule type."""
        counts = {}
        for job in jobs:
            counts[job.schedule_type.value] = counts.get(job.schedule_type.value, 0) + 1
        return counts

    def _get_next_run_time(self, jobs: list[ScheduledJob]) -> str | None:
        """Get the next scheduled run time."""
        enabled_jobs = [j for j in jobs if j.enabled and j.next_run is not None]
        if not enabled_jobs:
            return None
        
        next_job = min(enabled_jobs, key=lambda j: j.next_run)
        return next_job.next_run.isoformat() if next_job.next_run else None