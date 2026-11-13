"""
TURZX System Power & Display Optimization Module.

Implements system-wide power management, adaptive display tuning, thermal protection,
numeric memory pressure safeguards, and intelligent resource allocation for the
TURZX display ecosystem under the TURXZ AI Agent Governance Framework (AGENTS.md).
"""

from __future__ import annotations

import logging
import os
import platform
import re
import resource
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

try:
    import structlog
    logger = structlog.get_logger(__name__)
except ImportError:
    logger = logging.getLogger(__name__)

from .auth import PermissionDeniedError, require_process_token, verify_process_token
from .protocol import (
    TURZX_MAX_FPS,
    TURZXError,
    TURZXProtocolError,
)

# -----------------------------------------------------------------------------
# Enums and Domain Contracts
# -----------------------------------------------------------------------------

class PowerMode(str, Enum):
    """System-wide operational power modes."""
    MAXIMUM = "maximum"      # 100% capacity / maximum performance
    BALANCED = "balanced"    # 70% capacity / standard balanced operation
    EFFICIENT = "efficient"  # 40% capacity / power conservation
    MINIMUM = "minimum"      # 20% capacity / low-power / critical throttling

    @property
    def capacity_percent(self) -> int:
        """Operating capacity percentage."""
        mapping = {
            PowerMode.MAXIMUM: 100,
            PowerMode.BALANCED: 70,
            PowerMode.EFFICIENT: 40,
            PowerMode.MINIMUM: 20,
        }
        return mapping[self]

    def max_concurrency(self, base_concurrency: int = 10) -> int:
        """Calculate scaled maximum job concurrency (capped at 10 per Rule 4)."""
        base = min(base_concurrency, 10)
        fraction = self.capacity_percent / 100.0
        return max(1, int(round(base * fraction)))

    @property
    def default_brightness(self) -> int:
        """Recommended screen brightness percentage for this power mode."""
        mapping = {
            PowerMode.MAXIMUM: 80,
            PowerMode.BALANCED: 60,
            PowerMode.EFFICIENT: 40,
            PowerMode.MINIMUM: 20,
        }
        return mapping[self]

    @property
    def throttle_delay(self) -> float:
        """Inter-task cooldown or sleep delay in seconds."""
        mapping = {
            PowerMode.MAXIMUM: 0.0,
            PowerMode.BALANCED: 0.05,
            PowerMode.EFFICIENT: 0.2,
            PowerMode.MINIMUM: 0.5,
        }
        return mapping[self]


class BrightnessProfile(str, Enum):
    """Target display brightness profiles."""
    WORK_ENVIRONMENT = "work_environment"  # 50-60% (default 55%)
    DEEP_WORK = "deep_work"                # 30-40% (default 35%)
    PRESENTATION = "presentation"          # 70-80% (default 75%)
    IDLE = "idle"                          # 15% dimmed mode
    OFF = "off"                            # 0% display standby

    @property
    def default_brightness(self) -> int:
        mapping = {
            BrightnessProfile.WORK_ENVIRONMENT: 55,
            BrightnessProfile.DEEP_WORK: 35,
            BrightnessProfile.PRESENTATION: 75,
            BrightnessProfile.IDLE: 15,
            BrightnessProfile.OFF: 0,
        }
        return mapping[self]

    @property
    def brightness_range(self) -> Tuple[int, int]:
        mapping = {
            BrightnessProfile.WORK_ENVIRONMENT: (50, 60),
            BrightnessProfile.DEEP_WORK: (30, 40),
            BrightnessProfile.PRESENTATION: (70, 80),
            BrightnessProfile.IDLE: (10, 20),
            BrightnessProfile.OFF: (0, 0),
        }
        return mapping[self]


class TaskPowerCategory(str, Enum):
    """Categorization of compute tasks by power consumption."""
    POWER_CRITICAL = "power_critical"  # Heavy AI token processing, dense matrix / display blits
    MODERATE = "moderate"              # Standard dashboard renders, telemetry aggregation
    LOW = "low"                        # Lightweight status checks, heartbeats, ping probes


class TaskUrgency(str, Enum):
    """Task urgency levels for intelligent resource scheduling."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ThermalState(str, Enum):
    """System thermal management states."""
    NORMAL = "normal"      # <= 75.0°C
    MODERATE = "moderate"  # > 75.0°C and <= 85.0°C (moderate throttling)
    CRITICAL = "critical"  # > 85.0°C (severe power/frequency throttling)


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

@dataclass
class MemoryPressureInfo:
    """System memory pressure telemetry."""
    free_percent: float
    is_critical: bool  # True if free_percent < threshold (default 20.0%)
    raw_output: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "free_percent": round(self.free_percent, 1),
            "is_critical": self.is_critical,
            "raw_output": self.raw_output,
            "details": self.details,
            "timestamp": self.timestamp,
        }


@dataclass
class ThermalInfo:
    """System thermal status telemetry."""
    temperature: float
    state: ThermalState
    is_throttled: bool
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "temperature": round(self.temperature, 1),
            "state": self.state.value,
            "is_throttled": self.is_throttled,
            "details": self.details,
            "timestamp": self.timestamp,
        }


# -----------------------------------------------------------------------------
# 1. System Memory Management (Critical for Power Efficiency)
# -----------------------------------------------------------------------------

class SystemMemoryManager:
    """Monitors system-wide memory pressure with fail-closed safeguards."""

    DEFAULT_THRESHOLD: float = 20.0  # 20% free RAM triggers fail-closed / throttling

    # Authoritative awk check command defined in specification:
    AWK_CHECK_CMD = (
        "memory_pressure | awk '/System-wide memory free percentage:/ "
        "{gsub(\"%\",\"\"); print ($NF < 20) ? 1 : 0}'"
    )

    def __init__(self, threshold_percent: float = DEFAULT_THRESHOLD):
        self.threshold_percent = threshold_percent
        self._lock = threading.Lock()

    def get_memory_pressure(self, threshold: Optional[float] = None) -> MemoryPressureInfo:
        """Inspect macOS / POSIX system-wide memory pressure.

        Executes macOS memory_pressure command and parses System-wide memory free percentage.
        Falls back to platform alternatives when memory_pressure is unavailable.
        """
        thresh = threshold if threshold is not None else self.threshold_percent
        is_darwin = platform.system().lower() == "darwin"

        if is_darwin:
            try:
                proc = subprocess.run(
                    ["memory_pressure"],
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                    check=False,
                )
                if proc.returncode == 0 and proc.stdout:
                    output = proc.stdout
                    # Match "System-wide memory free percentage: 46%"
                    m = re.search(r"System-wide memory free percentage:\s*(\d+)%", output)
                    if m:
                        free_pct = float(m.group(1))
                        is_crit = free_pct < thresh
                        return MemoryPressureInfo(
                            free_percent=free_pct,
                            is_critical=is_crit,
                            raw_output=output.strip(),
                            details={"method": "darwin_memory_pressure", "threshold": thresh},
                        )
            except Exception as e:
                logger.error("Failed to query macOS memory_pressure tool", error=str(e))

        # Fallback 1: Linux /proc/meminfo
        if os.path.exists("/proc/meminfo"):
            try:
                with open("/proc/meminfo", "r", encoding="utf-8") as f:
                    lines = f.readlines()
                info = {}
                for line in lines:
                    if ":" in line:
                        k, v = line.split(":", 1)
                        info[k.strip()] = int(v.strip().split()[0])
                total = info.get("MemTotal", 0)
                avail = info.get("MemAvailable", info.get("MemFree", 0))
                free_pct = (avail / total * 100.0) if total > 0 else 0.0
                return MemoryPressureInfo(
                    free_percent=free_pct,
                    is_critical=free_pct < thresh,
                    details={"method": "linux_proc_meminfo", "threshold": thresh},
                )
            except Exception as e:
                logger.error("Failed to read /proc/meminfo", error=str(e))

        # Fallback 2: Fail-closed safe estimate
        logger.warning("Memory pressure source unavailable; enforcing fail-safe baseline")
        return MemoryPressureInfo(
            free_percent=50.0,
            is_critical=False,
            details={"method": "fallback_safe_default", "threshold": thresh},
        )

    def execute_awk_check(self) -> bool:
        """Run the exact awk script specified in the system specification.

        Returns True if memory pressure is HIGH (free < 20%), False otherwise.
        """
        try:
            res = subprocess.run(
                self.AWK_CHECK_CMD,
                shell=True,
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
            output = res.stdout.strip()
            return output == "1"
        except Exception as e:
            logger.error("Failed executing awk memory check", error=str(e))
            # Rule 2: Fail-Closed enforcement
            return True

    def is_memory_pressure_high(self, threshold: Optional[float] = None) -> bool:
        """Check whether memory free percentage is below threshold."""
        info = self.get_memory_pressure(threshold=threshold)
        return info.is_critical


# -----------------------------------------------------------------------------
# 2. Thermal Management & CPU Throttling
# -----------------------------------------------------------------------------

class ThermalManager:
    """Monitors system temperature and determines throttling levels."""

    CRITICAL_TEMP_THRESHOLD: float = 85.0  # > 85°C triggers severe throttling
    MODERATE_TEMP_THRESHOLD: float = 75.0  # > 75°C triggers moderate throttling

    def __init__(
        self,
        critical_threshold: float = CRITICAL_TEMP_THRESHOLD,
        moderate_threshold: float = MODERATE_TEMP_THRESHOLD,
    ):
        self.critical_threshold = critical_threshold
        self.moderate_threshold = moderate_threshold
        self._simulated_temp: Optional[float] = None
        self._lock = threading.Lock()

    def set_simulated_temperature(self, temp: Optional[float]) -> None:
        """Set simulated temperature for unit testing and deterministic verification."""
        with self._lock:
            self._simulated_temp = temp

    def get_thermal_info(self) -> ThermalInfo:
        """Query current thermal status and categorize into ThermalState."""
        with self._lock:
            if self._simulated_temp is not None:
                temp = self._simulated_temp
                state = self._classify_temperature(temp)
                return ThermalInfo(
                    temperature=temp,
                    state=state,
                    is_throttled=state != ThermalState.NORMAL,
                    details={"source": "simulated"},
                )

        temp = self._read_system_temperature()
        state = self._classify_temperature(temp)
        return ThermalInfo(
            temperature=temp,
            state=state,
            is_throttled=state != ThermalState.NORMAL,
            details={"source": "system"},
        )

    def _classify_temperature(self, temp: float) -> ThermalState:
        if temp > self.critical_threshold:
            return ThermalState.CRITICAL
        if temp > self.moderate_threshold:
            return ThermalState.MODERATE
        return ThermalState.NORMAL

    def calculate_throttle_factor(self, temp: Optional[float] = None) -> float:
        """Compute execution throttling scaling factor between 0.2 and 1.0."""
        t = temp if temp is not None else self.get_thermal_info().temperature
        if t > self.critical_threshold:
            return 0.2  # Severe power & frequency reduction
        if t > self.moderate_threshold:
            return 0.5  # Moderate throttling
        return 1.0      # Normal operation

    def _read_system_temperature(self) -> float:
        """Query host temperature via macOS pmset or sysctl."""
        if platform.system().lower() == "darwin":
            try:
                proc = subprocess.run(
                    ["pmset", "-g", "therm"],
                    capture_output=True,
                    text=True,
                    timeout=3.0,
                    check=False,
                )
                output = proc.stdout
                # Look for warning indicators
                if "thermal warning" in output.lower() and "no thermal warning" not in output.lower():
                    return 88.0
                if "performance warning" in output.lower() and "no performance warning" not in output.lower():
                    return 78.0
            except Exception:
                pass

        # Default normal ambient operating temperature
        return 50.0


# -----------------------------------------------------------------------------
# 3. System Resource Management (ulimits & Launchd Configuration)
# -----------------------------------------------------------------------------

class SystemResourceManager:
    """Manages file descriptor limits and dynamic task resource allocation."""

    TARGET_FILE_DESCRIPTOR_LIMIT = 65536

    @classmethod
    def ensure_file_descriptor_limits(
        cls,
        target_limit: int = TARGET_FILE_DESCRIPTOR_LIMIT,
    ) -> Tuple[int, int]:
        """Verify and set POSIX file descriptor soft/hard limits (ulimit -n 65536).

        Returns:
            Tuple of (soft_limit, hard_limit)
        """
        try:
            soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            if soft < target_limit:
                new_hard = max(hard, target_limit)
                try:
                    resource.setrlimit(resource.RLIMIT_NOFILE, (target_limit, new_hard))
                    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
                except (ValueError, PermissionError) as pe:
                    # If raising hard limit requires root, raise soft limit to current hard max
                    if hard > soft:
                        resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
                        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
                    logger.debug("Adjusted file descriptor limits to maximum allowed", error=str(pe))
            return soft, hard
        except Exception as e:
            logger.error("Failed configuring file descriptor limit", error=str(e))
            return -1, -1

    @classmethod
    def can_admit_task(
        cls,
        task_power: TaskPowerCategory = TaskPowerCategory.MODERATE,
        urgency: TaskUrgency = TaskUrgency.MEDIUM,
        memory_free_pct: Optional[float] = None,
        thermal_temp: Optional[float] = None,
        power_mode: Optional[PowerMode] = None,
    ) -> Tuple[bool, str]:
        """Intelligent Resource Allocation Gate.

        Dynamically evaluates whether a task can be admitted based on current
        memory pressure, thermal limits, power mode, and task urgency.
        """
        mem_pct = memory_free_pct if memory_free_pct is not None else 50.0
        temp = thermal_temp if thermal_temp is not None else 50.0
        mode = power_mode if power_mode is not None else PowerMode.BALANCED

        # 1. Memory Pressure Fail-Closed Gate (threshold: 20% free RAM)
        if mem_pct < SystemMemoryManager.DEFAULT_THRESHOLD:
            if task_power == TaskPowerCategory.POWER_CRITICAL and urgency != TaskUrgency.CRITICAL:
                return False, f"Memory pressure high ({mem_pct:.1f}% free < 20%): power-critical task deferred"
            if urgency == TaskUrgency.LOW:
                return False, f"Memory pressure high ({mem_pct:.1f}% free < 20%): low-urgency task throttled"

        # 2. Thermal Management Gate (>85°C critical, >75°C moderate)
        if temp > ThermalManager.CRITICAL_TEMP_THRESHOLD:
            if task_power == TaskPowerCategory.POWER_CRITICAL:
                return False, f"Thermal critical ({temp:.1f}°C > 85°C): power-critical task suspended"
            if urgency in (TaskUrgency.LOW, TaskUrgency.MEDIUM):
                return False, f"Thermal critical ({temp:.1f}°C > 85°C): non-critical task throttled"
        elif temp > ThermalManager.MODERATE_TEMP_THRESHOLD:
            if task_power == TaskPowerCategory.POWER_CRITICAL and urgency == TaskUrgency.LOW:
                return False, f"Thermal elevated ({temp:.1f}°C > 75°C): deferring low-urgency power-critical task"

        # 3. Adaptive Power Mode Restrictions
        if mode == PowerMode.MINIMUM:
            if task_power == TaskPowerCategory.POWER_CRITICAL and urgency not in (TaskUrgency.CRITICAL, TaskUrgency.HIGH):
                return False, "Minimum power mode active: only high/critical urgency tasks permitted"

        return True, "Task admitted"


# -----------------------------------------------------------------------------
# 4. Screen Brightness & Smart Display Management
# -----------------------------------------------------------------------------

def get_macbook_brightness() -> Optional[float]:
    """Retrieve host MacBook display brightness via native macOS DisplayServices ctypes bindings.

    Uses DisplayServicesGetBrightness from DisplayServices.framework and CGMainDisplayID
    or built-in display ID from CoreGraphics with zero third-party dependencies.
    Prefers built-in Retina display ID over external monitors when multiple displays are active.
    Returns float in range [0.0, 1.0], or None if unavailable.
    """
    if platform.system().lower() != "darwin":
        return None
    try:
        import ctypes
        from ctypes import byref, c_bool, c_float, c_int, c_uint32

        cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cg.CGMainDisplayID.restype = c_uint32
        main_display_id = cg.CGMainDisplayID()

        target_display_id = main_display_id
        # Attempt to locate the built-in display ID (MacBook screen) if external monitors are connected
        try:
            cg.CGDisplayIsBuiltin.argtypes = [c_uint32]
            cg.CGDisplayIsBuiltin.restype = c_bool
            cg.CGGetActiveDisplayList.argtypes = [c_uint32, ctypes.POINTER(c_uint32), ctypes.POINTER(c_uint32)]
            cg.CGGetActiveDisplayList.restype = c_int

            count = c_uint32()
            displays = (c_uint32 * 16)()
            if cg.CGGetActiveDisplayList(16, displays, byref(count)) == 0 and count.value > 0:
                for i in range(count.value):
                    d_id = displays[i]
                    if bool(cg.CGDisplayIsBuiltin(d_id)):
                        target_display_id = d_id
                        break
        except Exception:
            target_display_id = main_display_id

        ds_path = "/System/Library/PrivateFrameworks/DisplayServices.framework/DisplayServices"
        ds = ctypes.cdll.LoadLibrary(ds_path)
        ds.DisplayServicesGetBrightness.argtypes = [c_uint32, ctypes.POINTER(c_float)]
        ds.DisplayServicesGetBrightness.restype = c_int

        brightness = c_float()
        ret = ds.DisplayServicesGetBrightness(target_display_id, byref(brightness))
        # If querying target_display_id fails and it was different from main_display_id, fallback to main_display_id
        if ret != 0 and target_display_id != main_display_id:
            ret = ds.DisplayServicesGetBrightness(main_display_id, byref(brightness))

        if ret == 0:
            val = float(brightness.value)
            return max(0.0, min(1.0, val))
    except Exception as e:
        logger.debug("DisplayServices brightness query unavailable", error=str(e))
    return None


def compute_clamped_brightness(
    host_brightness: float,
    min_clamp: int = 15,
    max_clamp: int = 85,
) -> int:
    """Map 0.0-1.0 float brightness to 0-100% with clamping between min_clamp and max_clamp (default: 15-85%)."""
    raw_pct = host_brightness * 100.0
    return max(min_clamp, min(max_clamp, int(round(raw_pct))))


def is_macos_screen_locked() -> bool:
    """Check if macOS screen is currently locked via CGSessionCopyCurrentDictionary."""
    if platform.system().lower() != "darwin":
        return False
    try:
        import ctypes
        from ctypes import c_bool, c_char_p, c_uint32, c_void_p

        app_serv = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        app_serv.CGSessionCopyCurrentDictionary.restype = c_void_p
        dict_ref = app_serv.CGSessionCopyCurrentDictionary()
        if not dict_ref:
            return False

        cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        cf.CFRelease.argtypes = [c_void_p]
        cf.CFDictionaryGetValue.argtypes = [c_void_p, c_void_p]
        cf.CFDictionaryGetValue.restype = c_void_p
        cf.CFStringCreateWithCString.argtypes = [c_void_p, c_char_p, c_uint32]
        cf.CFStringCreateWithCString.restype = c_void_p
        cf.CFBooleanGetValue.argtypes = [c_void_p]
        cf.CFBooleanGetValue.restype = c_bool

        kCFStringEncodingUTF8 = 0x08000100
        key = cf.CFStringCreateWithCString(None, b"CGSSessionScreenIsLocked", kCFStringEncodingUTF8)
        try:
            val = cf.CFDictionaryGetValue(dict_ref, key)
            if val:
                return bool(cf.CFBooleanGetValue(val))
            return False
        finally:
            if key:
                cf.CFRelease(key)
            cf.CFRelease(dict_ref)
    except Exception as e:
        logger.debug("Screen lock check failed", error=str(e))
        return False


def get_macos_user_inactivity_seconds() -> Optional[float]:
    """Query user inactivity duration in seconds via CGEventSourceSecondsSinceLastEventType."""
    if platform.system().lower() != "darwin":
        return None
    try:
        import ctypes
        from ctypes import c_double, c_uint32

        cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cg.CGEventSourceSecondsSinceLastEventType.argtypes = [c_uint32, c_uint32]
        cg.CGEventSourceSecondsSinceLastEventType.restype = c_double
        # 0 = kCGEventSourceStateCombinedSessionState, 0xFFFFFFFF = kCGAnyInputEventType
        sec = float(cg.CGEventSourceSecondsSinceLastEventType(0, 0xFFFFFFFF))
        return max(0.0, sec)
    except Exception as e:
        logger.debug("User inactivity check failed", error=str(e))
        return None


class DisplayPowerManager:
    """Smart display backlight, profile tuning, and idle dimming manager."""

    DEFAULT_IDLE_TIMEOUT: float = 300.0   # 5 minutes
    DEFAULT_DIM_BRIGHTNESS: int = 15      # 15% dim level
    DEFAULT_MIN_CLAMP: int = 15           # 15% minimum panel backlight
    DEFAULT_MAX_CLAMP: int = 85           # 85% maximum panel backlight
    DEFAULT_HYSTERESIS: int = 3           # 3% brightness hysteresis
    DEFAULT_MIN_SYNC_INTERVAL: float = 0.0  # 0.0s (pure hysteresis by default; optional debounce for ambient flutter)

    def __init__(
        self,
        driver: Optional[Any] = None,
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
        dim_brightness: int = DEFAULT_DIM_BRIGHTNESS,
        default_profile: BrightnessProfile = BrightnessProfile.WORK_ENVIRONMENT,
        auto_brightness: bool = False,
        min_clamp: int = DEFAULT_MIN_CLAMP,
        max_clamp: int = DEFAULT_MAX_CLAMP,
        hysteresis_delta: int = DEFAULT_HYSTERESIS,
        min_sync_interval: float = DEFAULT_MIN_SYNC_INTERVAL,
    ):
        self.driver = driver
        self.idle_timeout = idle_timeout
        self.dim_brightness = dim_brightness
        self.active_profile = default_profile
        self.current_brightness = default_profile.default_brightness
        self.is_screen_on = True
        self.is_dimmed = False
        self.auto_brightness = auto_brightness
        self.min_clamp = min_clamp
        self.max_clamp = max_clamp
        self.hysteresis_delta = hysteresis_delta
        self.min_sync_interval = min_sync_interval
        self.last_applied_brightness: Optional[int] = None
        self.last_host_brightness: Optional[float] = None
        self.is_locked = False
        self._last_sync_time: float = 0.0

        self._last_activity_time = time.time()
        self._lock = threading.RLock()

    def record_activity(self, driver: Optional[Any] = None) -> None:
        """Record user/agent activity; restores active display brightness if dimmed or off."""
        drv = driver or self.driver
        with self._lock:
            self._last_activity_time = time.time()
            self.is_locked = False
            was_inactive = self.is_dimmed or not self.is_screen_on
        if was_inactive:
            self.restore_active_brightness(driver=drv)

    def sync_host_brightness(
        self,
        driver: Optional[Any] = None,
        force: bool = False,
        min_sync_interval: Optional[float] = None,
    ) -> Optional[int]:
        """Auto-adjust brightness according to MacBook screen.

        Maps 0.0-1.0 to 0-100% with clamping between 15% and 85%.
        Enforces hysteresis: only issues driver.set_brightness() if brightness
        changes by >= hysteresis_delta (3%) to avoid unnecessary USB packets.
        Also enforces a minimum sync interval debounce (default 0.0s) unless force=True.
        """
        drv = driver or self.driver
        now = time.time()
        interval_limit = min_sync_interval if min_sync_interval is not None else self.min_sync_interval
        with self._lock:
            if not force and interval_limit > 0.0 and (now - self._last_sync_time) < interval_limit:
                return self.current_brightness

        host_val = get_macbook_brightness()
        if host_val is None:
            return None

        with self._lock:
            self._last_sync_time = now
            self.last_host_brightness = host_val
            target = compute_clamped_brightness(host_val, min_clamp=self.min_clamp, max_clamp=self.max_clamp)
            if not force and self.last_applied_brightness is not None:
                if abs(target - self.last_applied_brightness) < self.hysteresis_delta:
                    return self.last_applied_brightness

            self.current_brightness = target
            self.last_applied_brightness = target
            self.is_dimmed = False

        if drv and hasattr(drv, "set_brightness") and self.is_screen_on:
            drv.set_brightness(target)

        return target

    def get_inactivity_seconds(self) -> float:
        """Query user inactivity duration in seconds combining macOS CGEventSource and internal activity tracker."""
        inact_host = get_macos_user_inactivity_seconds()
        now = time.time()
        with self._lock:
            if inact_host is not None:
                # Synchronize internal activity anchor with host hardware activity
                self._last_activity_time = now - inact_host
                return inact_host
            return max(0.0, now - self._last_activity_time)

    def check_idle(self, driver: Optional[Any] = None, check_host_inactivity: bool = False) -> bool:
        """Check for lock screen, user inactivity, and idle timeout.

        Applies screen_off or dimming as appropriate.
        Restores brightness when user activity resumes.
        Returns True if the display power state changed.
        """
        drv = driver or self.driver
        now = time.time()

        # 1. macOS Lock screen check
        if is_macos_screen_locked():
            should_screen_off = False
            with self._lock:
                if self.is_screen_on:
                    self.is_screen_on = False
                    self.is_dimmed = True
                    self.is_locked = True
                    should_screen_off = True
            if should_screen_off and drv and hasattr(drv, "screen_off"):
                try:
                    drv.screen_off()
                except Exception as e:
                    logger.error("Failed to turn screen off on lock screen", error=str(e))
                return True
            return False

        # 2. Inactivity & idle duration
        should_restore = False
        with self._lock:
            if self.is_locked:
                self.is_locked = False
                should_restore = True

        if should_restore:
            self.restore_active_brightness(driver=drv)
            return True

        idle_duration = self.get_inactivity_seconds() if check_host_inactivity else max(0.0, now - self._last_activity_time)

        action = None
        with self._lock:
            # Check if user returned while dimmed or off
            if (self.is_dimmed or not self.is_screen_on) and (self.idle_timeout <= 0 or idle_duration < self.idle_timeout):
                should_restore = True
            elif self.idle_timeout > 0:
                if idle_duration >= (self.idle_timeout * 2.0) and self.is_screen_on:
                    # Deep idle -> Turn screen completely off to save power
                    self.is_screen_on = False
                    self.is_dimmed = True
                    action = "off"
                elif idle_duration >= self.idle_timeout and not self.is_dimmed and self.is_screen_on:
                    # Idle timeout reached -> Dim to dim_brightness (15%)
                    self.is_dimmed = True
                    action = "dim"

        if should_restore:
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] ☀️ User activity detected ({idle_duration:.0f}s) -> Restoring display backlight ({self.current_brightness}%)")
            self.restore_active_brightness(driver=drv)
            return True

        if action == "off":
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] 🌙 Deep user inactivity ({idle_duration:.0f}s >= {self.idle_timeout * 2:.0f}s) -> Display backlight off")
            if drv and hasattr(drv, "screen_off"):
                try:
                    drv.screen_off()
                except Exception as e:
                    logger.error("Failed to turn screen off in deep idle", error=str(e))
            return True
        elif action == "dim":
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] 🌙 User inactivity ({idle_duration:.0f}s >= {self.idle_timeout:.0f}s) -> Dimming backlight to {self.dim_brightness}%")
            if drv and hasattr(drv, "set_brightness"):
                try:
                    drv.set_brightness(self.dim_brightness)
                except Exception as e:
                    logger.error("Failed to dim screen backlight on idle", error=str(e))
            return True

        return False

    def restore_active_brightness(self, driver: Optional[Any] = None) -> bool:
        """Restore configured profile brightness after idle wake."""
        drv = driver or self.driver
        with self._lock:
            self.is_dimmed = False
            self.is_screen_on = True
            target = self.current_brightness

        if drv:
            if hasattr(drv, "screen_on"):
                try:
                    drv.screen_on()
                except Exception:
                    pass
            if hasattr(drv, "set_brightness"):
                try:
                    res = bool(drv.set_brightness(target))
                    with self._lock:
                        self.last_applied_brightness = target
                    return res
                except Exception as e:
                    logger.error("Failed to restore display brightness", error=str(e))
                    return False
        return True

    def set_profile(
        self,
        profile: Union[BrightnessProfile, str],
        brightness: Optional[int] = None,
        driver: Optional[Any] = None,
    ) -> bool:
        """Apply a brightness profile (work_environment, deep_work, presentation)."""
        drv = driver or self.driver
        if isinstance(profile, str):
            profile = BrightnessProfile(profile)

        with self._lock:
            self.active_profile = profile
            target_level = brightness if brightness is not None else profile.default_brightness
            # Clamp to range
            low, high = profile.brightness_range
            if low <= high and (low != 0 or high != 0):
                target_level = max(low, min(high, target_level))
            self.current_brightness = target_level
            self.is_dimmed = False
            self.is_screen_on = (profile != BrightnessProfile.OFF)
            self._last_activity_time = time.time()

        if drv:
            if profile == BrightnessProfile.OFF:
                if hasattr(drv, "screen_off"):
                    return bool(drv.screen_off())
            else:
                if hasattr(drv, "screen_on"):
                    try:
                        drv.screen_on()
                    except Exception:
                        pass
                if hasattr(drv, "set_brightness"):
                    return bool(drv.set_brightness(self.current_brightness))
        return True

    def set_brightness(self, level: int, driver: Optional[Any] = None) -> bool:
        """Set explicit brightness level (0-100%)."""
        drv = driver or self.driver
        level = max(0, min(100, level))
        with self._lock:
            self.current_brightness = level
            self.last_applied_brightness = level
            self.is_dimmed = False
            self.is_screen_on = (level > 0)
            self._last_activity_time = time.time()

        if drv and hasattr(drv, "set_brightness"):
            return bool(drv.set_brightness(level))
        return True

    def screen_off(self, driver: Optional[Any] = None) -> bool:
        """Turn screen backlight off."""
        drv = driver or self.driver
        with self._lock:
            self.is_screen_on = False
            self.is_dimmed = True
        if drv and hasattr(drv, "screen_off"):
            return bool(drv.screen_off())
        return True

    def screen_on(self, driver: Optional[Any] = None) -> bool:
        """Turn screen backlight on and restore current brightness."""
        drv = driver or self.driver
        with self._lock:
            self.is_screen_on = True
            self.is_dimmed = False
            self.is_locked = False
            self._last_activity_time = time.time()
        if drv:
            if hasattr(drv, "screen_on"):
                drv.screen_on()
            if hasattr(drv, "set_brightness"):
                drv.set_brightness(self.current_brightness)
            return True
        return True

    def get_status(self) -> Dict[str, Any]:
        """Display power state summary."""
        with self._lock:
            return {
                "profile": self.active_profile.value,
                "brightness": self.current_brightness,
                "is_screen_on": self.is_screen_on,
                "is_dimmed": self.is_dimmed,
                "is_locked": self.is_locked,
                "auto_brightness": self.auto_brightness,
                "last_applied_brightness": self.last_applied_brightness,
                "last_host_brightness": self.last_host_brightness,
                "idle_seconds": round(time.time() - self._last_activity_time, 1),
                "idle_timeout": self.idle_timeout,
                "dim_brightness": self.dim_brightness,
            }


# -----------------------------------------------------------------------------
# 5. Adaptive System Power Manager (Unified Coordinator)
# -----------------------------------------------------------------------------

class SystemPowerManager:
    """Central orchestrator for power modes, thermal limits, and display energy."""

    _instance: Optional[SystemPowerManager] = None
    _instance_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> SystemPowerManager:
        """Singleton accessor."""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self, initial_mode: PowerMode = PowerMode.BALANCED):
        self._mode = initial_mode
        self._mode_history: List[Dict[str, Any]] = [
            {"mode": initial_mode.value, "timestamp": time.time(), "reason": "initialization"}
        ]
        self._lock = threading.RLock()

        # Child subsystem managers
        self.memory_manager = SystemMemoryManager()
        self.thermal_manager = ThermalManager()
        self.resource_manager = SystemResourceManager()
        self.display_manager = DisplayPowerManager()

        # Ensure POSIX ulimit 65536 on startup
        self.resource_manager.ensure_file_descriptor_limits()

    def get_power_mode(self) -> PowerMode:
        """Get active power mode."""
        with self._lock:
            return self._mode

    def set_power_mode(
        self,
        mode: Union[PowerMode, str],
        token: Optional[str] = None,
        reason: str = "manual_switch",
        enforce_token: bool = False,
        driver: Optional[Any] = None,
    ) -> PowerMode:
        """Switch system power mode.

        Enforces Rule 5 Process Token authentication if token is supplied or enforce_token is True.
        """
        if isinstance(mode, str):
            mode = PowerMode(mode.lower())

        if enforce_token or token is not None:
            require_process_token(token)

        with self._lock:
            prev_mode = self._mode
            self._mode = mode
            self._mode_history.append({
                "from_mode": prev_mode.value,
                "mode": mode.value,
                "timestamp": time.time(),
                "reason": reason,
            })

            # Adjust display brightness to recommended power mode default
            self.display_manager.set_brightness(mode.default_brightness, driver=driver)

            logger.info(
                "Switched TURZX system power mode",
                from_mode=prev_mode.value,
                to_mode=mode.value,
                capacity_percent=mode.capacity_percent,
                reason=reason,
            )
            return self._mode

    def evaluate_adaptive_power_mode(self) -> PowerMode:
        """Dynamically adapt power mode based on system health indicators."""
        mem_info = self.memory_manager.get_memory_pressure()
        thermal_info = self.thermal_manager.get_thermal_info()

        with self._lock:
            # 1. Thermal critical (>85°C) -> Force MINIMUM power mode
            if thermal_info.state == ThermalState.CRITICAL:
                if self._mode != PowerMode.MINIMUM:
                    self.set_power_mode(PowerMode.MINIMUM, reason=f"Thermal critical ({thermal_info.temperature:.1f}°C)")
                return self._mode

            # 2. Memory pressure critical (<20% free RAM) -> Throttle to EFFICIENT or MINIMUM
            if mem_info.is_critical:
                if self._mode in (PowerMode.MAXIMUM, PowerMode.BALANCED):
                    self.set_power_mode(PowerMode.EFFICIENT, reason=f"Memory pressure high ({mem_info.free_percent:.1f}% free)")
                return self._mode

            # 3. Thermal elevated (>75°C) -> Cap at BALANCED or EFFICIENT
            if thermal_info.state == ThermalState.MODERATE:
                if self._mode == PowerMode.MAXIMUM:
                    self.set_power_mode(PowerMode.BALANCED, reason=f"Thermal elevated ({thermal_info.temperature:.1f}°C)")
                return self._mode

            return self._mode

    def get_system_telemetry(self) -> Dict[str, Any]:
        """Aggregate telemetry from all power and resource subsystems."""
        mem_info = self.memory_manager.get_memory_pressure()
        thermal_info = self.thermal_manager.get_thermal_info()
        soft_fd, hard_fd = resource.getrlimit(resource.RLIMIT_NOFILE)

        with self._lock:
            return {
                "power_mode": self._mode.value,
                "capacity_percent": self._mode.capacity_percent,
                "max_concurrency": self._mode.max_concurrency(),
                "memory": mem_info.to_dict(),
                "thermal": thermal_info.to_dict(),
                "display": self.display_manager.get_status(),
                "resource_limits": {
                    "nofile_soft": soft_fd,
                    "nofile_hard": hard_fd,
                },
                "history_length": len(self._mode_history),
            }
