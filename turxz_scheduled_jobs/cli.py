#!/usr/bin/env python3
"""
TURXZ Scheduler Entry Point

Command-line interface for running the TURXZ native scheduler.
"""

import argparse
import importlib
import logging
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import (
    TURXZJobExecutor,
    TURXZJobManager,
    TURXZScheduler,
    TURXZSchedulerConfig,
    TURXZSchedulerHealthCheck,
)
from ._logger import get_logger
from .config import load_config
from .job_manager import JobDefinition, JobPriority
from .scheduler import ScheduleType

# Ensure workspace root is in sys.path
_ROOT_DIR = str(Path(__file__).resolve().parent.parent)
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = get_logger(__name__)


class TURXZSchedulerCLI:
    """CLI for TURXZ Scheduler."""
    
    def __init__(self):
        self.scheduler: TURXZScheduler | None = None
        self.job_manager: TURXZJobManager | None = None
        self.executor: TURXZJobExecutor | None = None
        self.health_check: TURXZSchedulerHealthCheck | None = None
        self.config: TURXZSchedulerConfig | None = None
        self._running = False
    
    def load_config(self, config_path: str | None = None) -> TURXZSchedulerConfig:
        """Load configuration."""
        if config_path:
            self.config = load_config(config_path)
        else:
            self.config = load_config()
        return self.config
    
    def setup_components(self) -> None:
        """Set up scheduler components."""
        if not self.config:
            self.load_config()
        
        # Create job manager
        self.job_manager = TURXZJobManager()
        
        # Create executor
        self.executor = TURXZJobExecutor(
            max_workers=self.config.executor.max_workers,
            default_timeout=self.config.scheduler.default_timeout,
        )
        self.executor.start()
        
        # Create scheduler
        self.scheduler = TURXZScheduler(
            job_manager=self.job_manager,
            executor=self.executor,
            check_interval=self.config.scheduler.check_interval,
            max_concurrent_jobs=self.config.scheduler.max_concurrent_jobs,
        )
        
        # Create health check
        health_settings = self.config.health_check
        self.health_check = TURXZSchedulerHealthCheck(
            scheduler=self.scheduler,
            executor=self.executor,
            settings=health_settings,
        )
        
        # Register default jobs
        self._register_default_jobs()
        
        # Add shutdown callback
        self.scheduler.add_shutdown_callback(self._on_shutdown)
    
    def _register_default_jobs(self) -> None:
        """Register default TURXZ scheduled jobs."""
        # Native Quota Sync job - runs every 5 minutes
        self.scheduler.add_job(
            name="turzx_quota_sync",
            schedule_type=ScheduleType.INTERVAL,
            schedule_value=300.0,  # 5 minutes
            job_definition=JobDefinition(
                job_id="turzx_quota_sync",
                name="TURZX Quota Sync (Hermes)",
                handler=self._check_agent_quotas,
                timeout=60.0,
                priority=JobPriority.HIGH,
                tags=["quota", "hermes", "sync"],
            ),
            metadata={"description": "Sync agent quotas from Hermes scheduled jobs and state"},
        )

        # Quota check job - runs every 5 minutes
        self.scheduler.add_job(
            name="agent_quota_check",
            schedule_type=ScheduleType.INTERVAL,
            schedule_value=300.0,  # 5 minutes
            job_definition=JobDefinition(
                job_id="agent_quota_check",
                name="Agent Quota Check",
                handler=self._check_agent_quotas,
                timeout=60.0,
                priority=JobPriority.HIGH,
                tags=["quota", "monitoring"],
            ),
            metadata={"description": "Check agent quota status"},
        )

        # TURZX Display update job - runs periodically
        self.scheduler.add_job(
            name="turzx_display_update",
            schedule_type=ScheduleType.INTERVAL,
            schedule_value=30.0,  # 30 seconds
            job_definition=JobDefinition(
                job_id="turzx_display_update",
                name="TURZX Display Quota Update",
                handler=self._sync_and_display_quotas,
                timeout=30.0,
                priority=JobPriority.NORMAL,
                tags=["display", "quota", "hardware"],
            ),
            metadata={"description": "Update TURZX physical display with agent quotas"},
        )
        
        # Display health check - runs every 2 minutes
        self.scheduler.add_job(
            name="display_health_check",
            schedule_type=ScheduleType.INTERVAL,
            schedule_value=120.0,  # 2 minutes
            job_definition=JobDefinition(
                job_id="display_health_check",
                name="Display Health Check",
                handler=self._check_display_health,
                timeout=30.0,
                priority=JobPriority.NORMAL,
                tags=["display", "health"],
            ),
            metadata={"description": "Check TURZX display health"},
        )
        
        # Metrics collection - runs every minute
        self.scheduler.add_job(
            name="metrics_collection",
            schedule_type=ScheduleType.INTERVAL,
            schedule_value=60.0,  # 1 minute
            job_definition=JobDefinition(
                job_id="metrics_collection",
                name="Metrics Collection",
                handler=self._collect_metrics,
                timeout=30.0,
                priority=JobPriority.LOW,
                tags=["metrics", "monitoring"],
            ),
            metadata={"description": "Collect system metrics"},
        )
        
        # Cleanup old execution history - runs daily at startup
        self.scheduler.add_job(
            name="cleanup_history",
            schedule_type=ScheduleType.STARTUP,
            schedule_value=0,
            job_definition=JobDefinition(
                job_id="cleanup_history",
                name="Cleanup Execution History",
                handler=self._cleanup_history,
                timeout=60.0,
                priority=JobPriority.LOW,
                tags=["maintenance", "cleanup"],
            ),
            metadata={"description": "Clean up old execution history"},
        )
        
        logger.info("Default TURXZ jobs registered")
    
    def _check_agent_quotas(self) -> dict[str, Any]:
        """Check agent quotas by syncing from Hermes agent and scheduled jobs data."""
        try:
            from agent_quota_manager import AgentQuotaManager
            quota_manager = AgentQuotaManager()
            sync_res = quota_manager.sync_agent_quotas()
            if sync_res.get("status") != "completed":
                raise RuntimeError(f"Agent quota sync failed: {sync_res.get('error', 'Unknown sync error')}")
            return {"status": "ok", "quotas": sync_res.get("synced_agents", {})}
        except Exception as e:
            logger.error("Quota check failed", error=str(e))
            raise

    def _sync_and_display_quotas(self) -> dict[str, Any]:
        """Sync agent quotas from Hermes data and update TURZX physical display."""
        try:
            from agent_quota_manager import AgentQuotaManager
            from turzx_display_service import TURZXDisplayService
            quota_manager = AgentQuotaManager()
            sync_res = quota_manager.sync_agent_quotas()
            with TURZXDisplayService() as display_svc:
                render_res = display_svc.update_display_protocol(
                    agent_data=sync_res.get("synced_agents", {}),
                    hermes_jobs=sync_res.get("hermes_jobs", []),
                )
                if not render_res.get("success", False):
                    raise RuntimeError(f"TURZX display update failed: {render_res.get('error', 'Unknown display error')}")
                return {"status": "ok", "sync": sync_res, "render": render_res}
        except Exception as e:
            logger.error("Sync and display quotas failed", error=str(e))
            raise
    
    def _check_display_health(self) -> dict[str, Any]:
        """Check display health."""
        try:
            from turzx_display_service import TURZXDisplayService
            display_service = TURZXDisplayService()
            health = display_service.health_check()
            return {"status": "ok", "health": health}
        except Exception as e:
            logger.error("Display health check failed", error=str(e))
            return {"status": "error", "error": str(e)}
    
    def _collect_metrics(self) -> dict[str, Any]:
        """Collect system metrics."""
        try:
            import psutil
            metrics = {
                "cpu_percent": psutil.cpu_percent(interval=0.1),
                "memory_percent": psutil.virtual_memory().percent,
                "disk_percent": psutil.disk_usage('/').percent,
            }
            return {"status": "ok", "metrics": metrics}
        except ImportError:
            return {"status": "ok", "metrics": {"note": "psutil not available"}}
        except Exception as e:
            logger.error("Metrics collection failed", error=str(e))
            return {"status": "error", "error": str(e)}
    
    def _cleanup_history(self) -> dict[str, Any]:
        """Clean up old execution history."""
        if self.job_manager:
            removed = self.job_manager.clear_history()
            return {"status": "ok", "removed_entries": removed}
        return {"status": "skipped", "reason": "job_manager not available"}
    
    def _on_shutdown(self) -> None:
        """Shutdown callback."""
        logger.info("Scheduler shutting down")
        if self.health_check:
            self.health_check.stop()
        if self.executor:
            self.executor.shutdown(wait=True)
    
    def start(self) -> None:
        """Start the scheduler."""
        if self._running:
            logger.warning("Scheduler already running")
            return
        
        self.setup_components()
        self.scheduler.start()
        self.health_check.start()
        self._running = True
        
        logger.info("TURXZ Scheduler started")
        
        # Set up signal handlers
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
    
    def stop(self) -> None:
        """Stop the scheduler."""
        if not self._running:
            return
        
        self._running = False
        if self.scheduler:
            self.scheduler.stop()
        
        logger.info("TURXZ Scheduler stopped")
    
    def _signal_handler(self, signum, frame) -> None:
        """Handle shutdown signals."""
        logger.info("Received signal", signal=signum)
        self.stop()
        sys.exit(0)
    
    def run_forever(self) -> None:
        """Run the scheduler until interrupted."""
        self.start()
        try:
            while self._running:
                signal.pause()
        except KeyboardInterrupt:
            self.stop()
    
    def status(self) -> dict[str, Any]:
        """Get scheduler status."""
        if not self.scheduler:
            return {"error": "Scheduler not initialized"}
        
        return {
            "scheduler": self.scheduler.get_status(),
            "health": self.health_check.get_status() if self.health_check else {},
            "executor": self.executor.get_status() if self.executor else {},
        }
    
    def add_job(self, args) -> str:
        """Add a job from CLI arguments."""
        if not self.scheduler:
            self.setup_components()
        
        job_id = self.scheduler.add_job(
            name=args.name,
            schedule_type=ScheduleType(args.type),
            schedule_value=self._parse_schedule_value(args.type, args.value),
            job_definition=JobDefinition(
                job_id=args.id or f"cli_job_{args.name}",
                name=args.name,
                handler=self._create_handler(args.handler),
                timeout=args.timeout,
                priority=JobPriority(args.priority),
            ),
            enabled=not args.disabled,
            max_runs=args.max_runs,
        )
        
        logger.info("Job added via CLI", job_id=job_id)
        return job_id
    
    def _parse_schedule_value(self, schedule_type: str, value: str) -> str | int | float:
        """Parse and validate schedule value from string (Fail-Closed)."""
        if schedule_type == "interval":
            try:
                parsed = float(value)
                if parsed <= 0:
                    raise ValueError(f"Interval value must be positive, got {value}")
                return parsed
            except ValueError as e:
                raise ValueError(f"Invalid interval value '{value}': {e}") from e
        elif schedule_type == "cron":
            parts = value.strip().split()
            if len(parts) != 5:
                raise ValueError(
                    f"Invalid cron expression '{value}': expected 5 space-separated fields (minute hour day month weekday)"
                )
            return value.strip()
        elif schedule_type == "once":
            try:
                return float(value)
            except ValueError:
                return value
        return value
    
    def _create_handler(self, handler_name: str) -> Callable[..., Any]:
        """Create a job handler from name or module:callable reference."""
        handlers = {
            "quota_check": self._check_agent_quotas,
            "quota_sync": self._check_agent_quotas,
            "display_check": self._check_display_health,
            "display_update": self._sync_and_display_quotas,
            "display_quota": self._sync_and_display_quotas,
            "metrics": self._collect_metrics,
            "cleanup": self._cleanup_history,
        }
        if handler_name in handlers:
            return handlers[handler_name]

        # Support module:function or module.function syntax as documented in CLI_USAGE.md
        target = handler_name.replace(":", ".")
        if "." in target:
            mod_part, func_part = target.rsplit(".", 1)
            try:
                mod = importlib.import_module(mod_part)
                func = getattr(mod, func_part)
                if callable(func):
                    return func
            except Exception as e:
                logger.error("Failed to dynamically import handler", target=handler_name, error=str(e))
                raise ValueError(f"Failed to resolve handler '{handler_name}': {e}") from e

        raise ValueError(
            f"Unknown or unresolvable handler '{handler_name}'. Available shortcuts: {list(handlers.keys())}"
        )


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="TURXZ Native Scheduler")
    parser.add_argument(
        "--config", "-c",
        help="Path to configuration file",
        default=None
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Log level"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # Start command
    start_parser = subparsers.add_parser("start", help="Start the scheduler")
    start_parser.add_argument("--foreground", "-f", action="store_true", help="Run in foreground")
    
    # Status command
    subparsers.add_parser("status", help="Show scheduler status")
    
    # Add job command
    add_parser = subparsers.add_parser("add-job", help="Add a scheduled job")
    add_parser.add_argument("--name", required=True, help="Job name")
    add_parser.add_argument("--type", choices=["interval", "cron", "once", "startup", "shutdown"], required=True)
    add_parser.add_argument("--value", required=True, help="Schedule value")
    add_parser.add_argument("--id", help="Job ID (auto-generated if not provided)")
    add_parser.add_argument("--handler", required=True, help="Handler function name")
    add_parser.add_argument("--timeout", type=float, default=300.0, help="Job timeout")
    add_parser.add_argument("--priority", choices=["low", "normal", "high", "critical"], default="normal")
    add_parser.add_argument("--max-runs", type=int, help="Maximum number of runs")
    add_parser.add_argument("--disabled", action="store_true", help="Add job as disabled")
    
    # Remove job command
    remove_parser = subparsers.add_parser("remove-job", help="Remove a scheduled job")
    remove_parser.add_argument("job_id", nargs="?", default=None, help="Job ID to remove")
    remove_parser.add_argument("--id", dest="id_arg", default=None, help="Job ID to remove")
    
    # Run job now command
    run_parser = subparsers.add_parser("run-job", help="Run a job immediately")
    run_parser.add_argument("job_id", nargs="?", default=None, help="Job ID to run")
    run_parser.add_argument("--id", dest="id_arg", default=None, help="Job ID to run")
    
    args = parser.parse_args()
    
    # Set log level
    logging.getLogger().setLevel(getattr(logging, args.log_level))
    
    cli = TURXZSchedulerCLI()
    
    if args.config:
        cli.load_config(args.config)
    
    if args.command == "start":
        if args.foreground:
            cli.run_forever()
        else:
            cli.start()
            logger.info("Scheduler started in background")
    elif args.command == "status":
        cli.setup_components()
        status = cli.status()
        import json
        print(json.dumps(status, indent=2, default=str))
    elif args.command == "add-job":
        cli.setup_components()
        try:
            job_id = cli.add_job(args)
            print(f"Job added: {job_id}")
        except Exception as e:
            logger.error("Failed to add job", error=str(e))
            print(f"Error adding job: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.command == "remove-job":
        target_id = args.id_arg or args.job_id
        if not target_id:
            print("Error: job_id is required (specify job_id or --id <ID>)", file=sys.stderr)
            sys.exit(2)
        cli.setup_components()
        if cli.scheduler and cli.scheduler.remove_job(target_id):
            print(f"Job removed: {target_id}")
        else:
            print(f"Job not found: {target_id}", file=sys.stderr)
            sys.exit(1)
    elif args.command == "run-job":
        target_id = args.id_arg or args.job_id
        if not target_id:
            print("Error: job_id is required (specify job_id or --id <ID>)", file=sys.stderr)
            sys.exit(2)
        cli.setup_components()
        if cli.scheduler:
            result = cli.scheduler.run_job_now(target_id)
            if result:
                import json
                print(json.dumps(result.to_dict(), indent=2, default=str))
            else:
                print(f"Job not found: {target_id}", file=sys.stderr)
                sys.exit(1)
        else:
            print("Scheduler not initialized", file=sys.stderr)
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()