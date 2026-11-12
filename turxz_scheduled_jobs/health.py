"""
TURXZ Scheduler Health Checks - Health monitoring for the scheduler system.
"""

import asyncio
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from ._logger import get_logger
from .config import HealthCheckSettings
from .executor import TURXZJobExecutor
from .scheduler import TURXZScheduler

logger = get_logger(__name__)


class HealthStatus(str, Enum):
    """Health check status."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class HealthCheckResult:
    """Result of a health check."""
    name: str
    status: HealthStatus
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    duration: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
            "duration": self.duration,
        }


class HealthCheck:
    """Base class for health checks."""
    
    def __init__(self, name: str, timeout: float = 10.0):
        self.name = name
        self.timeout = timeout
    
    def check(self) -> HealthCheckResult:
        """Perform the health check."""
        raise NotImplementedError
    
    async def check_async(self) -> HealthCheckResult:
        """Perform the health check asynchronously."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.check)


class SchedulerRunningCheck(HealthCheck):
    """Check if scheduler is running."""
    
    def __init__(self, scheduler: TURXZScheduler, timeout: float = 5.0):
        super().__init__("scheduler_running", timeout)
        self.scheduler = scheduler
    
    def check(self) -> HealthCheckResult:
        started = time.time()
        try:
            status = self.scheduler.get_status()
            is_running = status.get("running", False)
            
            if is_running:
                return HealthCheckResult(
                    name=self.name,
                    status=HealthStatus.HEALTHY,
                    message="Scheduler is running",
                    details={"status": status},
                    duration=time.time() - started,
                )
            else:
                return HealthCheckResult(
                    name=self.name,
                    status=HealthStatus.UNHEALTHY,
                    message="Scheduler is not running",
                    details={"status": status},
                    duration=time.time() - started,
                )
        except Exception as e:
            return HealthCheckResult(
                name=self.name,
                status=HealthStatus.UNHEALTHY,
                message=f"Failed to check scheduler: {e!s}",
                details={"error": str(e)},
                duration=time.time() - started,
            )


class ExecutorHealthyCheck(HealthCheck):
    """Check if executor is healthy."""
    
    def __init__(self, executor: TURXZJobExecutor, timeout: float = 5.0):
        super().__init__("executor_healthy", timeout)
        self.executor = executor
    
    def check(self) -> HealthCheckResult:
        started = time.time()
        try:
            status = self.executor.get_status()
            running = status.get("running", False)
            success_rate = status.get("success_rate", 0.0)
            
            if not running:
                return HealthCheckResult(
                    name=self.name,
                    status=HealthStatus.UNHEALTHY,
                    message="Executor is not running",
                    details={"status": status},
                    duration=time.time() - started,
                )
            
            if success_rate < 0.5 and status.get("total_executions", 0) > 10:
                return HealthCheckResult(
                    name=self.name,
                    status=HealthStatus.DEGRADED,
                    message=f"Low success rate: {success_rate:.1%}",
                    details={"status": status},
                    duration=time.time() - started,
                )
            
            return HealthCheckResult(
                name=self.name,
                status=HealthStatus.HEALTHY,
                message="Executor is healthy",
                details={"status": status},
                duration=time.time() - started,
            )
        except Exception as e:
            return HealthCheckResult(
                name=self.name,
                status=HealthStatus.UNHEALTHY,
                message=f"Failed to check executor: {e!s}",
                details={"error": str(e)},
                duration=time.time() - started,
            )


class JobQueueHealthyCheck(HealthCheck):
    """Check if job queue is healthy."""
    
    def __init__(self, scheduler: TURXZScheduler, timeout: float = 5.0):
        super().__init__("job_queue_healthy", timeout)
        self.scheduler = scheduler
    
    def check(self) -> HealthCheckResult:
        started = time.time()
        try:
            status = self.scheduler.get_status()
            total_jobs = status.get("total_jobs", 0)
            enabled_jobs = status.get("enabled_jobs", 0)
            
            # Check for stuck jobs (jobs that should have run but didn't)
            now = datetime.now()
            stuck_jobs = 0
            
            with self.scheduler._lock:
                for job in self.scheduler._jobs.values():
                    if job.enabled and job.next_run and job.next_run < now:
                        stuck_jobs += 1
            
            details = {
                "total_jobs": total_jobs,
                "enabled_jobs": enabled_jobs,
                "stuck_jobs": stuck_jobs,
            }
            
            if stuck_jobs > 0:
                return HealthCheckResult(
                    name=self.name,
                    status=HealthStatus.DEGRADED,
                    message=f"{stuck_jobs} jobs appear stuck",
                    details=details,
                    duration=time.time() - started,
                )
            
            return HealthCheckResult(
                name=self.name,
                status=HealthStatus.HEALTHY,
                message="Job queue is healthy",
                details=details,
                duration=time.time() - started,
            )
        except Exception as e:
            return HealthCheckResult(
                name=self.name,
                status=HealthStatus.UNHEALTHY,
                message=f"Failed to check job queue: {e!s}",
                details={"error": str(e)},
                duration=time.time() - started,
            )


class DiskSpaceCheck(HealthCheck):
    """Check available disk space."""
    
    def __init__(self, path: str = "/", threshold_percent: float = 10.0, timeout: float = 5.0):
        super().__init__("disk_space", timeout)
        self.path = path
        self.threshold_percent = threshold_percent
    
    def check(self) -> HealthCheckResult:
        started = time.time()
        try:
            usage = shutil.disk_usage(self.path)
            free_percent = (usage.free / usage.total) * 100
            
            details = {
                "path": self.path,
                "total_gb": round(usage.total / (1024**3), 2),
                "free_gb": round(usage.free / (1024**3), 2),
                "used_gb": round(usage.used / (1024**3), 2),
                "free_percent": round(free_percent, 2),
                "threshold_percent": self.threshold_percent,
            }
            
            if free_percent < self.threshold_percent:
                return HealthCheckResult(
                    name=self.name,
                    status=HealthStatus.UNHEALTHY,
                    message=f"Low disk space: {free_percent:.1f}% free",
                    details=details,
                    duration=time.time() - started,
                )
            elif free_percent < self.threshold_percent * 2:
                return HealthCheckResult(
                    name=self.name,
                    status=HealthStatus.DEGRADED,
                    message=f"Disk space getting low: {free_percent:.1f}% free",
                    details=details,
                    duration=time.time() - started,
                )
            
            return HealthCheckResult(
                name=self.name,
                status=HealthStatus.HEALTHY,
                message=f"Disk space OK: {free_percent:.1f}% free",
                details=details,
                duration=time.time() - started,
            )
        except Exception as e:
            return HealthCheckResult(
                name=self.name,
                status=HealthStatus.UNHEALTHY,
                message=f"Failed to check disk space: {e!s}",
                details={"error": str(e)},
                duration=time.time() - started,
            )


class MemoryUsageCheck(HealthCheck):
    """Check memory usage."""
    
    def __init__(self, threshold_percent: float = 90.0, timeout: float = 5.0):
        super().__init__("memory_usage", timeout)
        self.threshold_percent = threshold_percent
    
    def check(self) -> HealthCheckResult:
        started = time.time()
        try:
            # Try to get memory info
            try:
                import psutil
                mem = psutil.virtual_memory()
                used_percent = mem.percent
                details = {
                    "total_mb": round(mem.total / (1024**2), 2),
                    "available_mb": round(mem.available / (1024**2), 2),
                    "used_mb": round(mem.used / (1024**2), 2),
                    "used_percent": round(used_percent, 2),
                    "threshold_percent": self.threshold_percent,
                }
            except ImportError:
                # Fallback: read from /proc/meminfo on Linux
                used_percent = 0
                details = {"note": "psutil not available, using fallback"}
                try:
                    with open('/proc/meminfo', 'r') as f:
                        meminfo = {}
                        for line in f:
                            if ':' in line:
                                key, val = line.split(':', 1)
                                meminfo[key.strip()] = val.strip()
                        
                        total = int(meminfo.get('MemTotal', '0').split()[0]) * 1024
                        available = int(meminfo.get('MemAvailable', '0').split()[0]) * 1024
                        used = total - available
                        used_percent = (used / total) * 100 if total > 0 else 0
                        
                        details = {
                            "total_mb": round(total / (1024**2), 2),
                            "available_mb": round(available / (1024**2), 2),
                            "used_mb": round(used / (1024**2), 2),
                            "used_percent": round(used_percent, 2),
                            "threshold_percent": self.threshold_percent,
                        }
                except Exception:
                    pass
            
            if used_percent > self.threshold_percent:
                return HealthCheckResult(
                    name=self.name,
                    status=HealthStatus.UNHEALTHY,
                    message=f"High memory usage: {used_percent:.1f}%",
                    details=details,
                    duration=time.time() - started,
                )
            elif used_percent > self.threshold_percent * 0.8:
                return HealthCheckResult(
                    name=self.name,
                    status=HealthStatus.DEGRADED,
                    message=f"Memory usage elevated: {used_percent:.1f}%",
                    details=details,
                    duration=time.time() - started,
                )
            
            return HealthCheckResult(
                name=self.name,
                status=HealthStatus.HEALTHY,
                message=f"Memory usage OK: {used_percent:.1f}%",
                details=details,
                duration=time.time() - started,
            )
        except Exception as e:
            return HealthCheckResult(
                name=self.name,
                status=HealthStatus.UNHEALTHY,
                message=f"Failed to check memory: {e!s}",
                details={"error": str(e)},
                duration=time.time() - started,
            )


class TURXZSchedulerHealthCheck:
    """
    Comprehensive health check system for TURXZ Scheduler.
    
    Runs multiple health checks and provides overall health status.
    """
    
    def __init__(
        self,
        scheduler: TURXZScheduler,
        executor: TURXZJobExecutor,
        settings: HealthCheckSettings | None = None,
    ):
        self.scheduler = scheduler
        self.executor = executor
        self.settings = settings or HealthCheckSettings()
        
        self._checks: dict[str, HealthCheck] = {}
        self._last_results: dict[str, HealthCheckResult] = {}
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._failure_counts: dict[str, int] = {}
        self._callbacks: list[Callable[[dict[str, HealthCheckResult]], None]] = []
        
        # Register default checks
        self._register_default_checks()
        
        logger.info("TURXZSchedulerHealthCheck initialized", 
                   enabled=self.settings.enabled,
                   interval=self.settings.interval)

    def _register_default_checks(self) -> None:
        """Register default health checks."""
        self.register_check(SchedulerRunningCheck(self.scheduler))
        self.register_check(ExecutorHealthyCheck(self.executor))
        self.register_check(JobQueueHealthyCheck(self.scheduler))
        self.register_check(DiskSpaceCheck())
        self.register_check(MemoryUsageCheck())

    def register_check(self, check: HealthCheck) -> None:
        """Register a health check."""
        with self._lock:
            self._checks[check.name] = check
            self._failure_counts[check.name] = 0
        logger.debug("Health check registered", name=check.name)

    def unregister_check(self, name: str) -> bool:
        """Unregister a health check."""
        with self._lock:
            if name in self._checks:
                del self._checks[name]
                del self._failure_counts[name]
                if name in self._last_results:
                    del self._last_results[name]
                return True
        return False

    def add_callback(self, callback: Callable[[dict[str, HealthCheckResult]], None]) -> None:
        """Add a callback for health check results."""
        self._callbacks.append(callback)

    def run_checks(self) -> dict[str, HealthCheckResult]:
        """Run all health checks synchronously."""
        results = {}
        
        for name, check in self._checks.items():
            try:
                result = check.check()
                results[name] = result
                self._last_results[name] = result
                
                # Track failures
                if result.status in (HealthStatus.UNHEALTHY, HealthStatus.DEGRADED):
                    self._failure_counts[name] = self._failure_counts.get(name, 0) + 1
                else:
                    self._failure_counts[name] = 0
                    
            except Exception as e:
                result = HealthCheckResult(
                    name=name,
                    status=HealthStatus.UNHEALTHY,
                    message=f"Check failed: {e!s}",
                    details={"error": str(e)},
                )
                results[name] = result
                self._last_results[name] = result
                self._failure_counts[name] = self._failure_counts.get(name, 0) + 1
        
        # Trigger callbacks
        for callback in self._callbacks:
            try:
                callback(results)
            except Exception as e:
                logger.error("Health check callback failed", error=str(e))
        
        return results

    async def run_checks_async(self) -> dict[str, HealthCheckResult]:
        """Run all health checks asynchronously."""
        tasks = []
        for check in self._checks.values():
            tasks.append(check.check_async())
        
        results_list = await asyncio.gather(*tasks, return_exceptions=True)
        
        results: dict[str, HealthCheckResult] = {}
        for check, result in zip(self._checks.values(), results_list):
            if isinstance(result, Exception):
                result = HealthCheckResult(
                    name=check.name,
                    status=HealthStatus.UNHEALTHY,
                    message=f"Check failed: {result!s}",
                    details={"error": str(result)},
                )
            elif isinstance(result, HealthCheckResult):
                pass  # Already correct type
            else:
                result = HealthCheckResult(
                    name=check.name,
                    status=HealthStatus.UNHEALTHY,
                    message=f"Check returned unexpected type: {type(result)}",
                    details={"error": "unexpected result type"},
                )
            results[check.name] = result
            self._last_results[check.name] = result
            
            if result.status in (HealthStatus.UNHEALTHY, HealthStatus.DEGRADED):
                self._failure_counts[check.name] = self._failure_counts.get(check.name, 0) + 1
            else:
                self._failure_counts[check.name] = 0
        
        for callback in self._callbacks:
            try:
                callback(results)
            except Exception as e:
                logger.error("Health check callback failed", error=str(e))
        
        return results

    def get_overall_status(self) -> HealthStatus:
        """Get overall health status."""
        if not self._last_results:
            return HealthStatus.UNKNOWN
        
        statuses = [r.status for r in self._last_results.values()]
        
        if HealthStatus.UNHEALTHY in statuses:
            return HealthStatus.UNHEALTHY
        elif HealthStatus.DEGRADED in statuses:
            return HealthStatus.DEGRADED
        elif HealthStatus.HEALTHY in statuses:
            return HealthStatus.HEALTHY
        return HealthStatus.UNKNOWN

    def get_last_results(self) -> dict[str, HealthCheckResult]:
        """Get last health check results."""
        with self._lock:
            return dict(self._last_results)

    def get_failure_counts(self) -> dict[str, int]:
        """Get failure counts for each check."""
        with self._lock:
            return dict(self._failure_counts)

    def start(self) -> None:
        """Start periodic health checks."""
        if self._running or not self.settings.enabled:
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Health checks started", interval=self.settings.interval)

    def stop(self) -> None:
        """Stop periodic health checks."""
        if not self._running:
            return
        
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("Health checks stopped")

    def _run_loop(self) -> None:
        """Background health check loop."""
        while self._running:
            try:
                self.run_checks()
            except Exception as e:
                logger.error("Health check loop error", error=str(e))
            
            time.sleep(self.settings.interval)

    def get_status(self) -> dict[str, Any]:
        """Get health check system status."""
        with self._lock:
            return {
                "running": self._running,
                "enabled": self.settings.enabled,
                "interval": self.settings.interval,
                "overall_status": self.get_overall_status().value,
                "checks": {
                    name: result.to_dict() 
                    for name, result in self._last_results.items()
                },
                "failure_counts": dict(self._failure_counts),
                "registered_checks": list(self._checks.keys()),
            }