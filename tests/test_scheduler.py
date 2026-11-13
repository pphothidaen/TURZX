"""
Tests for TURXZ Native Scheduler Infrastructure.
"""

import asyncio
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# Import TURXZ scheduler components
from turxz_scheduled_jobs import (
    ExecutionResult,
    JobDefinition,
    JobPriority,
    ScheduledJob,
    ScheduleType,
    TURXZJobExecutor,
    TURXZJobManager,
    TURXZScheduler,
    TURXZSchedulerConfig,
    TURXZSchedulerHealthCheck,
)


class TestJobDefinition:
    """Test JobDefinition dataclass."""
    
    def test_create_job_definition(self):
        """Test creating a job definition."""
        def sample_handler():
            return "test"
        
        job = JobDefinition(
            job_id="test_job",
            name="Test Job",
            handler=sample_handler,
            args=(1, 2),
            kwargs={"key": "value"},
            timeout=60.0,
            priority=JobPriority.HIGH,
            tags=["test"],
        )
        
        assert job.job_id == "test_job"
        assert job.name == "Test Job"
        assert job.handler == sample_handler
        assert job.args == (1, 2)
        assert job.kwargs == {"key": "value"}
        assert job.timeout == 60.0
        assert job.priority == JobPriority.HIGH
        assert job.tags == ["test"]
    
    def test_auto_job_id(self):
        """Test auto-generation of job_id."""
        def sample_handler():
            pass
        
        job = JobDefinition(
            name="Test Job",
            handler=sample_handler,
        )
        
        assert job.job_id is not None
        assert len(job.job_id) > 0


class TestTURXZJobManager:
    """Test TURXZJobManager."""
    
    def setup_method(self):
        self.job_manager = TURXZJobManager(max_history=100)
    
    def test_register_job(self):
        """Test registering a job."""
        def handler():
            return "result"
        
        job = JobDefinition(
            job_id="test_job",
            name="Test Job",
            handler=handler,
        )
        
        job_id = self.job_manager.register_job(job)
        assert job_id == "test_job"
        assert self.job_manager.get_job("test_job") == job
    
    def test_unregister_job(self):
        """Test unregistering a job."""
        def handler():
            pass
        
        job = JobDefinition(job_id="test_job", name="Test Job", handler=handler)
        self.job_manager.register_job(job)
        
        assert self.job_manager.unregister_job("test_job") is True
        assert self.job_manager.get_job("test_job") is None
        assert self.job_manager.unregister_job("nonexistent") is False
    
    def test_list_jobs(self):
        """Test listing jobs."""
        def handler():
            pass
        
        job1 = JobDefinition(job_id="job1", name="Job 1", handler=handler, tags=["tag1"])
        job2 = JobDefinition(job_id="job2", name="Job 2", handler=handler, tags=["tag2"])
        job3 = JobDefinition(job_id="job3", name="Job 3", handler=handler, tags=["tag1"])
        
        self.job_manager.register_job(job1)
        self.job_manager.register_job(job2)
        self.job_manager.register_job(job3)
        
        all_jobs = self.job_manager.list_jobs()
        assert len(all_jobs) == 3
        
        tagged_jobs = self.job_manager.list_jobs(tags=["tag1"])
        assert len(tagged_jobs) == 2
    
    def test_get_jobs_by_priority(self):
        """Test getting jobs by priority."""
        def handler():
            pass
        
        job1 = JobDefinition(job_id="job1", name="Job 1", handler=handler, priority=JobPriority.HIGH)
        job2 = JobDefinition(job_id="job2", name="Job 2", handler=handler, priority=JobPriority.LOW)
        job3 = JobDefinition(job_id="job3", name="Job 3", handler=handler, priority=JobPriority.HIGH)
        
        self.job_manager.register_job(job1)
        self.job_manager.register_job(job2)
        self.job_manager.register_job(job3)
        
        high_jobs = self.job_manager.get_jobs_by_priority(JobPriority.HIGH)
        assert len(high_jobs) == 2
        
        low_jobs = self.job_manager.get_jobs_by_priority(JobPriority.LOW)
        assert len(low_jobs) == 1
    
    def test_record_execution(self):
        """Test recording execution results."""
        def handler():
            return "result"
        
        job = JobDefinition(job_id="test_job", name="Test Job", handler=handler)
        self.job_manager.register_job(job)
        
        result = ExecutionResult(
            job_id="test_job",
            success=True,
            result="test_result",
            duration=1.5,
        )
        
        self.job_manager.record_execution(result)
        
        history = self.job_manager.get_execution_history(job_id="test_job")
        assert len(history) == 1
        assert history[0].success is True
        assert history[0].result == "test_result"
    
    def test_get_job_stats(self):
        """Test getting job statistics."""
        def handler():
            return "result"
        
        job = JobDefinition(job_id="test_job", name="Test Job", handler=handler)
        self.job_manager.register_job(job)
        
        # Record some executions
        for i in range(5):
            result = ExecutionResult(
                job_id="test_job",
                success=i < 4,  # 4 success, 1 failure
                duration=1.0,
            )
            self.job_manager.record_execution(result)
        
        stats = self.job_manager.get_job_stats("test_job")
        assert stats["total_executions"] == 5
        assert stats["successful"] == 4
        assert stats["failed"] == 1
        assert stats["success_rate"] == 0.8
        assert stats["avg_duration"] == 1.0


class TestTURXZJobExecutor:
    """Test TURXZJobExecutor."""
    
    def setup_method(self):
        self.executor = TURXZJobExecutor(max_workers=2)
        self.executor.start()
    
    def teardown_method(self):
        self.executor.shutdown(wait=True)
    
    def test_execute_sync_job(self):
        """Test executing a synchronous job."""
        def sync_handler(x, y):
            return x + y
        
        job = JobDefinition(
            job_id="sync_job",
            name="Sync Job",
            handler=sync_handler,
            args=(2, 3),
        )
        
        result = self.executor.execute(job)
        
        assert result.success is True
        assert result.result == 5
        assert result.duration >= 0
    
    def test_execute_async_job(self):
        """Test executing an async job."""
        async def async_handler(x, y):
            await asyncio.sleep(0.01)
            return x * y
        
        job = JobDefinition(
            job_id="async_job",
            name="Async Job",
            handler=async_handler,
            args=(3, 4),
        )
        
        result = self.executor.execute(job, mode="async")
        
        assert result.success is True
        assert result.result == 12
    
    def test_execute_thread_job(self):
        """Test executing a job in thread pool."""
        def thread_handler(x):
            time.sleep(0.01)
            return x * 2
        
        job = JobDefinition(
            job_id="thread_job",
            name="Thread Job",
            handler=thread_handler,
            args=(5,),
        )
        
        result = self.executor.execute(job, mode="thread")
        
        assert result.success is True
        assert result.result == 10
    
    def test_execute_failing_job(self):
        """Test executing a job that fails."""
        def failing_handler():
            raise ValueError("Test error")
        
        job = JobDefinition(
            job_id="failing_job",
            name="Failing Job",
            handler=failing_handler,
        )
        
        result = self.executor.execute(job)
        
        assert result.success is False
        assert "ValueError" in result.error
        assert "Test error" in result.error
    
    def test_execute_timeout_job(self):
        """Test executing a job that times out."""
        def slow_handler():
            time.sleep(10)
            return "done"
        
        job = JobDefinition(
            job_id="timeout_job",
            name="Timeout Job",
            handler=slow_handler,
            timeout=0.1,  # Very short timeout
        )
        
        result = self.executor.execute(job, timeout=0.1)
        
        assert result.success is False
        assert "timeout" in result.error.lower() or "timed out" in result.error.lower()
    
    def test_get_status(self):
        """Test getting executor status."""
        status = self.executor.get_status()
        
        assert status["running"] is True
        assert status["max_workers"] == 2
        assert "total_executions" in status
        assert "error_count" in status
        assert "success_rate" in status


class TestTURXZScheduler:
    """Test TURXZScheduler."""
    
    def setup_method(self):
        self.job_manager = TURXZJobManager()
        self.executor = TURXZJobExecutor(max_workers=2)
        self.executor.start()
        self.scheduler = TURXZScheduler(
            job_manager=self.job_manager,
            executor=self.executor,
            check_interval=0.1,
        )
    
    def teardown_method(self):
        self.scheduler.stop()
        self.executor.shutdown(wait=True)
    
    def test_add_interval_job(self):
        """Test adding an interval job."""
        def handler():
            return "interval"
        
        job_def = JobDefinition(
            job_id="interval_job",
            name="Interval Job",
            handler=handler,
        )
        
        job_id = self.scheduler.add_job(
            name="Test Interval",
            schedule_type=ScheduleType.INTERVAL,
            schedule_value=60.0,
            job_definition=job_def,
        )
        
        assert job_id is not None
        job = self.scheduler.get_job(job_id)
        assert job is not None
        assert job.schedule_type == ScheduleType.INTERVAL
        assert job.schedule_value == 60.0
        assert job.enabled is True
    
    def test_add_cron_job(self):
        """Test adding a cron job."""
        def handler():
            return "cron"
        
        job_def = JobDefinition(
            job_id="cron_job",
            name="Cron Job",
            handler=handler,
        )
        
        job_id = self.scheduler.add_job(
            name="Test Cron",
            schedule_type=ScheduleType.CRON,
            schedule_value="0 * * * *",
            job_definition=job_def,
        )
        
        job = self.scheduler.get_job(job_id)
        assert job.schedule_type == ScheduleType.CRON
        assert job.schedule_value == "0 * * * *"
    
    def test_add_once_job(self):
        """Test adding a one-shot job."""
        def handler():
            return "once"
        
        job_def = JobDefinition(
            job_id="once_job",
            name="Once Job",
            handler=handler,
        )
        
        future_time = datetime.now() + timedelta(seconds=10)
        job_id = self.scheduler.add_job(
            name="Test Once",
            schedule_type=ScheduleType.ONCE,
            schedule_value=future_time,
            job_definition=job_def,
        )
        
        job = self.scheduler.get_job(job_id)
        assert job.schedule_type == ScheduleType.ONCE
        assert job.next_run == future_time
    
    def test_add_startup_job(self):
        """Test adding a startup job."""
        def handler():
            return "startup"
        
        job_def = JobDefinition(
            job_id="startup_job",
            name="Startup Job",
            handler=handler,
        )
        
        job_id = self.scheduler.add_job(
            name="Test Startup",
            schedule_type=ScheduleType.STARTUP,
            schedule_value=0,
            job_definition=job_def,
        )
        
        job = self.scheduler.get_job(job_id)
        assert job.schedule_type == ScheduleType.STARTUP
        assert job.next_run is not None
    
    def test_remove_job(self):
        """Test removing a job."""
        def handler():
            pass
        
        job_def = JobDefinition(job_id="remove_job", name="Remove Job", handler=handler)
        job_id = self.scheduler.add_job(
            name="Test Remove",
            schedule_type=ScheduleType.INTERVAL,
            schedule_value=60.0,
            job_definition=job_def,
        )
        
        assert self.scheduler.remove_job(job_id) is True
        assert self.scheduler.get_job(job_id) is None
        assert self.scheduler.remove_job("nonexistent") is False
    
    def test_enable_disable_job(self):
        """Test enabling and disabling a job."""
        def handler():
            pass
        
        job_def = JobDefinition(job_id="enable_job", name="Enable Job", handler=handler)
        job_id = self.scheduler.add_job(
            name="Test Enable",
            schedule_type=ScheduleType.INTERVAL,
            schedule_value=60.0,
            job_definition=job_def,
        )
        
        # Disable
        assert self.scheduler.disable_job(job_id) is True
        job = self.scheduler.get_job(job_id)
        assert job.enabled is False
        
        # Enable
        assert self.scheduler.enable_job(job_id) is True
        job = self.scheduler.get_job(job_id)
        assert job.enabled is True
    
    def test_run_job_now(self):
        """Test running a job immediately."""
        def handler():
            return "manual_run"
        
        job_def = JobDefinition(job_id="manual_job", name="Manual Job", handler=handler)
        job_id = self.scheduler.add_job(
            name="Test Manual",
            schedule_type=ScheduleType.INTERVAL,
            schedule_value=60.0,
            job_definition=job_def,
        )
        
        result = self.scheduler.run_job_now(job_id)
        
        assert result is not None
        assert result.success is True
        assert result.result == "manual_run"
    
    def test_start_stop(self):
        """Test starting and stopping the scheduler."""
        assert self.scheduler._running is False
        
        self.scheduler.start()
        assert self.scheduler._running is True
        
        self.scheduler.stop()
        assert self.scheduler._running is False
    
    def test_get_status(self):
        """Test getting scheduler status."""
        def handler():
            pass
        
        job_def = JobDefinition(job_id="status_job", name="Status Job", handler=handler)
        self.scheduler.add_job(
            name="Test Status",
            schedule_type=ScheduleType.INTERVAL,
            schedule_value=60.0,
            job_definition=job_def,
        )
        
        status = self.scheduler.get_status()
        
        assert status["running"] is False  # Not started yet
        assert status["total_jobs"] == 1
        assert status["enabled_jobs"] == 1
        assert "jobs_by_type" in status
        assert "executor_status" in status


class TestTURXZSchedulerConfig:
    """Test TURXZSchedulerConfig."""
    
    def test_default_config(self):
        """Test default configuration."""
        config = TURXZSchedulerConfig()
        
        assert config.scheduler.check_interval == 1.0
        assert config.scheduler.max_concurrent_jobs == 10
        assert config.executor.max_workers == 10
        assert config.job_defaults.timeout == 300.0
    
    def test_config_to_dict(self):
        """Test converting config to dictionary."""
        config = TURXZSchedulerConfig()
        data = config.to_dict()
        
        assert "scheduler" in data
        assert "executor" in data
        assert "job_defaults" in data
        assert "health_check" in data
        assert "version" in data
    
    def test_config_merge(self):
        """Test merging configurations."""
        config1 = TURXZSchedulerConfig()
        config2 = TURXZSchedulerConfig()
        
        config2.scheduler.check_interval = 5.0
        config2.executor.max_workers = 20
        
        merged = config1.merge(config2)
        
        assert merged.scheduler.check_interval == 5.0
        assert merged.executor.max_workers == 20
    
    def test_config_from_env(self):
        """Test loading config from environment."""
        with patch.dict('os.environ', {
            'TURXZ_SCHEDULER_CHECK_INTERVAL': '2.0',
            'TURXZ_EXECUTOR_MAX_WORKERS': '5',
            'TURXZ_JOB_TIMEOUT': '600.0',
        }):
            config = TURXZSchedulerConfig.from_env()
            
            assert config.scheduler.check_interval == 2.0
            assert config.executor.max_workers == 5
            assert config.job_defaults.timeout == 600.0


class TestTURXZSchedulerHealthCheck:
    """Test TURXZSchedulerHealthCheck."""
    
    def setup_method(self):
        self.job_manager = TURXZJobManager()
        self.executor = TURXZJobExecutor(max_workers=2)
        self.executor.start()
        self.scheduler = TURXZScheduler(
            job_manager=self.job_manager,
            executor=self.executor,
            check_interval=0.1,
        )
        
        from turxz_scheduled_jobs.config import HealthCheckSettings
        self.health_check = TURXZSchedulerHealthCheck(
            scheduler=self.scheduler,
            executor=self.executor,
            settings=HealthCheckSettings(enabled=False),  # Disable auto-run
        )
    
    def teardown_method(self):
        self.scheduler.stop()
        self.executor.shutdown(wait=True)
    
    def test_run_checks(self):
        """Test running health checks."""
        results = self.health_check.run_checks()
        
        assert "scheduler_running" in results
        assert "executor_healthy" in results
        assert "job_queue_healthy" in results
        assert "disk_space" in results
        assert "memory_usage" in results
        
        for name, result in results.items():
            assert result.name == name
            assert result.status in ["healthy", "degraded", "unhealthy", "unknown"]
            assert isinstance(result.message, str)
    
    def test_get_overall_status(self):
        """Test getting overall health status."""
        self.health_check.run_checks()
        status = self.health_check.get_overall_status()
        
        assert status in ["healthy", "degraded", "unhealthy", "unknown"]
    
    def test_get_last_results(self):
        """Test getting last results."""
        self.health_check.run_checks()
        results = self.health_check.get_last_results()
        
        assert len(results) == 5  # 5 default checks
    
    def test_get_status(self):
        """Test getting health check system status."""
        self.health_check.run_checks()
        status = self.health_check.get_status()
        
        assert "running" in status
        assert "enabled" in status
        assert "overall_status" in status
        assert "checks" in status
        assert "failure_counts" in status


class TestScheduledJob:
    """Test ScheduledJob dataclass."""
    
    def test_scheduled_job_creation(self):
        """Test creating a scheduled job."""
        def handler():
            pass
        
        job_def = JobDefinition(job_id="test", name="Test", handler=handler)
        
        scheduled = ScheduledJob(
            job_id="sched_1",
            name="Scheduled Job",
            schedule_type=ScheduleType.INTERVAL,
            schedule_value=60.0,
            job_definition=job_def,
        )
        
        assert scheduled.job_id == "sched_1"
        assert scheduled.next_run is not None
        assert scheduled.run_count == 0
    
    def test_should_run(self):
        """Test should_run logic."""
        def handler():
            pass
        
        job_def = JobDefinition(job_id="test", name="Test", handler=handler)
        
        # Job that should run now
        past_time = datetime.now() - timedelta(seconds=10)
        scheduled = ScheduledJob(
            job_id="sched_1",
            name="Scheduled Job",
            schedule_type=ScheduleType.ONCE,
            schedule_value=past_time,
            job_definition=job_def,
        )
        
        assert scheduled.should_run() is True
        
        # Job that should not run yet
        future_time = datetime.now() + timedelta(seconds=10)
        scheduled2 = ScheduledJob(
            job_id="sched_2",
            name="Scheduled Job 2",
            schedule_type=ScheduleType.ONCE,
            schedule_value=future_time,
            job_definition=job_def,
        )
        
        assert scheduled2.should_run() is False
    
    def test_mark_run(self):
        """Test marking a job as run."""
        def handler():
            pass
        
        job_def = JobDefinition(job_id="test", name="Test", handler=handler)
        
        scheduled = ScheduledJob(
            job_id="sched_1",
            name="Scheduled Job",
            schedule_type=ScheduleType.INTERVAL,
            schedule_value=60.0,
            job_definition=job_def,
        )
        
        initial_run_count = scheduled.run_count
        initial_next_run = scheduled.next_run
        
        scheduled.mark_run()
        
        assert scheduled.run_count == initial_run_count + 1
        assert scheduled.last_run is not None
        assert scheduled.next_run != initial_next_run
        assert scheduled.next_run > scheduled.last_run


class TestScheduleValidation:
    """Test Fail-Closed validation on ScheduledJob parameters."""

    def test_interval_validation_zero_or_negative(self):
        """Interval <= 0 must raise ValueError."""
        def handler():
            pass
        job_def = JobDefinition(job_id="test", name="Test", handler=handler)

        with pytest.raises(ValueError, match="positive number"):
            ScheduledJob(
                job_id="inv_1",
                name="Invalid Interval",
                schedule_type=ScheduleType.INTERVAL,
                schedule_value=0,
                job_definition=job_def,
            )

        with pytest.raises(ValueError, match="positive number"):
            ScheduledJob(
                job_id="inv_2",
                name="Invalid Interval",
                schedule_type=ScheduleType.INTERVAL,
                schedule_value=-10.0,
                job_definition=job_def,
            )

    def test_cron_validation_invalid_parts(self):
        """Cron expression with not exactly 5 parts must raise ValueError."""
        def handler():
            pass
        job_def = JobDefinition(job_id="test", name="Test", handler=handler)

        with pytest.raises(ValueError, match="5 space-separated fields"):
            ScheduledJob(
                job_id="cron_inv_1",
                name="Invalid Cron",
                schedule_type=ScheduleType.CRON,
                schedule_value="invalid",
                job_definition=job_def,
            )

        with pytest.raises(ValueError, match="5 space-separated fields"):
            ScheduledJob(
                job_id="cron_inv_2",
                name="Invalid Cron",
                schedule_type=ScheduleType.CRON,
                schedule_value="* * *",
                job_definition=job_def,
            )

    def test_valid_cron_succeeds(self):
        """Valid 5-part cron must calculate next run successfully."""
        def handler():
            pass
        job_def = JobDefinition(job_id="test", name="Test", handler=handler)

        job = ScheduledJob(
            job_id="cron_valid",
            name="Valid Cron",
            schedule_type=ScheduleType.CRON,
            schedule_value="*/5 * * * *",
            job_definition=job_def,
        )
        assert job.next_run is not None
        assert job.next_run > datetime.now() - timedelta(seconds=1)


class TestTURXZSchedulerCLI:
    """Test turxz-scheduler command-line interface."""

    def test_cli_help(self):
        """Test --help exits cleanly with code 0."""
        from turxz_scheduled_jobs.cli import main as cli_main

        with patch.object(sys, "argv", ["turxz-scheduler", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                cli_main()
            assert exc_info.value.code == 0

    def test_cli_status(self, capsys):
        """Test status command returns JSON status."""
        from turxz_scheduled_jobs.cli import main as cli_main

        with patch.object(sys, "argv", ["turxz-scheduler", "status"]):
            cli_main()
            captured = capsys.readouterr()
            assert "scheduler" in captured.out
            assert "health" in captured.out
            assert "executor" in captured.out

    def test_cli_add_job_interval_and_dynamic_handler(self, capsys):
        """Test add-job with interval and dynamic handler."""
        from turxz_scheduled_jobs.cli import main as cli_main

        test_args = [
            "turxz-scheduler", "add-job",
            "--name", "test_interval_job",
            "--type", "interval",
            "--value", "45",
            "--handler", "os:getcwd",
            "--priority", "high"
        ]
        with patch.object(sys, "argv", test_args):
            cli_main()
            captured = capsys.readouterr()
            assert "Job added: cli_job_test_interval_job" in captured.out

    def test_cli_add_job_cron(self, capsys):
        """Test add-job with valid cron expression."""
        from turxz_scheduled_jobs.cli import main as cli_main

        test_args = [
            "turxz-scheduler", "add-job",
            "--name", "test_cron_job",
            "--type", "cron",
            "--value", "*/10 * * * *",
            "--handler", "cleanup"
        ]
        with patch.object(sys, "argv", test_args):
            cli_main()
            captured = capsys.readouterr()
            assert "Job added: cli_job_test_cron_job" in captured.out

    def test_cli_add_job_invalid_cron_fail_closed(self, capsys):
        """Invalid cron expression must exit with code 1."""
        from turxz_scheduled_jobs.cli import main as cli_main

        test_args = [
            "turxz-scheduler", "add-job",
            "--name", "bad_cron",
            "--type", "cron",
            "--value", "invalid_cron",
            "--handler", "cleanup"
        ]
        with patch.object(sys, "argv", test_args):
            with pytest.raises(SystemExit) as exc_info:
                cli_main()
            assert exc_info.value.code == 1
            captured = capsys.readouterr()
            assert "Error adding job" in captured.err

    def test_cli_add_job_invalid_interval_fail_closed(self, capsys):
        """Non-numeric or negative interval must exit with code 1."""
        from turxz_scheduled_jobs.cli import main as cli_main

        test_args = [
            "turxz-scheduler", "add-job",
            "--name", "bad_interval",
            "--type", "interval",
            "--value", "-10",
            "--handler", "cleanup"
        ]
        with patch.object(sys, "argv", test_args):
            with pytest.raises(SystemExit) as exc_info:
                cli_main()
            assert exc_info.value.code == 1

    def test_cli_remove_job_positional_and_flag(self, capsys):
        """Test remove-job with both positional ID and --id flag."""
        from turxz_scheduled_jobs.cli import main as cli_main

        # Nonexistent job should exit 1
        with patch.object(sys, "argv", ["turxz-scheduler", "remove-job", "nonexistent_job"]):
            with pytest.raises(SystemExit) as exc:
                cli_main()
            assert exc.value.code == 1

        with patch.object(sys, "argv", ["turxz-scheduler", "remove-job", "--id", "nonexistent_job"]):
            with pytest.raises(SystemExit) as exc:
                cli_main()
            assert exc.value.code == 1

        # Missing ID should exit 2
        with patch.object(sys, "argv", ["turxz-scheduler", "remove-job"]):
            with pytest.raises(SystemExit) as exc:
                cli_main()
            assert exc.value.code == 2

    def test_cli_run_job_positional_and_flag(self, capsys):
        """Test run-job with positional ID and --id flag."""
        from turxz_scheduled_jobs.cli import main as cli_main

        with patch.object(sys, "argv", ["turxz-scheduler", "run-job", "nonexistent_job"]):
            with pytest.raises(SystemExit) as exc:
                cli_main()
            assert exc.value.code == 1

        with patch.object(sys, "argv", ["turxz-scheduler", "run-job", "--id", "nonexistent_job"]):
            with pytest.raises(SystemExit) as exc:
                cli_main()
            assert exc.value.code == 1

        # Running default registered job
        with patch.object(sys, "argv", ["turxz-scheduler", "run-job", "--id", "cleanup_history"]):
            cli_main()
            captured = capsys.readouterr()
            assert "completed" in captured.out or "result" in captured.out or "status" in captured.out


class TestProcessTokenAuth:
    """Test Lightweight Local Process Token Authentication (AGENTS.md Rule 5)."""

    def setup_method(self):
        import os
        from turzx.auth import clear_process_token
        clear_process_token()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.custom_token_path = Path(self.temp_dir.name) / "turxz-test" / "process.token"
        os.environ["TURXZ_TOKEN_PATH"] = str(self.custom_token_path)

    def teardown_method(self):
        import os
        from turzx.auth import clear_process_token
        clear_process_token()
        os.environ.pop("TURXZ_TOKEN_PATH", None)
        self.temp_dir.cleanup()

    def test_token_generation_and_format(self):
        """Token must be 64-character hex string stored with 0600 mode."""
        import os
        import stat
        from turzx.auth import generate_process_token, get_process_token

        token = generate_process_token()
        assert isinstance(token, str)
        assert len(token) == 64
        assert int(token, 16) > 0

        # Check POSIX file mode is 0600
        file_stat = self.custom_token_path.stat()
        mode = stat.S_IMODE(file_stat.st_mode)
        assert mode == 0o600

        # Check environment variable is set
        assert os.environ.get("TURXZ_PROCESS_TOKEN") == token

        # Verify get_process_token retrieves it
        retrieved = get_process_token()
        assert retrieved == token

    def test_verify_process_token_constant_time(self):
        """Test valid, invalid, and empty tokens."""
        from turzx.auth import generate_process_token, verify_process_token

        token = generate_process_token()
        assert verify_process_token(token) is True
        assert verify_process_token(token + "x") is False
        assert verify_process_token("wrong_token") is False
        assert verify_process_token("") is False
        assert verify_process_token(None) is False

    def test_require_process_token_fail_closed(self):
        """require_process_token must raise PermissionDeniedError on invalid token."""
        from turzx.auth import PermissionDeniedError, generate_process_token, require_process_token

        token = generate_process_token()
        # Valid token does not raise
        require_process_token(token)

        # Invalid tokens raise PermissionDeniedError
        with pytest.raises(PermissionDeniedError, match="Fail-Closed"):
            require_process_token("tampered_token")

        with pytest.raises(PermissionDeniedError, match="Fail-Closed"):
            require_process_token(None)

        with pytest.raises(PermissionDeniedError, match="Fail-Closed"):
            require_process_token("")

    def test_insecure_permissions_rejected(self):
        """World/group-accessible token file must be rejected (Fail-Closed)."""
        import os
        from turzx.auth import clear_process_token, generate_process_token, get_process_token

        token = generate_process_token()
        # Clear env var to force reading from file
        os.environ.pop("TURXZ_PROCESS_TOKEN", None)

        # Make file world-readable (0666)
        os.chmod(self.custom_token_path, 0o666)
        rejected = get_process_token()
        assert rejected is None, "Insecure token file must be rejected under Rule 5"

    def test_clear_process_token(self):
        """clear_process_token removes token file and env var."""
        import os
        from turzx.auth import clear_process_token, generate_process_token

        token = generate_process_token()
        assert self.custom_token_path.is_file()
        clear_process_token()
        assert not self.custom_token_path.is_file()
        assert "TURXZ_PROCESS_TOKEN" not in os.environ


class TestHealthChecksPowerAndMemory:
    """Test memory pressure and thermal health checks in scheduler."""

    def test_memory_usage_check_with_high_pressure(self):
        from unittest.mock import patch
        from turxz_scheduled_jobs.health import HealthStatus, MemoryUsageCheck
        from turzx.power import MemoryPressureInfo

        check = MemoryUsageCheck(threshold_percent=90.0)
        # Mock high memory pressure: 15% free RAM (< 20% threshold)
        mock_info = MemoryPressureInfo(free_percent=15.0, is_critical=True)

        with patch("turzx.power.SystemMemoryManager.get_memory_pressure", return_value=mock_info):
            result = check.check()
            assert result.status == HealthStatus.UNHEALTHY
            assert "High memory pressure" in result.message

    def test_thermal_health_check_states(self):
        from turxz_scheduled_jobs.health import HealthStatus, ThermalHealthCheck
        from turzx.power import SystemPowerManager

        pm = SystemPowerManager.get_instance()
        check = ThermalHealthCheck()

        # Normal (60°C)
        pm.thermal_manager.set_simulated_temperature(60.0)
        res_norm = check.check()
        assert res_norm.status == HealthStatus.HEALTHY

        # Moderate (> 75°C)
        pm.thermal_manager.set_simulated_temperature(78.0)
        res_mod = check.check()
        assert res_mod.status == HealthStatus.DEGRADED

        # Critical (> 85°C)
        pm.thermal_manager.set_simulated_temperature(92.0)
        res_crit = check.check()
        assert res_crit.status == HealthStatus.UNHEALTHY

        # Reset
        pm.thermal_manager.set_simulated_temperature(None)

    def test_power_mode_health_check(self):
        from turxz_scheduled_jobs.health import HealthStatus, PowerModeHealthCheck
        from turzx.power import PowerMode, SystemPowerManager

        pm = SystemPowerManager.get_instance()
        pm.set_power_mode(PowerMode.BALANCED)

        check = PowerModeHealthCheck()
        res = check.check()
        assert res.status == HealthStatus.HEALTHY
        assert "balanced" in res.message.lower()


class TestExecutorResourceGating:
    """Test job executor dynamic resource allocation and throttling."""

    def test_executor_throttles_power_critical_task_under_memory_pressure(self):
        from unittest.mock import patch
        from turxz_scheduled_jobs.executor import TURXZJobExecutor
        from turxz_scheduled_jobs.job_manager import JobDefinition, TaskPowerCategory, TaskUrgency
        from turzx.power import MemoryPressureInfo, PowerMode, SystemPowerManager

        pm = SystemPowerManager.get_instance()
        pm.set_power_mode(PowerMode.BALANCED)

        executor = TURXZJobExecutor(max_workers=2)
        called = False

        def heavy_task():
            nonlocal called
            called = True
            return "done"

        job = JobDefinition(
            job_id="heavy_ai_job",
            name="Heavy AI Task",
            handler=heavy_task,
            power_category=TaskPowerCategory.POWER_CRITICAL,
            urgency=TaskUrgency.LOW,
        )

        # Mock high memory pressure: 14% free RAM (< 20%)
        mock_mem = MemoryPressureInfo(free_percent=14.0, is_critical=True)
        with patch.object(pm.memory_manager, "get_memory_pressure", return_value=mock_mem):
            result = executor.execute(job)
            assert not result.success
            assert "ResourceAllocationGate" in str(result.error)
            assert not called  # Task was throttled / never run

    def test_executor_throttles_under_critical_thermal(self):
        from turxz_scheduled_jobs.executor import TURXZJobExecutor
        from turxz_scheduled_jobs.job_manager import JobDefinition, TaskPowerCategory, TaskUrgency
        from turzx.power import SystemPowerManager

        pm = SystemPowerManager.get_instance()
        pm.thermal_manager.set_simulated_temperature(91.0)  # > 85°C

        executor = TURXZJobExecutor(max_workers=2)
        called = False

        def task():
            nonlocal called
            called = True

        job = JobDefinition(
            job_id="thermal_heavy_job",
            name="Thermal Heavy Task",
            handler=task,
            power_category=TaskPowerCategory.POWER_CRITICAL,
            urgency=TaskUrgency.MEDIUM,
        )

        try:
            result = executor.execute(job)
            assert not result.success
            assert "ResourceAllocationGate" in str(result.error)
            assert not called
        finally:
            pm.thermal_manager.set_simulated_temperature(None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])