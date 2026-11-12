"""
TURXZ Job Executor - Executes jobs with concurrency control and timeout handling.
"""

import asyncio
import concurrent.futures
import inspect
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from ._logger import get_logger
from .job_manager import ExecutionResult, JobDefinition

logger = get_logger(__name__)


class ExecutionMode(str, Enum):
    """Execution mode for jobs."""
    SYNC = "sync"
    ASYNC = "async"
    THREAD = "thread"
    PROCESS = "process"


@dataclass
class ExecutorConfig:
    """Configuration for the job executor."""
    max_workers: int = 10
    default_timeout: float = 300.0
    default_mode: ExecutionMode = ExecutionMode.THREAD
    enable_metrics: bool = True


class TURXZJobExecutor:
    """
    Executes jobs with support for sync, async, thread, and process execution.
    
    Provides timeout handling, retry logic, and concurrency control.
    """
    
    def __init__(
        self,
        max_workers: int = 10,
        default_timeout: float = 300.0,
        default_mode: ExecutionMode = ExecutionMode.THREAD,
    ):
        self.config = ExecutorConfig(
            max_workers=max_workers,
            default_timeout=default_timeout,
            default_mode=default_mode,
        )
        
        self._thread_pool: concurrent.futures.ThreadPoolExecutor | None = None
        self._process_pool: concurrent.futures.ProcessPoolExecutor | None = None
        self._lock = threading.RLock()
        self._running = False
        self._active_executions: dict[str, asyncio.Task] = {}
        self._execution_count = 0
        self._error_count = 0
        self._total_duration = 0.0
        
        logger.info("TURXZJobExecutor initialized", 
                   max_workers=max_workers,
                   default_timeout=default_timeout,
                   default_mode=default_mode.value)

    def start(self) -> None:
        """Start the executor (initialize pools)."""
        with self._lock:
            if self._running:
                return
            
            self._thread_pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=self.config.max_workers,
                thread_name_prefix="turxz-job-"
            )
            self._process_pool = concurrent.futures.ProcessPoolExecutor(
                max_workers=min(self.config.max_workers, 4)
            )
            self._running = True
            
            logger.info("TURXZJobExecutor started")

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the executor."""
        with self._lock:
            if not self._running:
                return
            
            self._running = False
            
            # Cancel active async executions
            for task in self._active_executions.values():
                task.cancel()
            
            if self._thread_pool:
                self._thread_pool.shutdown(wait=wait)
                self._thread_pool = None
            
            if self._process_pool:
                self._process_pool.shutdown(wait=wait)
                self._process_pool = None
            
            logger.info("TURXZJobExecutor shutdown")

    def execute(
        self,
        job: JobDefinition,
        mode: ExecutionMode | None = None,
        timeout: float | None = None,
    ) -> ExecutionResult:
        """
        Execute a job synchronously.
        
        Args:
            job: Job definition to execute
            mode: Execution mode override
            timeout: Timeout override in seconds
            
        Returns:
            ExecutionResult with outcome
        """
        mode = mode or self.config.default_mode
        timeout = timeout or job.timeout or self.config.default_timeout
        
        if mode == ExecutionMode.SYNC:
            return self._execute_sync(job, timeout)
        elif mode == ExecutionMode.ASYNC:
            return self._execute_async(job, timeout)
        elif mode == ExecutionMode.THREAD:
            return self._execute_thread(job, timeout)
        elif mode == ExecutionMode.PROCESS:
            return self._execute_process(job, timeout)
        else:
            return self._execute_thread(job, timeout)

    def execute_async(
        self,
        job: JobDefinition,
        mode: ExecutionMode | None = None,
        timeout: float | None = None,
    ) -> asyncio.Future:
        """
        Execute a job asynchronously, returning a Future.
        """
        mode = mode or self.config.default_mode
        timeout = timeout or job.timeout or self.config.default_timeout
        
        loop = asyncio.get_event_loop()
        
        if mode == ExecutionMode.ASYNC:
            return loop.create_task(self._run_async_job(job, timeout))
        else:
            return loop.run_in_executor(
                self._thread_pool,
                lambda: self.execute(job, mode, timeout)
            )

    def _execute_sync(self, job: JobDefinition, timeout: float) -> ExecutionResult:
        """Execute job synchronously in current thread."""
        result = ExecutionResult(job_id=job.job_id, success=False)
        started = time.time()
        result.started_at = datetime.now()
        
        try:
            handler = job.handler
            if inspect.iscoroutinefunction(handler):
                # Run async function in new event loop
                loop = asyncio.new_event_loop()
                try:
                    result.result = loop.run_until_complete(
                        asyncio.wait_for(handler(*job.args, **job.kwargs), timeout=timeout)
                    )
                finally:
                    loop.close()
            else:
                result.result = handler(*job.args, **job.kwargs)
            
            result.success = True
            
        except asyncio.TimeoutError:
            result.error = f"Job timed out after {timeout}s"
            result.duration = time.time() - started
            self._error_count += 1
            
        except Exception as e:
            result.error = f"{type(e).__name__}: {e!s}"
            result.duration = time.time() - started
            self._error_count += 1
            logger.error("Job execution failed", job_id=job.job_id, error=str(e))
        
        result.duration = time.time() - started
        result.completed_at = datetime.now()
        self._update_stats(result)
        
        return result

    def _execute_thread(self, job: JobDefinition, timeout: float) -> ExecutionResult:
        """Execute job in thread pool."""
        if not self._thread_pool:
            self.start()
        
        future = self._thread_pool.submit(self._execute_sync, job, timeout)
        try:
            return future.result(timeout=timeout + 1.0)  # Small buffer
        except concurrent.futures.TimeoutError:
            return ExecutionResult(
                job_id=job.job_id,
                success=False,
                error=f"Thread pool timeout after {timeout}s",
                duration=timeout,
                completed_at=datetime.now(),
            )

    def _execute_process(self, job: JobDefinition, timeout: float) -> ExecutionResult:
        """Execute job in process pool."""
        if not self._process_pool:
            self.start()
        
        # For process execution, we need a picklable function
        # This is a simplified version - in production use a proper serialization
        future = self._process_pool.submit(self._execute_sync, job, timeout)
        try:
            return future.result(timeout=timeout + 1.0)
        except concurrent.futures.TimeoutError:
            return ExecutionResult(
                job_id=job.job_id,
                success=False,
                error=f"Process pool timeout after {timeout}s",
                duration=timeout,
                completed_at=datetime.now(),
            )

    def _execute_async(self, job: JobDefinition, timeout: float) -> ExecutionResult:
        """Execute async job."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self._run_async_job(job, timeout))
        finally:
            loop.close()

    async def _run_async_job(self, job: JobDefinition, timeout: float) -> ExecutionResult:
        """Run an async job with timeout."""
        result = ExecutionResult(job_id=job.job_id, success=False)
        started = time.time()
        result.started_at = datetime.now()
        
        try:
            handler = job.handler
            if not inspect.iscoroutinefunction(handler):
                # Wrap sync function
                result.result = handler(*job.args, **job.kwargs)
            else:
                result.result = await asyncio.wait_for(
                    handler(*job.args, **job.kwargs),
                    timeout=timeout
                )
            
            result.success = True
            
        except asyncio.TimeoutError:
            result.error = f"Job timed out after {timeout}s"
            self._error_count += 1
            
        except Exception as e:
            result.error = f"{type(e).__name__}: {e!s}"
            self._error_count += 1
            logger.error("Async job execution failed", job_id=job.job_id, error=str(e))
        
        result.duration = time.time() - started
        result.completed_at = datetime.now()
        self._update_stats(result)
        
        return result

    def _update_stats(self, result: ExecutionResult) -> None:
        """Update executor statistics."""
        self._execution_count += 1
        self._total_duration += result.duration

    def get_status(self) -> dict[str, Any]:
        """Get executor status."""
        with self._lock:
            return {
                "running": self._running,
                "max_workers": self.config.max_workers,
                "active_executions": len(self._active_executions),
                "total_executions": self._execution_count,
                "error_count": self._error_count,
                "success_rate": (
                    (self._execution_count - self._error_count) / self._execution_count
                    if self._execution_count > 0 else 0.0
                ),
                "avg_duration": (
                    self._total_duration / self._execution_count
                    if self._execution_count > 0 else 0.0
                ),
                "thread_pool_active": self._thread_pool is not None,
                "process_pool_active": self._process_pool is not None,
            }

    def get_active_executions(self) -> list[str]:
        """Get list of active execution job IDs."""
        with self._lock:
            return list(self._active_executions.keys())

    def cancel_execution(self, job_id: str) -> bool:
        """Cancel an active execution."""
        with self._lock:
            task = self._active_executions.get(job_id)
            if task:
                task.cancel()
                del self._active_executions[job_id]
                return True
        return False