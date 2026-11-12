"""
Configuration module for DeepSeek Web Bridge Backend.
"""

import os
import yaml
from typing import Dict, List, Optional, Any
from datetime import datetime

class DatabaseConfig:
    def __init__(self):
        self.url = os.getenv("DATABASE_URL", "sqlite:///./data.db")

class RedisConfig:
    def __init__(self):
        self.url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.ttl = int(os.getenv("REDIS_TTL", "3600"))
        self.max_connections = int(os.getenv("REDIS_MAX_CONNECTIONS", "20"))

class SecurityConfig:
    def __init__(self):
        self.secret_key = os.getenv("SECRET_KEY", "change-this-secret")
        self.turzx_api_key = os.getenv("TURZX_API_KEY", "not-set")
        self.token_expiry_hours = int(os.getenv("TOKEN_EXPIRY_HOURS", "24"))
        self.allowed_origins = self._get_allowed_origins()

    def _get_allowed_origins(self) -> List[str]:
        origins_str = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:8000")
        return [origin.strip() for origin in origins_str.split(",")]

class QuotaConfig:
    def __init__(self):
        self.default_limit = int(os.getenv("QUOTA_LIMIT", "1000"))
        self.warning_threshold = float(os.getenv("QUOTA_WARNING_THRESHOLD", "0.8"))
        self.burst_limit = float(os.getenv("QUOTA_BURST_LIMIT", "1.5"))
        self.reset_interval_hours = int(os.getenv("QUOTA_RESET_INTERVAL", "24"))

class LoggingConfig:
    def __init__(self):
        self.level = os.getenv("LOG_LEVEL", "INFO")
        self.format = os.getenv("LOG_FORMAT", "json")
        self.enable_prometheus = os.getenv("LOG_ENABLE_PROMETHEUS", "true").lower() == "true"
        self.service_name = os.getenv("LOG_SERVICE_NAME", "deepseek-web-bridge-backend")

class MonitoringConfig:
    def __init__(self):
        self.enable_health_checks = os.getenv("MONITORING_ENABLE_HEALTH_CHECKS", "true").lower() == "true"
        self.health_check_interval = int(os.getenv("HEALTH_CHECK_INTERVAL", "30"))
        self.metrics_port = int(os.getenv("METRICS_PORT", "8001"))
        self.enable_cors = os.getenv("MONITORING_ENABLE_CORS", "true").lower() == "true"

class ServerConfig:
    def __init__(self):
        self.host = os.getenv("SERVER_HOST", "0.0.0.0")
        self.port = int(os.getenv("SERVER_PORT", "8000"))
        self.workers = int(os.getenv("SERVER_WORKERS", "1"))
        self.reload = os.getenv("SERVER_RELOAD", "true").lower() == "true"

class AppSettings:
    def __init__(self):
        self.database = DatabaseConfig()
        self.redis = RedisConfig()
        self.security = SecurityConfig()
        self.quota = QuotaConfig()
        self.logging = LoggingConfig()
        self.monitoring = MonitoringConfig()
        self.server = ServerConfig()

    def get_config_dict(self) -> Dict[str, Any]:
        """Get configuration as a dictionary."""
        config = {}
        config["database"] = {"url": self.database.url}
        config["redis"] = {
            "url": self.redis.url,
            "ttl": self.redis.ttl,
            "max_connections": self.redis.max_connections,
        }
        config["security"] = {
            "secret_key": self.security.secret_key,
            "turzx_api_key": self.security.turzx_api_key,
            "token_expiry_hours": self.security.token_expiry_hours,
            "allowed_origins": self.security.allowed_origins,
        }
        config["quota"] = {
            "default_limit": self.quota.default_limit,
            "warning_threshold": self.quota.warning_threshold,
            "burst_limit": self.quota.burst_limit,
            "reset_interval_hours": self.quota.reset_interval_hours,
        }
        config["logging"] = {
            "level": self.logging.level,
            "format": self.logging.format,
            "enable_prometheus": self.logging.enable_prometheus,
            "service_name": self.logging.service_name,
        }
        config["monitoring"] = {
            "enable_health_checks": self.monitoring.enable_health_checks,
            "health_check_interval": self.monitoring.health_check_interval,
            "metrics_port": self.monitoring.metrics_port,
            "enable_cors": self.monitoring.enable_cors,
        }
        config["server"] = {
            "host": self.server.host,
            "port": self.server.port,
            "workers": self.server.workers,
            "reload": self.server.reload,
        }
        return config

    def validate_config(self) -> List[str]:
        """Validate configuration and return list of errors."""
        errors = []

        if self.security.secret_key == "change-this-secret":
            errors.append("Security secret key should be changed from default in production")

        if self.security.turzx_api_key == "not-set":
            errors.append("TURZX API key should be configured")

        if self.database.url == "sqlite:///./data.db":
            errors.append("Database should be configured for production use")

        return errors

    @classmethod
    def load_from_file(cls, config_file: str) -> 'AppSettings':
        """Load configuration from YAML file."""
        with open(config_file, 'r') as f:
            config_data = yaml.safe_load(f)

        settings = cls()

        if "database" in config_data:
            settings.database.url = config_data["database"]["url"]
        if "redis" in config_data:
            settings.redis.url = config_data["redis"]["url"]
            settings.redis.ttl = config_data["redis"]["ttl"]
            settings.redis.max_connections = config_data["redis"]["max_connections"]
        if "security" in config_data:
            settings.security.secret_key = config_data["security"]["secret_key"]
            settings.security.turzx_api_key = config_data["security"]["turzx_api_key"]
            settings.security.token_expiry_hours = config_data["security"]["token_expiry_hours"]
            settings.security.allowed_origins = config_data["security"]["allowed_origins"]
        if "quota" in config_data:
            settings.quota.default_limit = config_data["quota"]["default_limit"]
            settings.quota.warning_threshold = config_data["quota"]["warning_threshold"]
            settings.quota.burst_limit = config_data["quota"]["burst_limit"]
            settings.quota.reset_interval_hours = config_data["quota"]["reset_interval_hours"]
        if "logging" in config_data:
            settings.logging.level = config_data["logging"]["level"]
            settings.logging.format = config_data["logging"]["format"]
            settings.logging.enable_prometheus = config_data["logging"]["enable_prometheus"]
            settings.logging.service_name = config_data["logging"]["service_name"]
        if "monitoring" in config_data:
            settings.monitoring.enable_health_checks = config_data["monitoring"]["enable_health_checks"]
            settings.monitoring.health_check_interval = config_data["monitoring"]["health_check_interval"]
            settings.monitoring.metrics_port = config_data["monitoring"]["metrics_port"]
            settings.monitoring.enable_cors = config_data["monitoring"]["enable_cors"]
        if "server" in config_data:
            settings.server.host = config_data["server"]["host"]
            settings.server.port = config_data["server"]["port"]
            settings.server.workers = config_data["server"]["workers"]
            settings.server.reload = config_data["server"]["reload"]

        return settings

def get_settings() -> AppSettings:
    """Get application settings."""
    return AppSettings()
