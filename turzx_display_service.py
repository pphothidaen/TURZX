#!/usr/bin/env python3
"""TURZX display service for DeepSeek Web Bridge.

Integrates with TURXZSerialDriver using the official Rev A (UsbMonitor) protocol.
"""

import asyncio
import json
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import structlog
from dataclasses import dataclass, asdict
from enum import Enum
from PIL import Image

from backend.services.serial_driver import (
    TURXZSerialDriver, 
    TURXZSerialError,
    TURXZSerialProtocolError,
    Command,
    SubRevision,
    image_to_rgb565,
)

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
    screen_resolution: Dict[str, int] = None
    refresh_rate: float = 0.0
    cpu_usage: float = 0.0
    memory_usage: float = 0.0
    active_sessions: int = 0
    total_sessions: int = 0
    last_update: datetime = None


@dataclass
class TURZXConfig:
    enabled: bool = False
    resolution: Dict[str, int] = None
    refresh_rate: float = 60.0
    color_depth: int = 8
    gamma: float = 2.2
    hdr_enabled: bool = False
    adaptive_refresh: bool = False
    custom_settings: Dict[str, Any] = None


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
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = None

    def to_dict(self) -> Dict[str, Any]:
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
    data: Dict[str, Any] = None
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
        self._sessions: Dict[str, DisplaySession] = {}
        self._events: List[DisplayEvent] = []
        self._monitoring_enabled = True
        self._serial_driver = TURXZSerialDriver(
            port=port, 
            baudrate=baudrate,
            display_width=display_width,
            display_height=display_height,
        )
        self._hardware_initialized = False

    def create_display_session(self, session_id: str, agent_id: str, 
                              initial_config: Optional[TURZXConfig] = None) -> DisplaySession:
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

    def get_session(self, session_id: str) -> Optional[DisplaySession]:
        """Get display session by ID."""
        return self._sessions.get(session_id)

    def update_session_status(self, session_id: str, status: DisplayStatus, 
                            error_message: Optional[str] = None) -> bool:
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

    def update_session_config(self, session_id: str, config_updates: Dict[str, Any]) -> bool:
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

    def update_session_metrics(self, session_id: str, metrics: Dict[str, Any]) -> bool:
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

    def get_session_status(self, session_id: str) -> Optional[Dict[str, Any]]:
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

    def get_all_sessions(self) -> List[Dict[str, Any]]:
        """Get all active display sessions."""
        sessions = []
        for session in self._sessions.values():
            sessions.append(self.get_session_status(session.session_id))
        
        return sessions

    def _add_event(self, event_type: str, session_id: str, 
                   data: Optional[Dict[str, Any]] = None) -> DisplayEvent:
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
                          event_type: Optional[str] = None) -> List[Dict[str, Any]]:
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

    def get_display_analytics(self, hours: int = 24) -> Dict[str, Any]:
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

    def get_system_status(self) -> Dict[str, Any]:
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

    def health_check(self) -> Dict[str, Any]:
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

    def initialize_hardware(self) -> Dict[str, Any]:
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
        agent_data: Dict[str, Dict[str, Any]], 
        timestamp: Optional[str] = None
    ) -> Dict[str, Any]:
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
            
        Returns:
            Dict with rendering results.
        """
        self.logger.info("Rendering agent quota display", agents=list(agent_data.keys()))
        
        try:
            # Create RGB565 image for 320x480 display
            width = self._serial_driver.display_width
            height = self._serial_driver.display_height
            
            # Create image using PIL
            img = Image.new('RGB', (width, height), color='black')
            
            # Draw quota bars and text
            from PIL import ImageDraw, ImageFont
            draw = ImageDraw.Draw(img)
            
            # Try to load a font with graceful fallback
            import os
            font = None
            font_large = None
            for font_cand in [
                "/System/Library/Fonts/SFCompact.ttf",
                "/System/Library/Fonts/Helvetica.ttc",
                "/System/Library/Fonts/Supplemental/Arial.ttf",
            ]:
                if os.path.isfile(font_cand):
                    try:
                        font = ImageFont.truetype(font_cand, 12)
                        font_large = ImageFont.truetype(font_cand, 13)
                        break
                    except Exception:
                        pass
            if not font:
                font = ImageFont.load_default()
                font_large = font
            
            # Title bar
            draw.rectangle([0, 0, width, 28], fill='#1a1a2e')
            draw.text((8, 6), "AGENT QUOTA DASHBOARD", fill='#00ff88', font=font_large)
            
            # Timestamp
            ts = timestamp or datetime.now().strftime("%H:%M:%S")
            draw.text((width - 65, 7), ts, fill='#888888', font=font)
            
            # Agent rows
            y_pos = 40
            row_height = 50
            max_agents = (height - 50) // row_height
            
            for i, (agent_id, quota) in enumerate(list(agent_data.items())[:max_agents]):
                used_pct = quota.get("used_pct", 0.0)
                remaining_pct = quota.get("remaining_pct", 100.0 - used_pct)
                status = quota.get("status", "healthy")
                
                # Status color
                if status == "healthy":
                    status_color = '#00ff88'
                    bar_color = '#00ff88'
                elif status == "warning":
                    status_color = '#ffaa00'
                    bar_color = '#ffaa00'
                elif status == "critical":
                    status_color = '#ff4444'
                    bar_color = '#ff4444'
                else:
                    status_color = '#888888'
                    bar_color = '#444444'
                
                # Agent name
                draw.text((10, y_pos + 2), agent_id.upper(), fill='#ffffff', font=font)
                
                # Usage percentage
                draw.text((width - 80, y_pos + 2), f"{used_pct:.1f}%", fill=status_color, font=font)
                
                # Progress bar background
                bar_x = 10
                bar_y = y_pos + 22
                bar_width = width - 20
                bar_h = 20
                draw.rectangle([bar_x, bar_y, bar_x + bar_width, bar_y + bar_h], fill='#333333', outline='#555555')
                
                # Progress bar fill
                fill_width = int(bar_width * (used_pct / 100.0))
                if fill_width > 0:
                    draw.rectangle([bar_x, bar_y, bar_x + fill_width, bar_y + bar_h], fill=bar_color)
                
                # Remaining text
                small_font = ImageFont.load_default()
                draw.text((10, y_pos + 24 + 2), f"Rem: {remaining_pct:.1f}%", fill='#aaaaaa', font=small_font)
                
                y_pos += row_height
            
            # Convert to RGB565 little-endian using numpy-based conversion
            rgb565_data = image_to_rgb565(img)
            
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

    def display_pil_image(self, image: Image.Image) -> Dict[str, Any]:
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

    def display_image_file(self, image_path: str) -> Dict[str, Any]:
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

    def display_image(self, image_input: Any) -> Dict[str, Any]:
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

    def get_hardware_status(self) -> Dict[str, Any]:
        """Get hardware connection and display status."""
        return self._serial_driver.status()

    def __repr__(self) -> str:
        return f"<TURZXDisplayService sessions_count={len(self._sessions)} events_count={len(self._events)} hardware_ready={self._hardware_initialized}>"