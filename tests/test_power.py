"""
Tests for TURZX System Power & Display Optimization Module.

Verifies:
1. Memory Management & awk-based memory pressure check (threshold: 20% free RAM triggers throttling).
2. Screen Brightness & Power Tuning (work 50-60%, deep work 30-40%, presentation 70-80%).
3. System Resource Management & file descriptor limits (ulimit -n 65536).
4. Thermal Management & CPU throttling (>85°C critical, >75°C moderate).
5. Adaptive Power Modes (maximum 100%, balanced 70%, efficient 40%, minimum 20%).
6. Smart Display Management (idle dimming to 15%, deep idle screen-off, activity restoration).
7. Intelligent Resource Allocation & admission gating.
8. Rule 5 Process Token Authentication for power control.
"""

import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest

from turzx.auth import PermissionDeniedError, clear_process_token, generate_process_token
from turzx.power import (
    BrightnessProfile,
    DisplayPowerManager,
    MemoryPressureInfo,
    PowerMode,
    SystemMemoryManager,
    SystemPowerManager,
    SystemResourceManager,
    TaskPowerCategory,
    TaskUrgency,
    ThermalInfo,
    ThermalManager,
    ThermalState,
    compute_clamped_brightness,
    get_macbook_brightness,
    get_macos_user_inactivity_seconds,
    is_macos_screen_locked,
)
from turzx.cli import main as cli_main


# -----------------------------------------------------------------------------
# 1. Power Modes & Brightness Profiles
# -----------------------------------------------------------------------------

class TestPowerModesAndProfiles:
    """Verify PowerMode and BrightnessProfile constants and methods."""

    def test_power_mode_capacities(self):
        assert PowerMode.MAXIMUM.capacity_percent == 100
        assert PowerMode.BALANCED.capacity_percent == 70
        assert PowerMode.EFFICIENT.capacity_percent == 40
        assert PowerMode.MINIMUM.capacity_percent == 20

    def test_power_mode_concurrency_scaling(self):
        # Base 10 concurrency (Rule 4 Resource Boundary)
        assert PowerMode.MAXIMUM.max_concurrency(10) == 10
        assert PowerMode.BALANCED.max_concurrency(10) == 7
        assert PowerMode.EFFICIENT.max_concurrency(10) == 4
        assert PowerMode.MINIMUM.max_concurrency(10) == 2

        # Enforces hard upper limit of 10 even if base is larger
        assert PowerMode.MAXIMUM.max_concurrency(50) == 10

    def test_power_mode_default_brightness(self):
        assert PowerMode.MAXIMUM.default_brightness == 80
        assert PowerMode.BALANCED.default_brightness == 60
        assert PowerMode.EFFICIENT.default_brightness == 40
        assert PowerMode.MINIMUM.default_brightness == 20

    def test_power_mode_throttle_delay(self):
        assert PowerMode.MAXIMUM.throttle_delay == 0.0
        assert PowerMode.BALANCED.throttle_delay == 0.05
        assert PowerMode.EFFICIENT.throttle_delay == 0.2
        assert PowerMode.MINIMUM.throttle_delay == 0.5

    def test_brightness_profiles(self):
        # Work Environment: 50-60%, default 55%
        assert BrightnessProfile.WORK_ENVIRONMENT.default_brightness == 55
        assert BrightnessProfile.WORK_ENVIRONMENT.brightness_range == (50, 60)

        # Deep Work: 30-40%, default 35%
        assert BrightnessProfile.DEEP_WORK.default_brightness == 35
        assert BrightnessProfile.DEEP_WORK.brightness_range == (30, 40)

        # Presentation: 70-80%, default 75%
        assert BrightnessProfile.PRESENTATION.default_brightness == 75
        assert BrightnessProfile.PRESENTATION.brightness_range == (70, 80)

        # Idle & Off
        assert BrightnessProfile.IDLE.default_brightness == 15
        assert BrightnessProfile.OFF.default_brightness == 0


# -----------------------------------------------------------------------------
# 2. System Memory Management
# -----------------------------------------------------------------------------

class TestSystemMemoryManager:
    """Verify memory pressure checks with awk expression and numeric comparisons."""

    def test_awk_command_syntax(self):
        cmd = SystemMemoryManager.AWK_CHECK_CMD
        assert "memory_pressure" in cmd
        assert "System-wide memory free percentage:" in cmd
        assert "($NF < 20) ? 1 : 0" in cmd

    def test_memory_pressure_parsing_healthy(self):
        mgr = SystemMemoryManager(threshold_percent=20.0)
        sample_output = (
            "The system has 17179869184 pages.\n"
            "System-wide memory free percentage: 46%\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=sample_output)
            with patch("platform.system", return_value="Darwin"):
                info = mgr.get_memory_pressure()
                assert info.free_percent == 46.0
                assert not info.is_critical
                assert not mgr.is_memory_pressure_high()

    def test_memory_pressure_parsing_critical_threshold(self):
        # 18% free RAM is < 20% threshold -> triggers fail-closed / throttling
        mgr = SystemMemoryManager(threshold_percent=20.0)
        sample_output = (
            "The system has 17179869184 pages.\n"
            "System-wide memory free percentage: 18%\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=sample_output)
            with patch("platform.system", return_value="Darwin"):
                info = mgr.get_memory_pressure()
                assert info.free_percent == 18.0
                assert info.is_critical
                assert mgr.is_memory_pressure_high()

    def test_execute_awk_check_returns_boolean(self):
        mgr = SystemMemoryManager()
        with patch("subprocess.run") as mock_run:
            # 1 indicates high pressure (< 20%)
            mock_run.return_value = MagicMock(returncode=0, stdout="1\n")
            assert mgr.execute_awk_check() is True

            # 0 indicates healthy (>= 20%)
            mock_run.return_value = MagicMock(returncode=0, stdout="0\n")
            assert mgr.execute_awk_check() is False

    def test_awk_check_fail_closed_on_error(self):
        mgr = SystemMemoryManager()
        with patch("subprocess.run", side_effect=OSError("Command failed")):
            # Rule 2: Fail-closed returns True (high pressure / safe throttle)
            assert mgr.execute_awk_check() is True


# -----------------------------------------------------------------------------
# 3. Thermal Management & Throttling
# -----------------------------------------------------------------------------

class TestThermalManager:
    """Verify thermal tracking, categorization, and CPU throttle scaling."""

    def test_thermal_classification_normal(self):
        tm = ThermalManager()
        tm.set_simulated_temperature(65.0)
        info = tm.get_thermal_info()
        assert info.temperature == 65.0
        assert info.state == ThermalState.NORMAL
        assert not info.is_throttled
        assert tm.calculate_throttle_factor(65.0) == 1.0

    def test_thermal_classification_moderate(self):
        # > 75°C triggers moderate throttling
        tm = ThermalManager()
        tm.set_simulated_temperature(78.5)
        info = tm.get_thermal_info()
        assert info.temperature == 78.5
        assert info.state == ThermalState.MODERATE
        assert info.is_throttled
        assert tm.calculate_throttle_factor(78.5) == 0.5

    def test_thermal_classification_critical(self):
        # > 85°C triggers severe power/frequency throttling
        tm = ThermalManager()
        tm.set_simulated_temperature(89.2)
        info = tm.get_thermal_info()
        assert info.temperature == 89.2
        assert info.state == ThermalState.CRITICAL
        assert info.is_throttled
        assert tm.calculate_throttle_factor(89.2) == 0.2


# -----------------------------------------------------------------------------
# 4. System Resource Management & Dynamic Allocation Gate
# -----------------------------------------------------------------------------

class TestSystemResourceManager:
    """Verify file descriptor limits and dynamic task admission gating."""

    def test_file_descriptor_limits(self):
        soft, hard = SystemResourceManager.ensure_file_descriptor_limits(65536)
        assert soft >= 65536 or soft == hard

    def test_task_admission_normal_conditions(self):
        admitted, reason = SystemResourceManager.can_admit_task(
            task_power=TaskPowerCategory.POWER_CRITICAL,
            urgency=TaskUrgency.HIGH,
            memory_free_pct=50.0,
            thermal_temp=60.0,
            power_mode=PowerMode.BALANCED,
        )
        assert admitted
        assert reason == "Task admitted"

    def test_task_admission_memory_pressure_rejection(self):
        # When free RAM < 20%, power-critical tasks without critical urgency are rejected
        admitted, reason = SystemResourceManager.can_admit_task(
            task_power=TaskPowerCategory.POWER_CRITICAL,
            urgency=TaskUrgency.HIGH,
            memory_free_pct=15.0,  # < 20%
            thermal_temp=60.0,
            power_mode=PowerMode.BALANCED,
        )
        assert not admitted
        assert "Memory pressure high" in reason

        # But critical urgency task is admitted even under memory pressure
        admitted_crit, _ = SystemResourceManager.can_admit_task(
            task_power=TaskPowerCategory.POWER_CRITICAL,
            urgency=TaskUrgency.CRITICAL,
            memory_free_pct=15.0,
            thermal_temp=60.0,
            power_mode=PowerMode.BALANCED,
        )
        assert admitted_crit

    def test_task_admission_thermal_critical_rejection(self):
        # > 85°C rejects power-critical tasks
        admitted, reason = SystemResourceManager.can_admit_task(
            task_power=TaskPowerCategory.POWER_CRITICAL,
            urgency=TaskUrgency.CRITICAL,
            memory_free_pct=50.0,
            thermal_temp=89.0,  # > 85°C
            power_mode=PowerMode.BALANCED,
        )
        assert not admitted
        assert "Thermal critical" in reason

    def test_task_admission_minimum_power_mode(self):
        # Minimum power mode admits only high/critical urgency
        admitted_low, reason = SystemResourceManager.can_admit_task(
            task_power=TaskPowerCategory.POWER_CRITICAL,
            urgency=TaskUrgency.LOW,
            memory_free_pct=50.0,
            thermal_temp=60.0,
            power_mode=PowerMode.MINIMUM,
        )
        assert not admitted_low
        assert "Minimum power mode active" in reason


# -----------------------------------------------------------------------------
# 5. Display Power Manager & Smart Idle Dimming
# -----------------------------------------------------------------------------

class TestDisplayPowerManager:
    """Verify brightness profile tuning, idle dimming (15%), and screen on/off."""

    def test_profile_setting(self):
        mock_driver = MagicMock()
        mgr = DisplayPowerManager(driver=mock_driver)

        # Set work environment
        assert mgr.set_profile(BrightnessProfile.WORK_ENVIRONMENT)
        assert mgr.current_brightness == 55
        mock_driver.set_brightness.assert_called_with(55)

        # Set deep work
        assert mgr.set_profile(BrightnessProfile.DEEP_WORK)
        assert mgr.current_brightness == 35
        mock_driver.set_brightness.assert_called_with(35)

        # Set presentation
        assert mgr.set_profile(BrightnessProfile.PRESENTATION)
        assert mgr.current_brightness == 75
        mock_driver.set_brightness.assert_called_with(75)

    def test_idle_dimming_after_timeout(self):
        mock_driver = MagicMock()
        mgr = DisplayPowerManager(
            driver=mock_driver,
            idle_timeout=5.0,     # 5 seconds for test
            dim_brightness=15,
        )
        mgr.current_brightness = 60

        # Initially active
        assert not mgr.is_dimmed

        # Advance time by 6 seconds (> idle_timeout 5.0)
        mgr._last_activity_time = time.time() - 6.0
        changed = mgr.check_idle()
        assert changed
        assert mgr.is_dimmed
        mock_driver.set_brightness.assert_called_with(15)

    def test_deep_idle_turns_screen_off(self):
        mock_driver = MagicMock()
        mgr = DisplayPowerManager(
            driver=mock_driver,
            idle_timeout=5.0,
            dim_brightness=15,
        )
        mgr.current_brightness = 60

        # Advance time past 2x idle_timeout (11 seconds)
        mgr._last_activity_time = time.time() - 11.0
        changed = mgr.check_idle()
        assert changed
        assert not mgr.is_screen_on
        mock_driver.screen_off.assert_called_once()

    def test_record_activity_restores_brightness(self):
        mock_driver = MagicMock()
        mgr = DisplayPowerManager(
            driver=mock_driver,
            idle_timeout=5.0,
            dim_brightness=15,
        )
        mgr.current_brightness = 60
        mgr.is_dimmed = True
        mgr.is_screen_on = False

        # Activity detected
        mgr.record_activity()
        assert not mgr.is_dimmed
        assert mgr.is_screen_on
        mock_driver.screen_on.assert_called_once()
        mock_driver.set_brightness.assert_called_with(60)

    def test_clamped_brightness_mapping(self):
        # 15% - 85% clamping
        assert compute_clamped_brightness(0.0) == 15
        assert compute_clamped_brightness(0.05) == 15
        assert compute_clamped_brightness(0.14) == 15
        assert compute_clamped_brightness(0.15) == 15
        assert compute_clamped_brightness(0.50) == 50
        assert compute_clamped_brightness(0.72) == 72
        assert compute_clamped_brightness(0.85) == 85
        assert compute_clamped_brightness(0.95) == 85
        assert compute_clamped_brightness(1.0) == 85

    def test_auto_brightness_hysteresis_enforcement(self):
        mock_driver = MagicMock()
        mgr = DisplayPowerManager(
            driver=mock_driver,
            auto_brightness=True,
            hysteresis_delta=3,
        )

        # First query: 50%
        with patch("turzx.power.get_macbook_brightness", return_value=0.50):
            val = mgr.sync_host_brightness()
            assert val == 50
            mock_driver.set_brightness.assert_called_with(50)
            assert mock_driver.set_brightness.call_count == 1

        # Small 1% shift (51%): less than 3% hysteresis -> driver write suppressed
        with patch("turzx.power.get_macbook_brightness", return_value=0.51):
            val = mgr.sync_host_brightness()
            assert val == 50  # Keeps existing applied value
            assert mock_driver.set_brightness.call_count == 1

        # Another 1% shift (52%): still only 2% delta from 50 -> suppressed
        with patch("turzx.power.get_macbook_brightness", return_value=0.52):
            val = mgr.sync_host_brightness()
            assert val == 50
            assert mock_driver.set_brightness.call_count == 1

        # 4% shift (54%): >= 3% hysteresis -> driver write issued!
        with patch("turzx.power.get_macbook_brightness", return_value=0.54):
            val = mgr.sync_host_brightness()
            assert val == 54
            mock_driver.set_brightness.assert_called_with(54)
            assert mock_driver.set_brightness.call_count == 2

        # Forced update (55%): forces write even though delta is only 1%
        with patch("turzx.power.get_macbook_brightness", return_value=0.55):
            val = mgr.sync_host_brightness(force=True)
            assert val == 55
            mock_driver.set_brightness.assert_called_with(55)
            assert mock_driver.set_brightness.call_count == 3

    def test_screen_lock_detection_invokes_screen_off(self):
        mock_driver = MagicMock()
        mgr = DisplayPowerManager(driver=mock_driver)
        assert mgr.is_screen_on

        # Lock screen is active
        with patch("turzx.power.is_macos_screen_locked", return_value=True):
            changed = mgr.check_idle()
            assert changed
            assert not mgr.is_screen_on
            assert mgr.is_locked
            mock_driver.screen_off.assert_called_once()

        # While locked, check_idle returns False (already off)
        with patch("turzx.power.is_macos_screen_locked", return_value=True):
            changed = mgr.check_idle()
            assert not changed

        # Unlocking and activity restores screen
        with patch("turzx.power.is_macos_screen_locked", return_value=False):
            mgr.record_activity()
            assert mgr.is_screen_on
            assert not mgr.is_locked
            mock_driver.screen_on.assert_called_once()

    def test_user_inactivity_query(self):
        mock_driver = MagicMock()
        mgr = DisplayPowerManager(driver=mock_driver)
        with patch("turzx.power.get_macos_user_inactivity_seconds", return_value=42.5):
            inact = mgr.get_inactivity_seconds()
            assert inact >= 42.5

    def test_inactivity_deadlock_prevention_on_user_return(self):
        """Verify that when user returns after idle period, inactivity duration synchronizes
        and check_idle restores active brightness without deadlocking."""
        mock_driver = MagicMock()
        mgr = DisplayPowerManager(
            driver=mock_driver,
            idle_timeout=300.0,
            dim_brightness=15,
        )
        mgr.current_brightness = 60
        mgr.is_dimmed = True
        # Simulate 320 seconds idle in internal timer
        mgr._last_activity_time = time.time() - 320.0

        # Host hardware event source indicates user returned 0.5s ago!
        with patch("turzx.power.get_macos_user_inactivity_seconds", return_value=0.5):
            inact = mgr.get_inactivity_seconds()
            assert inact == 0.5
            # check_idle detects user activity and restores screen
            changed = mgr.check_idle(check_host_inactivity=True)
            assert changed
            assert not mgr.is_dimmed
            assert mgr.is_screen_on
            mock_driver.set_brightness.assert_called_with(60)

    def test_auto_brightness_debounce_rate_limiting(self):
        """Verify that rapid ambient light fluctuations within min_sync_interval are debounced."""
        mock_driver = MagicMock()
        mgr = DisplayPowerManager(
            driver=mock_driver,
            auto_brightness=True,
            hysteresis_delta=3,
            min_sync_interval=0.5,
        )

        # 1. First sync at 50%
        with patch("turzx.power.get_macbook_brightness", return_value=0.50):
            res = mgr.sync_host_brightness()
            assert res == 50
            assert mock_driver.set_brightness.call_count == 1

        # 2. Rapid ambient shift to 65% only 0.1s later (within 0.5s debounce interval) -> debounced
        with patch("turzx.power.get_macbook_brightness", return_value=0.65):
            res = mgr.sync_host_brightness()
            assert res == 50  # Keeps previous, driver not spammed
            assert mock_driver.set_brightness.call_count == 1

        # 3. Advance last sync time past 0.5s debounce -> sync proceeds!
        mgr._last_sync_time = time.time() - 0.6
        with patch("turzx.power.get_macbook_brightness", return_value=0.65):
            res = mgr.sync_host_brightness()
            assert res == 65
            assert mock_driver.set_brightness.call_count == 2

        # 4. Forced sync bypasses debounce immediately
        with patch("turzx.power.get_macbook_brightness", return_value=0.75):
            res = mgr.sync_host_brightness(force=True)
            assert res == 75
            assert mock_driver.set_brightness.call_count == 3

    def test_get_macbook_brightness_prefers_builtin_display(self):
        """Verify get_macbook_brightness locates built-in MacBook panel in multi-display setups."""
        import ctypes
        from unittest.mock import MagicMock

        mock_cg = MagicMock()
        mock_cg.CGMainDisplayID.return_value = 999  # External display is primary

        def fake_get_active_list(max_displays, array_ptr, count_ptr):
            count_ptr._obj.value = 2
            array_ptr[0] = 999  # External
            array_ptr[1] = 1    # Built-in MacBook panel
            return 0

        mock_cg.CGGetActiveDisplayList.side_effect = fake_get_active_list
        mock_cg.CGDisplayIsBuiltin.side_effect = lambda d_id: (d_id == 1)

        mock_ds = MagicMock()
        requested_displays = []

        def fake_ds_brightness(d_id, b_ptr):
            requested_displays.append(d_id)
            b_ptr._obj.value = 0.62
            return 0

        mock_ds.DisplayServicesGetBrightness.side_effect = fake_ds_brightness

        with patch("platform.system", return_value="Darwin"):
            with patch("ctypes.cdll.LoadLibrary") as mock_load:
                def fake_load(path):
                    if "CoreGraphics" in path:
                        return mock_cg
                    if "DisplayServices" in path:
                        return mock_ds
                    return MagicMock()

                mock_load.side_effect = fake_load
                val = get_macbook_brightness()
                assert val == pytest.approx(0.62, abs=0.01)
                assert 1 in requested_displays  # Built-in display 1 was queried!


# -----------------------------------------------------------------------------
# 6. SystemPowerManager Unified Coordinator & Token Auth
# -----------------------------------------------------------------------------

class TestSystemPowerManager:
    """Verify central power coordinator, token authentication, and adaptive switching."""

    def setup_method(self):
        clear_process_token()

    def teardown_method(self):
        clear_process_token()

    def test_singleton_instance(self):
        inst1 = SystemPowerManager.get_instance()
        inst2 = SystemPowerManager.get_instance()
        assert inst1 is inst2

    def test_power_mode_switching(self):
        mgr = SystemPowerManager.get_instance()
        mgr.set_power_mode(PowerMode.MAXIMUM)
        assert mgr.get_power_mode() == PowerMode.MAXIMUM
        assert mgr.get_power_mode().capacity_percent == 100

        mgr.set_power_mode(PowerMode.EFFICIENT)
        assert mgr.get_power_mode() == PowerMode.EFFICIENT
        assert mgr.get_power_mode().capacity_percent == 40

        # Reset back to balanced
        mgr.set_power_mode(PowerMode.BALANCED)

    def test_process_token_authentication_enforcement(self):
        mgr = SystemPowerManager.get_instance()
        valid_token = generate_process_token(force_regenerate=True)

        # Valid token succeeds
        mgr.set_power_mode(PowerMode.EFFICIENT, token=valid_token, enforce_token=True)
        assert mgr.get_power_mode() == PowerMode.EFFICIENT

        # Invalid token fails (Rule 5 Fail-Closed)
        with pytest.raises(PermissionDeniedError):
            mgr.set_power_mode(PowerMode.MAXIMUM, token="invalid_token_12345", enforce_token=True)

        # Clean reset
        mgr.set_power_mode(PowerMode.BALANCED, token=valid_token, enforce_token=True)

    def test_adaptive_power_mode_thermal_critical(self):
        mgr = SystemPowerManager.get_instance()
        mgr.set_power_mode(PowerMode.MAXIMUM)

        # Inject simulated thermal temperature of 90°C (>85°C critical)
        mgr.thermal_manager.set_simulated_temperature(90.0)
        adapted = mgr.evaluate_adaptive_power_mode()
        assert adapted == PowerMode.MINIMUM
        assert mgr.get_power_mode() == PowerMode.MINIMUM

        # Clean reset
        mgr.thermal_manager.set_simulated_temperature(None)
        mgr.set_power_mode(PowerMode.BALANCED)

    def test_adaptive_power_mode_memory_critical(self):
        mgr = SystemPowerManager.get_instance()
        mgr.set_power_mode(PowerMode.MAXIMUM)

        # Mock high memory pressure (<20% free RAM)
        with patch.object(
            mgr.memory_manager,
            "get_memory_pressure",
            return_value=MemoryPressureInfo(free_percent=12.0, is_critical=True),
        ):
            adapted = mgr.evaluate_adaptive_power_mode()
            assert adapted == PowerMode.EFFICIENT
            assert mgr.get_power_mode() == PowerMode.EFFICIENT

        # Clean reset
        mgr.set_power_mode(PowerMode.BALANCED)

    def test_telemetry_aggregation(self):
        mgr = SystemPowerManager.get_instance()
        telemetry = mgr.get_system_telemetry()
        assert "power_mode" in telemetry
        assert "capacity_percent" in telemetry
        assert "memory" in telemetry
        assert "thermal" in telemetry
        assert "display" in telemetry
        assert "resource_limits" in telemetry


# -----------------------------------------------------------------------------
# 7. CLI Power & Display Commands Integration
# -----------------------------------------------------------------------------

class TestCLIPowerCommands:
    """Verify CLI integration for power-mode, brightness --profile, and resource-status."""

    def test_cli_resource_status(self, capsys):
        ret = cli_main(["resource-status"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "TURZX Adaptive Power & Resource Status" in captured.out
        assert "Active Mode:" in captured.out

    def test_cli_power_mode_status(self, capsys):
        ret = cli_main(["power-mode"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "Active Mode:" in captured.out

    def test_cli_power_mode_switch(self, capsys):
        ret = cli_main(["power-mode", "efficient"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "Power mode switched to EFFICIENT" in captured.out

        # Reset
        cli_main(["power-mode", "balanced"])

    def test_cli_brightness_profile_work(self, monkeypatch):
        mock_driver = MagicMock()
        mock_driver.connect.return_value = True
        monkeypatch.setattr("turzx.cli.get_driver_from_args", lambda args: mock_driver)

        ret = cli_main(["brightness", "--profile", "work_environment"])
        assert ret == 0
        mock_driver.set_brightness.assert_called_with(55)

    def test_cli_brightness_profile_deep_work(self, monkeypatch):
        mock_driver = MagicMock()
        mock_driver.connect.return_value = True
        monkeypatch.setattr("turzx.cli.get_driver_from_args", lambda args: mock_driver)

        ret = cli_main(["brightness", "--profile", "deep_work"])
        assert ret == 0
        mock_driver.set_brightness.assert_called_with(35)

    def test_cli_brightness_profile_presentation(self, monkeypatch):
        mock_driver = MagicMock()
        mock_driver.connect.return_value = True
        monkeypatch.setattr("turzx.cli.get_driver_from_args", lambda args: mock_driver)

        ret = cli_main(["brightness", "--profile", "presentation"])
        assert ret == 0
        mock_driver.set_brightness.assert_called_with(75)

    def test_cli_brightness_auto(self, monkeypatch):
        mock_driver = MagicMock()
        mock_driver.connect.return_value = True
        monkeypatch.setattr("turzx.cli.get_driver_from_args", lambda args: mock_driver)

        with patch("turzx.cli.get_macbook_brightness", return_value=0.60):
            ret = cli_main(["brightness", "--auto"])
            assert ret == 0
            mock_driver.set_brightness.assert_called_with(60)
