#!/usr/bin/env python3
"""TURZX display service for DeepSeek Web Bridge.

Integrates with TURXZSerialDriver using the official Rev A (UsbMonitor) protocol.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import structlog
from PIL import Image

from turzx import (
    TURZXDriver,
    TURXZSerialDriver,
    TURZXProtocolError,
    TURXZSerialProtocolError,
    image_to_rgb565,
)

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

logger = structlog.get_logger(__name__)


class DisplayStatus(str, Enum):
    IDLE = "idle"
    PROCESSING = "processing"
    READY = "ready"
    ERROR = "error"
    MAINTENANCE = "maintenance"


class DisplayMode(str, Enum):
    STANDARD = "standard"
    TURZX = "turzx"
    ENHANCED = "enhanced"
    DEBUG = "debug"


class ConnectionStatus(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    PENDING = "pending"


@dataclass
class DisplayMetrics:
    screen_resolution: dict[str, int] = None
    refresh_rate: float = 0.0
    cpu_usage: float = 0.0
    memory_usage: float = 0.0
    active_sessions: int = 0
    total_sessions: int = 0
    last_update: datetime = None


@dataclass
class TURZXConfig:
    enabled: bool = False
    resolution: dict[str, int] = None
    refresh_rate: float = 60.0
    color_depth: int = 8
    gamma: float = 2.2
    hdr_enabled: bool = False
    adaptive_refresh: bool = False
    custom_settings: dict[str, Any] = None


@dataclass
class DisplaySession:
    session_id: str
    agent_id: str
    status: DisplayStatus = DisplayStatus.IDLE
    mode: DisplayMode = DisplayMode.STANDARD
    config: TURZXConfig = None
    metrics: DisplayMetrics = None
    start_time: datetime = None
    last_activity: datetime = None
    connection_status: ConnectionStatus = ConnectionStatus.DISCONNECTED
    error_message: str | None = None
    metadata: dict[str, Any] = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.config:
            data["config"] = asdict(self.config)
        if self.metrics:
            data["metrics"] = asdict(self.metrics)
        if self.start_time:
            data["start_time"] = self.start_time.isoformat()
        if self.last_activity:
            data["last_activity"] = self.last_activity.isoformat()
        return data


@dataclass
class DisplayEvent:
    event_type: str
    session_id: str
    timestamp: datetime = None
    data: dict[str, Any] = None
    priority: str = "normal"


class TURZXDisplayService:
    def __init__(
        self, 
        redis_client=None, 
        cache_ttl: int = 300, 
        port: str = "AUTO", 
        baudrate: int = 115200,
        display_width: int = 320,
        display_height: int = 480,
    ):
        self.redis = redis_client
        self.logger = structlog.get_logger(__name__)
        self.cache_ttl = cache_ttl
        self._sessions: dict[str, DisplaySession] = {}
        self._events: list[DisplayEvent] = []
        self._monitoring_enabled = True
        self._serial_driver = TURXZSerialDriver(
            port=port, 
            baudrate=baudrate,
            display_width=display_width,
            display_height=display_height,
        )
        self._hardware_initialized = False

    def create_display_session(self, session_id: str, agent_id: str, 
                              initial_config: TURZXConfig | None = None) -> DisplaySession:
        """Create a new display session."""
        config = initial_config or TURZXConfig()
        
        session = DisplaySession(
            session_id=session_id,
            agent_id=agent_id,
            config=config,
            start_time=datetime.now(),
            last_activity=datetime.now(),
            metrics=DisplayMetrics(last_update=datetime.now()),
        )
        
        self._sessions[session_id] = session
        self._add_event("session_created", session_id, {
            "agent_id": agent_id,
            "config": asdict(config),
        })
        
        self.logger.info("Created display session", 
                        session_id=session_id, 
                        agent_id=agent_id,
                        config=config)
        
        return session

    def get_session(self, session_id: str) -> DisplaySession | None:
        """Get display session by ID."""
        return self._sessions.get(session_id)

    def update_session_status(self, session_id: str, status: DisplayStatus, 
                            error_message: str | None = None) -> bool:
        """Update session status."""
        session = self.get_session(session_id)
        if not session:
            self.logger.warning("Attempted to update non-existent session", 
                              session_id=session_id)
            return False

        session.status = status
        session.error_message = error_message
        session.last_activity = datetime.now()
        
        if status == DisplayStatus.ERROR and error_message:
            self._add_event("session_error", session_id, {
                "error": error_message,
            })
        
        self.logger.info("Updated session status", 
                        session_id=session_id, 
                        status=status.value,
                        error=error_message)
        
        return True

    def update_session_mode(self, session_id: str, mode: DisplayMode) -> bool:
        """Update session display mode."""
        session = self.get_session(session_id)
        if not session:
            return False

        old_mode = session.mode
        session.mode = mode
        session.last_activity = datetime.now()
        
        self._add_event("mode_changed", session_id, {
            "old_mode": old_mode.value,
            "new_mode": mode.value,
        })
        
        self.logger.info("Updated session mode", 
                        session_id=session_id,
                        old_mode=old_mode.value,
                        new_mode=mode.value)
        
        return True

    def update_session_config(self, session_id: str, config_updates: dict[str, Any]) -> bool:
        """Update session configuration."""
        session = self.get_session(session_id)
        if not session:
            return False

        if not session.config:
            session.config = TURZXConfig()
        
        for key, value in config_updates.items():
            if hasattr(session.config, key):
                setattr(session.config, key, value)
            elif key == "custom_settings" and isinstance(value, dict):
                if not session.config.custom_settings:
                    session.config.custom_settings = {}
                session.config.custom_settings.update(value)
        
        session.last_activity = datetime.now()
        
        self._add_event("config_updated", session_id, config_updates)
        
        self.logger.info("Updated session config", 
                        session_id=session_id,
                        updates=config_updates)
        
        return True

    def update_session_metrics(self, session_id: str, metrics: dict[str, Any]) -> bool:
        """Update session metrics."""
        session = self.get_session(session_id)
        if not session:
            return False

        if not session.metrics:
            session.metrics = DisplayMetrics()
        
        for key, value in metrics.items():
            if hasattr(session.metrics, key):
                setattr(session.metrics, key, value)
        
        session.metrics.last_update = datetime.now()
        session.last_activity = datetime.now()
        
        self.logger.info("Updated session metrics", 
                        session_id=session_id,
                        metrics=metrics)
        
        return True

    def update_connection_status(self, session_id: str, status: ConnectionStatus) -> bool:
        """Update connection status."""
        session = self.get_session(session_id)
        if not session:
            return False

        old_status = session.connection_status
        session.connection_status = status
        session.last_activity = datetime.now()
        
        if status == ConnectionStatus.ERROR:
            self._add_event("connection_error", session_id, {
                "old_status": old_status.value,
                "new_status": status.value,
            })
        
        self.logger.info("Updated connection status", 
                        session_id=session_id,
                        old_status=old_status.value,
                        new_status=status.value)
        
        return True

    def close_session(self, session_id: str) -> bool:
        """Close display session."""
        session = self.get_session(session_id)
        if not session:
            return False

        old_status = session.status
        session.status = DisplayStatus.IDLE
        session.connection_status = ConnectionStatus.DISCONNECTED
        session.last_activity = datetime.now()
        
        self._add_event("session_closed", session_id, {
            "old_status": old_status.value,
        })
        
        del self._sessions[session_id]
        
        self.logger.info("Closed display session", 
                        session_id=session_id,
                        old_status=old_status.value)
        
        return True

    def get_session_status(self, session_id: str) -> dict[str, Any] | None:
        """Get session status information."""
        session = self.get_session(session_id)
        if not session:
            return None

        status_info = {
            "session_id": session.session_id,
            "agent_id": session.agent_id,
            "status": session.status.value,
            "mode": session.mode.value,
            "connection_status": session.connection_status.value,
            "start_time": session.start_time.isoformat() if session.start_time else None,
            "last_activity": session.last_activity.isoformat() if session.last_activity else None,
            "error_message": session.error_message,
            "metadata": session.metadata,
        }
        
        if session.config:
            status_info["config"] = asdict(session.config)
        
        if session.metrics:
            status_info["metrics"] = asdict(session.metrics)
        
        return status_info

    def get_all_sessions(self) -> list[dict[str, Any]]:
        """Get all active display sessions."""
        sessions = []
        for session in self._sessions.values():
            sessions.append(self.get_session_status(session.session_id))
        
        return sessions

    def _add_event(self, event_type: str, session_id: str, 
                   data: dict[str, Any] | None = None) -> DisplayEvent:
        """Add event to session history."""
        event = DisplayEvent(
            event_type=event_type,
            session_id=session_id,
            timestamp=datetime.now(),
            data=data or {},
        )
        
        self._events.append(event)
        
        if len(self._events) > 1000:
            self._events = self._events[-1000:]
        
        self.logger.info("Added event", 
                        event_type=event_type,
                        session_id=session_id,
                        data=data)
        
        return event

    def get_session_events(self, session_id: str, limit: int = 50, 
                          event_type: str | None = None) -> list[dict[str, Any]]:
        """Get events for a session."""
        events = []
        for event in reversed(self._events):
            if event.session_id == session_id:
                if not event_type or event.event_type == event_type:
                    events.append({
                        "event_type": event.event_type,
                        "session_id": event.session_id,
                        "timestamp": event.timestamp.isoformat() if event.timestamp else None,
                        "data": event.data,
                        "priority": event.priority,
                    })
        
        return events[-limit:]

    def get_display_analytics(self, hours: int = 24) -> dict[str, Any]:
        """Get display analytics for time period."""
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        total_sessions = len(self._sessions)
        active_sessions = sum(1 for s in self._sessions.values() 
                             if s.status == DisplayStatus.PROCESSING)
        
        mode_counts = {}
        status_counts = {}
        
        for session in self._sessions.values():
            mode_counts[session.mode.value] = mode_counts.get(session.mode.value, 0) + 1
            status_counts[session.status.value] = status_counts.get(session.status.value, 0) + 1
        
        total_refresh_rate = 0.0
        total_cpu_usage = 0.0
        total_memory_usage = 0.0
        metrics_count = 0
        
        for session in self._sessions.values():
            if session.metrics:
                total_refresh_rate += session.metrics.refresh_rate
                total_cpu_usage += session.metrics.cpu_usage
                total_memory_usage += session.metrics.memory_usage
                metrics_count += 1
        
        analytics = {
            "time_period_hours": hours,
            "total_sessions": total_sessions,
            "active_sessions": active_sessions,
            "mode_distribution": mode_counts,
            "status_distribution": status_counts,
            "average_refresh_rate": (total_refresh_rate / metrics_count) if metrics_count > 0 else 0.0,
            "average_cpu_usage": (total_cpu_usage / metrics_count) if metrics_count > 0 else 0.0,
            "average_memory_usage": (total_memory_usage / metrics_count) if metrics_count > 0 else 0.0,
            "events_count": len(self._events),
        }
        
        return analytics

    def get_system_status(self) -> dict[str, Any]:
        """Get overall system status."""
        total_sessions = len(self._sessions)
        active_sessions = sum(1 for s in self._sessions.values() 
                             if s.status == DisplayStatus.PROCESSING)
        
        error_sessions = sum(1 for s in self._sessions.values() 
                            if s.status == DisplayStatus.ERROR)
        
        connected_sessions = sum(1 for s in self._sessions.values() 
                                if s.connection_status == ConnectionStatus.CONNECTED)
        
        health_score = 100.0
        if total_sessions > 0:
            health_score -= (error_sessions / total_sessions) * 50
            health_score -= ((total_sessions - connected_sessions) / total_sessions) * 25
        
        return {
            "system_status": "healthy" if health_score >= 90 else "degraded" if health_score >= 70 else "critical",
            "health_score": health_score,
            "total_sessions": total_sessions,
            "active_sessions": active_sessions,
            "error_sessions": error_sessions,
            "connected_sessions": connected_sessions,
            "events_last_hour": len([e for e in self._events 
                                   if e.timestamp and 
                                   e.timestamp > datetime.now() - timedelta(hours=1)]),
            "monitoring_enabled": self._monitoring_enabled,
        }

    def configure_monitoring(self, enabled: bool) -> None:
        """Enable or disable monitoring."""
        self._monitoring_enabled = enabled
        self.logger.info("Monitoring configuration updated", enabled=enabled)

    def health_check(self) -> dict[str, Any]:
        """Perform health check."""
        return {
            "service": "turzx_display_service",
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "active_sessions": len(self._sessions),
            "redis_connected": self.redis is not None and self.redis.ping(),
            "hardware_connected": self._serial_driver.connected,
            "hardware_initialized": self._hardware_initialized,
            "last_event_count": len(self._events),
        }

    def cleanup_expired_sessions(self, max_age_hours: int = 24) -> int:
        """Clean up expired sessions."""
        cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
        expired_ids = []
        
        for session_id, session in self._sessions.items():
            if session.last_activity < cutoff_time:
                expired_ids.append(session_id)
        
        cleaned = 0
        for session_id in expired_ids:
            self.close_session(session_id)
            cleaned += 1
        
        if cleaned > 0:
            self.logger.info("Cleaned up expired display sessions", count=cleaned)
        return cleaned

    # =========================================================================
    # Hardware Integration Methods (using new TURXZSerialDriver protocol)
    # =========================================================================

    def initialize_hardware(self) -> dict[str, Any]:
        """Initialize TURZX display hardware with the official Rev A protocol.
        
        Runs: connect() -> initialize() sequence
        Returns status of each step.
        """
        self.logger.info("Initializing TURZX display hardware")
        results = []
        
        try:
            # Step 1: Connect and run HELLO handshake
            self.logger.info("Step 1: Connecting to display...")
            connected = self._serial_driver.connect()
            results.append({
                'command': 'CONNECT',
                'response': f"Connected at {self._serial_driver.baudrate} baud",
                'success': connected
            })
            
            if not connected:
                self.logger.error("Failed to connect to display")
                return {
                    'status': 'failed',
                    'error': 'Connection failed',
                    'results': results,
                    'hardware_ready': False
                }
            
            # Step 2: Initialize (orientation, brightness, clear)
            self.logger.info("Step 2: Initializing display settings...")
            initialized = self._serial_driver.initialize()
            results.append({
                'command': 'INITIALIZE',
                'response': 'Display initialized',
                'success': initialized
            })
            
            if not initialized:
                self.logger.warning("Display initialization incomplete")
            
            self._hardware_initialized = True
            
            self.logger.info(
                "Hardware initialization completed",
                status="completed",
                results=results
            )
            
            return {
                'status': 'completed',
                'results': results,
                'hardware_ready': True,
                'display_info': self._serial_driver.status()
            }
            
        except TURXZSerialProtocolError as e:
            self.logger.error("Hardware initialization failed with protocol error", error=str(e))
            return {
                'status': 'failed',
                'error': str(e),
                'results': results,
                'hardware_ready': False
            }
        except Exception as e:
            self.logger.error("Hardware initialization failed", error=str(e))
            return {
                'status': 'failed',
                'error': str(e),
                'results': results,
                'hardware_ready': False
            }

    def render_quota_display(
        self, 
        agent_data: dict[str, dict[str, Any]], 
        timestamp: str | None = None,
        hermes_jobs: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """Render agent quota display on the TURZX display hardware.
        
        Creates an RGB565 image with quota information for all agents and
        displays it on the 320x480 TURZX display.
        
        Args:
            agent_data: Dict mapping agent_id to quota info
                e.g., {
                    "agy1": {"used_pct": 25.5, "remaining_pct": 74.5, "status": "healthy"},
                    "codex1": {"used_pct": 80.0, "remaining_pct": 20.0, "status": "warning"},
                    ...
                }
            timestamp: Optional timestamp string to display
            hermes_jobs: Optional list of Hermes scheduled jobs metadata
            
        Returns:
            Dict with rendering results.
        """
        self.logger.info("Rendering agent quota display", agents=list(agent_data.keys()))
        
        try:
            # Create high-contrast RGB565 image for 320x480 display
            width = self._serial_driver.display_width
            height = self._serial_driver.display_height
            
            # Deep Charcoal Slate background (prevents IPS backlight bleed & glare)
            img = Image.new('RGB', (width, height), color=(11, 15, 25))
            
            from PIL import ImageDraw, ImageFont
            draw = ImageDraw.Draw(img)
            
            # Try to load a font with graceful fallback
            import os
            font_title = None
            font_agent = None
            font_val = None
            font_badge = None
            font_footer = None
            for font_cand in [
                "/System/Library/Fonts/SFCompact.ttf",
                "/System/Library/Fonts/Helvetica.ttc",
                "/System/Library/Fonts/Supplemental/Arial.ttf",
            ]:
                if os.path.isfile(font_cand):
                    try:
                        font_title = ImageFont.truetype(font_cand, 13)
                        font_agent = ImageFont.truetype(font_cand, 12)
                        font_val = ImageFont.truetype(font_cand, 11)
                        font_badge = ImageFont.truetype(font_cand, 10)
                        font_footer = ImageFont.truetype(font_cand, 10)
                        break
                    except Exception:
                        pass
            if not font_title:
                font_title = ImageFont.load_default()
                font_agent = font_title
                font_val = font_title
                font_badge = font_title
                font_footer = font_title
            
            # Title bar (Navy Slate with bottom border)
            draw.rectangle([(0, 0), (width, 34)], fill=(21, 30, 48))
            draw.line([(0, 34), (width, 34)], fill=(37, 53, 84), width=1)
            draw.text((12, 10), "HERMES AGENT QUOTAS", fill=(248, 250, 252), font=font_title)
            
            ts = timestamp or datetime.now().strftime("%H:%M:%S")
            draw.text((width - 12, 11), ts, fill=(148, 163, 184), font=font_val, anchor='ra')
            
            # Color palette (WCAG high-contrast, dark mode optimized for IPS LCD)
            color_map = {
                'healthy': {
                    'bar_fill': (5, 150, 105),       # Deep Forest Emerald (no glare)
                    'bar_track': (15, 23, 42),       # Dark Slate track
                    'bar_border': (51, 65, 85),
                    'badge_bg': (6, 78, 59),         # Deep Emerald container
                    'badge_text': (110, 231, 183),   # High-contrast Mint text
                    'badge_border': (5, 150, 105),
                },
                'warning': {
                    'bar_fill': (217, 119, 6),       # Warm Amber
                    'bar_track': (15, 23, 42),
                    'bar_border': (51, 65, 85),
                    'badge_bg': (69, 26, 3),
                    'badge_text': (252, 211, 77),
                    'badge_border': (217, 119, 6),
                },
                'critical': {
                    'bar_fill': (220, 38, 38),       # Crimson Red
                    'bar_track': (15, 23, 42),
                    'bar_border': (51, 65, 85),
                    'badge_bg': (69, 10, 10),
                    'badge_text': (252, 165, 165),
                    'badge_border': (220, 38, 38),
                }
            }
            
            # Priority order for target 8 agents
            target_order = [f"agy{i}" for i in range(1, 6)] + [f"codex{i}" for i in range(1, 4)]
            sorted_items = []
            for tid in target_order:
                if tid in agent_data:
                    sorted_items.append((tid, agent_data[tid]))
            for k, v in agent_data.items():
                if k not in target_order:
                    sorted_items.append((k, v))

            is_landscape = width > height

            if is_landscape:
                # 2-column layout for landscape (480x320)
                col_w = (width - 24) // 2
                card_h = 56
                for i, (agent_id, quota) in enumerate(sorted_items[:8]):
                    col = i // 4
                    row = i % 4
                    rx = 8 if col == 0 else (16 + col_w)
                    ry = 36 + row * 60
                    
                    used_pct = quota.get("used_pct", quota.get("usage_percentage", 0.0))
                    remaining_pct = quota.get("remaining_pct", max(0.0, 100.0 - used_pct))
                    status = quota.get("display_status", quota.get("status", "healthy"))
                    if status in ("within_limit", "ok"):
                        status = "healthy"
                    elif status in ("exceeded", "limit_reached"):
                        status = "critical"
                    
                    theme = color_map.get(status, color_map['healthy'])
                    
                    draw.rounded_rectangle(
                        [(rx, ry), (rx + col_w, ry + card_h)],
                        radius=4,
                        fill=(19, 27, 46),
                        outline=(37, 53, 84),
                        width=1
                    )
                    
                    role_lbl = quota.get("role", "")
                    if role_lbl:
                        role_short = role_lbl.split()[0].replace("/", "")[:10]
                        draw.text((rx + 8, ry + 6), f"{agent_id.upper()} ({role_short})", fill=(248, 250, 252), font=font_agent)
                    else:
                        draw.text((rx + 8, ry + 6), agent_id.upper(), fill=(248, 250, 252), font=font_agent)
                        
                    current_u = quota.get("current_usage", used_pct)
                    quota_lim = quota.get("quota_limit", 100.0)
                    draw.text((rx + col_w - 58, ry + 7), f"{current_u:.0f}/{quota_lim:.0f}", fill=(148, 163, 184), font=font_val, anchor='ra')

                    badge_text = f"{used_pct:.1f}%"
                    badge_w = 46
                    draw.rounded_rectangle(
                        [(rx + col_w - badge_w - 6, ry + 5), (rx + col_w - 6, ry + 20)],
                        radius=3,
                        fill=theme['badge_bg'],
                        outline=theme['badge_border'],
                        width=1
                    )
                    draw.text((rx + col_w - 6 - badge_w // 2, ry + 12), badge_text, fill=theme['badge_text'], font=font_badge, anchor='mm')
                    
                    bar_x = rx + 8
                    bar_y = ry + 26
                    bar_w = col_w - 16
                    bar_h = 16
                    draw.rectangle([(bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h)], fill=theme['bar_track'], outline=theme['bar_border'], width=1)
                    fill_w = int(bar_w * (used_pct / 100.0))
                    if fill_w > 0:
                        draw.rectangle([(bar_x + 1, bar_y + 1), (bar_x + fill_w, bar_y + bar_h - 1)], fill=theme['bar_fill'])
                    draw.text((bar_x + 4, bar_y + 3), f"Rem: {remaining_pct:.0f}%", fill=(241, 245, 249), font=font_footer)
                    model_str = quota.get("model", "")
                    if model_str:
                        model_short = model_str.split("@")[0].split(":")[0][:10]
                        draw.text((bar_x + bar_w - 4, bar_y + 3), model_short, fill=(148, 163, 184), font=font_footer, anchor='ra')
            else:
                # 1-column layout for portrait (320x480) - 8 agents fit cleanly
                y_pos = 38
                card_height = 48
                gap = 4
                
                for i, (agent_id, quota) in enumerate(sorted_items[:8]):
                    used_pct = quota.get("used_pct", quota.get("usage_percentage", 0.0))
                    remaining_pct = quota.get("remaining_pct", max(0.0, 100.0 - used_pct))
                    status = quota.get("display_status", quota.get("status", "healthy"))
                    if status in ("within_limit", "ok"):
                        status = "healthy"
                    elif status in ("exceeded", "limit_reached"):
                        status = "critical"
                    
                    theme = color_map.get(status, color_map['healthy'])
                    
                    # Card Background (clean separation per agent)
                    draw.rounded_rectangle(
                        [(8, y_pos), (width - 8, y_pos + card_height)],
                        radius=4,
                        fill=(19, 27, 46),
                        outline=(37, 53, 84),
                        width=1
                    )
                    
                    # Line 1: Agent Name & Quota usage details
                    role_lbl = quota.get("role", "")
                    if role_lbl:
                        role_short = role_lbl.split()[0].replace("/", "")[:10]
                        agent_title = f"{agent_id.upper()} ({role_short})"
                    else:
                        agent_title = agent_id.upper()
                    draw.text((14, y_pos + 6), agent_title, fill=(248, 250, 252), font=font_agent)
                    
                    current_u = quota.get("current_usage", used_pct)
                    quota_lim = quota.get("quota_limit", 100.0)
                    # Right-aligned before status badge pill (no text overlap/collision)
                    draw.text((width - 72, y_pos + 7), f"{current_u:.0f}/{quota_lim:.0f}", fill=(148, 163, 184), font=font_val, anchor='ra')
                    
                    # Status Badge (Percentage Pill)
                    badge_text = f"{used_pct:.1f}%"
                    draw.rounded_rectangle(
                        [(width - 66, y_pos + 5), (width - 16, y_pos + 20)],
                        radius=3,
                        fill=theme['badge_bg'],
                        outline=theme['badge_border'],
                        width=1
                    )
                    draw.text((width - 41, y_pos + 12), badge_text, fill=theme['badge_text'], font=font_badge, anchor='mm')
                    
                    # Line 2: Dedicated Progress Bar (no text collision)
                    bar_x = 14
                    bar_y = y_pos + 26
                    bar_w = width - 28
                    bar_h = 15
                    
                    draw.rectangle([(bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h)], fill=theme['bar_track'], outline=theme['bar_border'], width=1)
                    
                    fill_w = int(bar_w * (used_pct / 100.0))
                    if fill_w > 0:
                        draw.rectangle([(bar_x + 1, bar_y + 1), (bar_x + fill_w, bar_y + bar_h - 1)], fill=theme['bar_fill'])
                    
                    # Overlay remaining percentage text inside progress bar
                    draw.text((bar_x + 6, bar_y + 2), f"Rem: {remaining_pct:.0f}%", fill=(241, 245, 249), font=font_footer)
                    
                    # Right-align AI model name inside progress bar
                    model_str = quota.get("model", "")
                    if model_str:
                        model_short = model_str.split("@")[0].split(":")[0][:14]
                        draw.text((bar_x + bar_w - 6, bar_y + 2), model_short, fill=(148, 163, 184), font=font_footer, anchor='ra')
                    
                    y_pos += card_height + gap
            
            # Bottom Footer (Navy Slate background with dynamic Hermes scheduled jobs telemetry)
            footer_y = height - 26
            draw.rectangle([(0, footer_y), (width, height)], fill=(15, 23, 42))
            draw.line([(0, footer_y), (width, footer_y)], fill=(37, 53, 84), width=1)

            if hermes_jobs:
                ok_count = sum(1 for j in hermes_jobs if j.get("last_status") == "ok")
                total_jobs = len(hermes_jobs)
                footer_text = f"HERMES CRON: {ok_count}/{total_jobs} OK | 8 AGENTS SYNCED"
            else:
                footer_text = "HERMES SCHEDULED JOBS SYNC | 8 AGENTS"

            draw.text((width // 2, height - 13), footer_text, fill=(148, 163, 184), font=font_footer, anchor='mm')
            
            # Save artifact copy for preview & verification
            try:
                img.save("agent_status_display.png")
            except Exception as e:
                self.logger.debug("Failed to save preview artifact", error=str(e))

            # Convert to RGB565 little-endian using numpy-based conversion
            rgb565_data = image_to_rgb565(img)
            
            # Fail-Closed buffer length validation
            expected_size = width * height * 2
            if len(rgb565_data) != expected_size:
                self.logger.error("Corrupted frame buffer length mismatch", size=len(rgb565_data), expected=expected_size)
                return {
                    'success': False,
                    'error': f'Buffer length mismatch: {len(rgb565_data)} != {expected_size}',
                    'elements_drawn': 0,
                }
            
            # Display on TURZX hardware
            self.logger.info("Sending image to TURZX display", size=len(rgb565_data))
            success = self._serial_driver.display_image(rgb565_data)
            
            if success:
                self.logger.info("Quota display rendered successfully")
                return {
                    'success': True,
                    'elements_drawn': len(agent_data),
                    'image_size': len(rgb565_data),
                }
            else:
                self.logger.error("Failed to send image to display")
                return {
                    'success': False,
                    'error': 'Display image send failed',
                    'elements_drawn': 0,
                }
            
        except Exception as e:
            self.logger.error("Quota display rendering failed", error=str(e))
            return {
                'success': False,
                'error': str(e),
                'elements_drawn': 0,
            }

    def display_pil_image(self, image: Image.Image) -> dict[str, Any]:
        """Display a PIL Image on the TURZX hardware."""
        self.logger.info("Displaying PIL image", size=image.size, mode=image.mode)
        try:
            success = self._serial_driver.display_pil_image(image)
            return {
                'success': success,
                'width': self._serial_driver.display_width,
                'height': self._serial_driver.display_height,
                'transport': self._serial_driver._transport,
            }
        except Exception as e:
            self.logger.error("Display PIL image failed", error=str(e))
            return {'success': False, 'error': str(e)}

    def display_image_file(self, image_path: str) -> dict[str, Any]:
        """Display an image from a file path on the TURZX hardware."""
        self.logger.info("Displaying image from file", path=image_path)
        try:
            success = self._serial_driver.display_image_file(image_path)
            return {
                'success': success,
                'image_path': image_path,
                'transport': self._serial_driver._transport,
            }
        except Exception as e:
            self.logger.error("Display image file failed", error=str(e))
            return {'success': False, 'error': str(e)}

    def display_image(self, image_input: Any) -> dict[str, Any]:
        """Display an image on TURZX hardware from filepath, PIL Image, or RGB565 bytes."""
        if isinstance(image_input, str):
            return self.display_image_file(image_input)
        elif isinstance(image_input, Image.Image):
            return self.display_pil_image(image_input)
        elif isinstance(image_input, (bytes, bytearray)):
            success = self._serial_driver.display_image(bytes(image_input))
            return {'success': success, 'bytes_sent': len(image_input)}
        else:
            return {'success': False, 'error': f'Unsupported image input type: {type(image_input)}'}

    def set_display_brightness(self, level: int) -> bool:
        """Set display brightness (0-100)."""
        if not self._hardware_initialized:
            return False
        return self._serial_driver.set_brightness(level)

    def set_display_orientation(self, orientation: int) -> bool:
        """Set display orientation (0=PORTRAIT, 1=LANDSCAPE, etc.)."""
        if not self._hardware_initialized:
            return False
        return self._serial_driver.set_orientation(orientation)

    def clear_display(self) -> bool:
        """Clear the display."""
        if not self._hardware_initialized:
            return False
        return self._serial_driver.clear()

    def turn_display_off(self) -> bool:
        """Turn display off."""
        if not self._hardware_initialized:
            return False
        return self._serial_driver.screen_off()

    def turn_display_on(self) -> bool:
        """Turn display on."""
        if not self._hardware_initialized:
            return False
        return self._serial_driver.screen_on()

    def update_display_protocol(
        self, 
        agent_data: dict[str, dict[str, Any]] | None = None,
        agent_quota_manager: Any = None,
        force_hardware_init: bool = True,
        hermes_jobs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        TURZX-native scheduled display protocol update.
        Called by TURZX scheduler or on demand.
        Collects quota metrics and renders the quota dashboard to the TURZX hardware display.
        """
        if agent_data is None:
            if agent_quota_manager is not None:
                if hasattr(agent_quota_manager, "sync_agent_quotas"):
                    sync_res = agent_quota_manager.sync_agent_quotas()
                    agent_data = sync_res.get("synced_agents", {})
                    if hermes_jobs is None:
                        hermes_jobs = sync_res.get("hermes_jobs", [])
                elif hasattr(agent_quota_manager, "get_all_quotas"):
                    agent_data = agent_quota_manager.get_all_quotas()
                    if hermes_jobs is None and hasattr(agent_quota_manager, "get_hermes_jobs"):
                        hermes_jobs = agent_quota_manager.get_hermes_jobs()
            else:
                try:
                    from agent_quota_manager import AgentQuotaManager
                    aqm = AgentQuotaManager()
                    sync_res = aqm.sync_agent_quotas()
                    agent_data = sync_res.get("synced_agents", aqm.get_all_quotas())
                    if hermes_jobs is None:
                        hermes_jobs = sync_res.get("hermes_jobs", [])
                except Exception as e:
                    self.logger.warning("Could not auto-fetch agent quotas", error=str(e))
                    agent_data = {}

        if force_hardware_init and not self._hardware_initialized:
            init_res = self.initialize_hardware()
            if not init_res.get("hardware_ready", False):
                err_msg = init_res.get("error", "Hardware connection failed")
                self.logger.error("Display hardware initialization failed", error=err_msg)
                return {
                    "success": False,
                    "error": f"Display hardware initialization failed: {err_msg}",
                    "elements_drawn": 0,
                }

        return self.render_quota_display(agent_data or {}, hermes_jobs=hermes_jobs)

    def disconnect(self) -> bool:
        """Disconnect display driver."""
        if hasattr(self, '_serial_driver') and self._serial_driver:
            self._hardware_initialized = False
            return self._serial_driver.disconnect()
        return True

    def close(self) -> bool:
        """Alias for disconnect."""
        return self.disconnect()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()

    def get_hardware_status(self) -> dict[str, Any]:
        """Get hardware connection and display status."""
        return self._serial_driver.status()

    def __repr__(self) -> str:
        return f"<TURZXDisplayService sessions_count={len(self._sessions)} events_count={len(self._events)} hardware_ready={self._hardware_initialized}>"

    # =========================================================================
    # TURXZ Scheduler Integration Methods
    # =========================================================================
    
    def register_scheduler_jobs(self, scheduler: 'TURXZScheduler') -> dict[str, str]:
        """
        Register display service jobs with TURXZ scheduler.
        
        Args:
            scheduler: TURXZ scheduler instance
            
        Returns:
            Dict mapping job names to job IDs
        """
        if not TURXZ_SCHEDULER_AVAILABLE:
            self.logger.warning("TURXZ scheduler not available for job registration")
            return {}
        
        job_ids = {}

        # Display quota update - runs periodically (30s)
        job_ids["display_quota_update"] = scheduler.add_job(
            name="display_quota_update",
            schedule_type=ScheduleType.INTERVAL,
            schedule_value=30.0,
            job_definition=JobDefinition(
                job_id="display_quota_update",
                name="Display Quota Update",
                handler=self._scheduled_display_quota_update,
                timeout=30.0,
                priority=JobPriority.NORMAL,
                tags=["display", "quota", "scheduled"],
            ),
            metadata={"description": "Periodic display update of agent quotas"},
        )
        
        # Display health check - runs every 2 minutes
        job_ids["display_health_check"] = scheduler.add_job(
            name="display_health_check",
            schedule_type=ScheduleType.INTERVAL,
            schedule_value=120.0,  # 2 minutes
            job_definition=JobDefinition(
                job_id="display_health_check",
                name="Display Health Check",
                handler=self._scheduled_display_health_check,
                timeout=30.0,
                priority=JobPriority.NORMAL,
                tags=["display", "health", "scheduled"],
            ),
            metadata={"description": "Periodic display hardware health check"},
        )
        
        # Session cleanup - runs every hour
        job_ids["session_cleanup"] = scheduler.add_job(
            name="display_session_cleanup",
            schedule_type=ScheduleType.INTERVAL,
            schedule_value=3600.0,  # 1 hour
            job_definition=JobDefinition(
                job_id="display_session_cleanup",
                name="Display Session Cleanup",
                handler=self._scheduled_session_cleanup,
                timeout=60.0,
                priority=JobPriority.LOW,
                tags=["display", "maintenance", "cleanup"],
            ),
            metadata={"description": "Clean up expired display sessions"},
        )
        
        # Display metrics collection - runs every 5 minutes
        job_ids["display_metrics"] = scheduler.add_job(
            name="display_metrics_collection",
            schedule_type=ScheduleType.INTERVAL,
            schedule_value=300.0,  # 5 minutes
            job_definition=JobDefinition(
                job_id="display_metrics_collection",
                name="Display Metrics Collection",
                handler=self._scheduled_metrics_collection,
                timeout=30.0,
                priority=JobPriority.LOW,
                tags=["display", "metrics", "monitoring"],
            ),
            metadata={"description": "Collect display system metrics"},
        )
        
        # Hardware status check - runs at startup
        job_ids["hardware_status"] = scheduler.add_job(
            name="display_hardware_status",
            schedule_type=ScheduleType.STARTUP,
            schedule_value=0,
            job_definition=JobDefinition(
                job_id="display_hardware_status",
                name="Display Hardware Status",
                handler=self._scheduled_hardware_status,
                timeout=30.0,
                priority=JobPriority.NORMAL,
                tags=["display", "hardware", "startup"],
            ),
            metadata={"description": "Check display hardware on startup"},
        )
        
        self.logger.info("Registered TURXZ scheduler jobs for display service", 
                        job_ids=job_ids)
        
        return job_ids
    
    def _scheduled_display_health_check(self) -> dict[str, Any]:
        """Scheduled display health check handler."""
        try:
            health = self.health_check()
            
            # Add hardware-specific checks
            hardware_status = self.get_hardware_status()
            health["hardware"] = hardware_status
            
            # Log if hardware is not connected
            if not hardware_status.get("connected", False):
                self.logger.warning("Display hardware not connected", 
                                  hardware_status=hardware_status)
            
            return {
                "status": "completed",
                "health": health,
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            self.logger.error("Scheduled display health check failed", error=str(e))
            return {
                "status": "failed",
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }
    
    def _scheduled_session_cleanup(self) -> dict[str, Any]:
        """Scheduled session cleanup handler."""
        try:
            cleaned = self.cleanup_expired_sessions(max_age_hours=24)
            return {
                "status": "completed",
                "cleaned_sessions": cleaned,
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            self.logger.error("Scheduled session cleanup failed", error=str(e))
            return {
                "status": "failed",
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }
    
    def _scheduled_metrics_collection(self) -> dict[str, Any]:
        """Scheduled metrics collection handler."""
        try:
            analytics = self.get_display_analytics(hours=1)
            system_status = self.get_system_status()
            
            return {
                "status": "completed",
                "analytics": analytics,
                "system_status": system_status,
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            self.logger.error("Scheduled metrics collection failed", error=str(e))
            return {
                "status": "failed",
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }
    
    def _scheduled_hardware_status(self) -> dict[str, Any]:
        """Scheduled hardware status check handler."""
        try:
            hardware_status = self.get_hardware_status()
            
            # Try to initialize if not initialized
            if not self._hardware_initialized:
                self.logger.info("Hardware not initialized, attempting initialization")
                init_result = self.initialize_hardware()
                hardware_status["initialization"] = init_result
            
            return {
                "status": "completed",
                "hardware_status": hardware_status,
                "hardware_initialized": self._hardware_initialized,
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            self.logger.error("Scheduled hardware status check failed", error=str(e))
            return {
                "status": "failed",
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }
    
    def _scheduled_display_quota_update(self) -> dict[str, Any]:
        """Scheduled display quota update handler."""
        try:
            res = self.update_display_protocol()
            if not res.get("success", False):
                raise RuntimeError(f"Display update failed: {res.get('error', 'Unknown error')}")
            return {
                "status": "completed",
                "result": res,
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            self.logger.error("Scheduled display quota update failed", error=str(e))
            raise

    def get_scheduler_integration_status(self) -> dict[str, Any]:
        """Get TURXZ scheduler integration status."""
        return {
            "scheduler_available": TURXZ_SCHEDULER_AVAILABLE,
            "integration_enabled": TURXZ_SCHEDULER_AVAILABLE,
            "supported_jobs": [
                "display_quota_update",
                "display_health_check",
                "display_session_cleanup",
                "display_metrics_collection",
                "display_hardware_status",
            ] if TURXZ_SCHEDULER_AVAILABLE else [],
        }