"""
TURXZ Scheduler Configuration - Configuration management for the scheduler.
"""

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ._logger import get_logger

logger = get_logger(__name__)


@dataclass
class SchedulerSettings:
    """Core scheduler settings."""
    check_interval: float = 1.0
    max_concurrent_jobs: int = 10
    default_timeout: float = 300.0
    default_retry_count: int = 0
    default_retry_delay: float = 1.0
    enable_persistence: bool = True
    persistence_path: str = "/var/lib/turxz/scheduler_jobs.json"
    log_level: str = "INFO"


@dataclass
class ExecutorSettings:
    """Executor settings."""
    max_workers: int = 10
    default_mode: str = "thread"
    enable_metrics: bool = True


@dataclass
class JobDefaults:
    """Default job settings."""
    timeout: float = 300.0
    priority: str = "normal"
    retries: int = 0
    retry_delay: float = 1.0


@dataclass
class HealthCheckSettings:
    """Health check settings."""
    enabled: bool = True
    interval: float = 60.0
    timeout: float = 10.0
    failure_threshold: int = 3
    checks: list[str] = field(default_factory=lambda: [
        "scheduler_running",
        "executor_healthy",
        "job_queue_healthy",
        "disk_space",
        "memory_usage",
    ])


@dataclass
class TURXZSchedulerConfig:
    """Main configuration class for TURXZ Scheduler."""
    scheduler: SchedulerSettings = field(default_factory=SchedulerSettings)
    executor: ExecutorSettings = field(default_factory=ExecutorSettings)
    job_defaults: JobDefaults = field(default_factory=JobDefaults)
    health_check: HealthCheckSettings = field(default_factory=HealthCheckSettings)
    custom: dict[str, Any] = field(default_factory=dict)
    
    # Metadata
    version: str = "1.0.0"
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        from datetime import datetime
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()

    @classmethod
    def from_file(cls, path: str | Path) -> 'TURXZSchedulerConfig':
        """Load configuration from JSON file."""
        path = Path(path)
        if not path.exists():
            logger.warning("Config file not found, using defaults", path=str(path))
            return cls()
        
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            
            config = cls()
            if 'scheduler' in data:
                config.scheduler = SchedulerSettings(**data['scheduler'])
            if 'executor' in data:
                config.executor = ExecutorSettings(**data['executor'])
            if 'job_defaults' in data:
                config.job_defaults = JobDefaults(**data['job_defaults'])
            if 'health_check' in data:
                config.health_check = HealthCheckSettings(**data['health_check'])
            if 'custom' in data:
                config.custom = data['custom']
            if 'version' in data:
                config.version = data['version']
            if 'created_at' in data:
                config.created_at = data['created_at']
            if 'updated_at' in data:
                config.updated_at = data['updated_at']
            
            logger.info("Configuration loaded", path=str(path))
            return config
            
        except Exception as e:
            logger.error("Failed to load config", path=str(path), error=str(e))
            return cls()

    @classmethod
    def from_env(cls) -> 'TURXZSchedulerConfig':
        """Load configuration from environment variables."""
        config = cls()
        
        # Scheduler settings
        config.scheduler.check_interval = float(os.getenv('TURXZ_SCHEDULER_CHECK_INTERVAL', '1.0'))
        config.scheduler.max_concurrent_jobs = int(os.getenv('TURXZ_SCHEDULER_MAX_CONCURRENT', '10'))
        config.scheduler.default_timeout = float(os.getenv('TURXZ_SCHEDULER_DEFAULT_TIMEOUT', '300.0'))
        config.scheduler.enable_persistence = os.getenv('TURXZ_SCHEDULER_PERSISTENCE', 'true').lower() == 'true'
        config.scheduler.persistence_path = os.getenv('TURXZ_SCHEDULER_PERSISTENCE_PATH', '/var/lib/turxz/scheduler_jobs.json')
        config.scheduler.log_level = os.getenv('TURXZ_SCHEDULER_LOG_LEVEL', 'INFO')
        
        # Executor settings
        config.executor.max_workers = int(os.getenv('TURXZ_EXECUTOR_MAX_WORKERS', '10'))
        config.executor.default_mode = os.getenv('TURXZ_EXECUTOR_DEFAULT_MODE', 'thread')
        config.executor.enable_metrics = os.getenv('TURXZ_EXECUTOR_METRICS', 'true').lower() == 'true'
        
        # Job defaults
        config.job_defaults.timeout = float(os.getenv('TURXZ_JOB_TIMEOUT', '300.0'))
        config.job_defaults.priority = os.getenv('TURXZ_JOB_PRIORITY', 'normal')
        config.job_defaults.retries = int(os.getenv('TURXZ_JOB_RETRIES', '0'))
        config.job_defaults.retry_delay = float(os.getenv('TURXZ_JOB_RETRY_DELAY', '1.0'))
        
        # Health check
        config.health_check.enabled = os.getenv('TURXZ_HEALTH_ENABLED', 'true').lower() == 'true'
        config.health_check.interval = float(os.getenv('TURXZ_HEALTH_INTERVAL', '60.0'))
        config.health_check.timeout = float(os.getenv('TURXZ_HEALTH_TIMEOUT', '10.0'))
        config.health_check.failure_threshold = int(os.getenv('TURXZ_HEALTH_FAILURE_THRESHOLD', '3'))
        
        logger.info("Configuration loaded from environment")
        return config

    def to_file(self, path: str | Path) -> bool:
        """Save configuration to JSON file."""
        path = Path(path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            
            self.updated_at = datetime.now().isoformat()
            
            data = {
                'version': self.version,
                'created_at': self.created_at,
                'updated_at': self.updated_at,
                'scheduler': asdict(self.scheduler),
                'executor': asdict(self.executor),
                'job_defaults': asdict(self.job_defaults),
                'health_check': asdict(self.health_check),
                'custom': self.custom,
            }
            
            with open(path, 'w') as f:
                json.dump(data, f, indent=2)
            
            logger.info("Configuration saved", path=str(path))
            return True
            
        except Exception as e:
            logger.error("Failed to save config", path=str(path), error=str(e))
            return False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            'version': self.version,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'scheduler': asdict(self.scheduler),
            'executor': asdict(self.executor),
            'job_defaults': asdict(self.job_defaults),
            'health_check': asdict(self.health_check),
            'custom': self.custom,
        }

    def merge(self, other: 'TURXZSchedulerConfig') -> 'TURXZSchedulerConfig':
        """Merge with another config (other takes precedence)."""
        merged = TURXZSchedulerConfig()
        
        # Merge scheduler settings
        for field_name in ['check_interval', 'max_concurrent_jobs', 'default_timeout',
                          'default_retry_count', 'default_retry_delay', 'enable_persistence',
                          'persistence_path', 'log_level']:
            setattr(merged.scheduler, field_name, 
                   getattr(other.scheduler, field_name, getattr(self.scheduler, field_name)))
        
        # Merge executor settings
        for field_name in ['max_workers', 'default_mode', 'enable_metrics']:
            setattr(merged.executor, field_name,
                   getattr(other.executor, field_name, getattr(self.executor, field_name)))
        
        # Merge job defaults
        for field_name in ['timeout', 'priority', 'retries', 'retry_delay']:
            setattr(merged.job_defaults, field_name,
                   getattr(other.job_defaults, field_name, getattr(self.job_defaults, field_name)))
        
        # Merge health check
        for field_name in ['enabled', 'interval', 'timeout', 'failure_threshold', 'checks']:
            setattr(merged.health_check, field_name,
                   getattr(other.health_check, field_name, getattr(self.health_check, field_name)))
        
        # Merge custom
        merged.custom = {**self.custom, **other.custom}
        
        return merged


def load_config(
    config_path: str | Path | None = None,
    use_env: bool = True,
) -> TURXZSchedulerConfig:
    """
    Load configuration with precedence:
    1. Default values
    2. Config file (if provided)
    3. Environment variables (if use_env=True)
    """
    config = TURXZSchedulerConfig()
    
    if config_path:
        file_config = TURXZSchedulerConfig.from_file(config_path)
        config = config.merge(file_config)
    
    if use_env:
        env_config = TURXZSchedulerConfig.from_env()
        config = config.merge(env_config)
    
    return config


# Default config paths
DEFAULT_CONFIG_PATHS = [
    Path("/etc/turxz/scheduler.json"),
    Path("/usr/local/etc/turxz/scheduler.json"),
    Path.home() / ".config" / "turxz" / "scheduler.json",
    Path.cwd() / "config" / "scheduler.json",
]


def find_and_load_config() -> TURXZSchedulerConfig:
    """Find and load config from default locations."""
    for path in DEFAULT_CONFIG_PATHS:
        if path.exists():
            return load_config(path)
    
    # Fall back to environment or defaults
    return load_config(use_env=True)