"""
Unit tests for TURZX Carousel Telemetry Display runner (turzx_carousel.py).
Tests real-time SIGUSR1 signal handling, reload event triggering, and loop responsiveness.
"""

import signal
import threading
from pathlib import Path
from unittest.mock import MagicMock

from turzx_carousel import (
    handle_sigusr1,
    reload_event,
    run_carousel,
    setup_signal_handlers,
    write_pid_file,
    cleanup_pid_file,
    PID_FILE,
)


def test_handle_sigusr1_sets_reload_event():
    """Verify handle_sigusr1 sets the reload_event."""
    reload_event.clear()
    assert not reload_event.is_set()

    handle_sigusr1(signal.SIGUSR1, None)
    assert reload_event.is_set()

    reload_event.clear()


def test_pid_file_lifecycle(tmp_path: Path):
    """Verify PID file writing and cleanup."""
    test_pid_file = tmp_path / "test_carousel.pid"

    write_pid_file(str(test_pid_file))
    assert test_pid_file.is_file()
    assert int(test_pid_file.read_text(encoding="utf-8").strip()) > 0

    cleanup_pid_file(str(test_pid_file))
    assert not test_pid_file.exists()


def test_run_carousel_with_mock_driver_and_stop_event(tmp_path: Path):
    """Verify run_carousel executes loop, handles signal reload, and stops cleanly."""
    mock_driver = MagicMock()
    mock_driver.connect.return_value = True
    mock_driver.display_image.return_value = True

    test_pid_file = tmp_path / "turzx-carousel.pid"
    stop_event = threading.Event()

    # Pre-set reload event to simulate a signal received
    reload_event.set()

    # Let loop run one cycle then stop
    def stop_after_delay():
        import time
        time.sleep(0.05)
        stop_event.set()
        reload_event.set()

    stopper = threading.Thread(target=stop_after_delay)
    stopper.start()

    run_carousel(
        interval=0.1,
        brightness=50,
        driver=mock_driver,
        stop_event=stop_event,
        pid_file=str(test_pid_file),
    )
    stopper.join()

    assert mock_driver.display_image.called
    assert mock_driver.set_brightness.called
    assert not test_pid_file.exists()


def test_run_carousel_single_page(tmp_path: Path):
    """Verify run_carousel single page mode displays only requested page and exits."""
    mock_driver = MagicMock()
    mock_driver.display_image.return_value = True

    test_pid_file = tmp_path / "single_page.pid"

    run_carousel(
        interval=10.0,
        brightness=70,
        single_page=0,
        driver=mock_driver,
        pid_file=str(test_pid_file),
    )

    assert mock_driver.display_image.call_count == 1
    assert not test_pid_file.exists()


def test_setup_signal_handlers_thread_safety():
    """Verify setup_signal_handlers handles invocation from worker threads gracefully."""
    errors = []

    def worker():
        try:
            setup_signal_handlers()
        except Exception as e:
            errors.append(e)

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    assert len(errors) == 0, f"setup_signal_handlers raised exception in thread: {errors}"


def test_carousel_adaptive_pagination_switching(tmp_path: Path):
    """
    Verify carousel pagination adaptively switches:
    - 3 pages when total agents <= 8
    - 4 pages (Page 0a: AGY Overview, Page 0b: Codex Overview) when total agents > 8
    - reverts back to 3 pages when dynamic agent is removed.
    """
    from generate_carousel_pages import get_carousel_pages
    from turzx.provisioner import AgentProvisioner

    reg_file = tmp_path / "registry.json"
    acc_dir = tmp_path / "accounts"
    prov = AgentProvisioner(registry_path=str(reg_file), accounts_root=str(acc_dir))

    # 1. Baseline: 8 seed agents -> 3 pages
    pages_3 = get_carousel_pages(prov=prov)
    assert len(pages_3) == 3
    assert pages_3[0][0] == "Page 0: Cluster Overview"
    assert pages_3[1][0] == "Page 1: AGY Cluster Deep-Dive"
    assert pages_3[2][0] == "Page 2: Codex Cluster Deep-Dive"

    for title, renderer in pages_3:
        img = renderer()
        assert img.size == (320, 480)

    # 2. Add dynamic agent -> 9 agents -> 4 pages
    dyn = prov.add_agent(cluster="agy", name="AGY6 Dynamic", preset="gemini_focus")
    pages_4 = get_carousel_pages(prov=prov)
    assert len(pages_4) == 4
    assert pages_4[0][0] == "Page 0a: AGY Cluster Overview"
    assert pages_4[1][0] == "Page 0b: Codex Cluster Overview"
    assert pages_4[2][0] == "Page 1: AGY Cluster Deep-Dive"
    assert pages_4[3][0] == "Page 2: Codex Cluster Deep-Dive"

    for title, renderer in pages_4:
        img = renderer()
        assert img.size == (320, 480)

    # 3. Remove dynamic agent -> back to 8 agents -> 3 pages
    prov.remove_agent("agy", dyn["id"])
    pages_revert = get_carousel_pages(prov=prov)
    assert len(pages_revert) == 3
    assert pages_revert[0][0] == "Page 0: Cluster Overview"


def test_run_carousel_sigusr1_interrupt_immediate_refresh(tmp_path: Path):
    """
    Verify that receiving SIGUSR1 interrupts a long interval sleep immediately,
    re-evaluates pages, and pushes updated RGB565 frames.
    """
    import time
    from turzx.provisioner import AgentProvisioner

    reg_file = tmp_path / "registry.json"
    acc_dir = tmp_path / "accounts"
    prov = AgentProvisioner(registry_path=str(reg_file), accounts_root=str(acc_dir))

    frames_written = []
    mock_driver = MagicMock()
    mock_driver.connect.return_value = True

    def record_frame(raw_bytes):
        frames_written.append(len(raw_bytes))
        return True

    mock_driver.display_image.side_effect = record_frame

    test_pid_file = tmp_path / "turzx-carousel.pid"
    stop_event = threading.Event()

    start_time = time.time()

    def signal_and_stop():
        # Wait a fraction of a second to ensure loop has started and entered wait
        time.sleep(0.08)
        # Add dynamic agent
        prov.add_agent("agy", "AGY6 Dynamic")
        # Trigger SIGUSR1 handler
        handle_sigusr1(signal.SIGUSR1, None)
        # Give carousel thread a moment to process the interrupt and render
        time.sleep(0.08)
        stop_event.set()
        reload_event.set()

    worker = threading.Thread(target=signal_and_stop)
    worker.start()

    run_carousel(
        interval=5.0,  # 5s interval would take seconds if not interrupted
        brightness=80,
        driver=mock_driver,
        stop_event=stop_event,
        pid_file=str(test_pid_file),
        provisioner=prov,
    )
    worker.join()

    elapsed = time.time() - start_time
    assert elapsed < 3.0, f"Carousel wait was not interrupted by SIGUSR1 (elapsed: {elapsed:.2f}s)"
    assert len(frames_written) >= 2, "Carousel must render at least initial frame and interrupted frame"
    for frame_len in frames_written:
        assert frame_len == 320 * 480 * 2, f"Expected RGB565 frame size 307200, got {frame_len}"


def test_carousel_auto_reconnect_on_protocol_error(tmp_path: Path):
    """
    Verify that when display_image raises TURZXProtocolError (e.g. transient USB drop),
    the carousel runner catches it, initiates Incident Runbook 1 reconnect,
    safely disconnects, reconnects, re-applies brightness, and resumes operation without crashing.
    """
    import time
    from turzx.protocol import TURZXProtocolError

    mock_driver = MagicMock()
    mock_driver.connect.return_value = True

    call_count = 0

    def flaky_display_image(raw_bytes):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise TURZXProtocolError("Command transmission failed: [Errno 19] No such device")
        return True

    mock_driver.display_image.side_effect = flaky_display_image

    test_pid_file = tmp_path / "turzx-carousel-reconnect.pid"
    stop_event = threading.Event()

    def stop_after_recovery():
        # Wait until recovery happens and next cycle proceeds
        for _ in range(50):
            time.sleep(0.05)
            if call_count >= 2:
                break
        stop_event.set()
        reload_event.set()

    worker = threading.Thread(target=stop_after_recovery)
    worker.start()

    run_carousel(
        interval=0.1,
        brightness=80,
        driver=mock_driver,
        stop_event=stop_event,
        pid_file=str(test_pid_file),
        reconnect_interval=0.02,
    )
    worker.join()

    assert mock_driver.disconnect.called, "Driver disconnect must be called upon USB error"
    assert mock_driver.connect.called, "Driver connect must be re-invoked during recovery"
    assert mock_driver.set_brightness.call_count >= 2, "Brightness must be re-applied after reconnection"
    assert call_count >= 2, f"Expected at least 2 display_image attempts, got {call_count}"
    assert not test_pid_file.exists()


def test_carousel_auto_reconnect_retry_loop_until_stop(tmp_path: Path):
    """
    Verify that when hardware remains disconnected, the reconnect loop continues retrying
    without raising an uncaught exception, and exits cleanly upon stop_event.
    """
    import time
    from turzx.protocol import TURZXProtocolError

    mock_driver = MagicMock()
    # Initial call to display fails
    mock_driver.display_image.side_effect = TURZXProtocolError("Device unplugged")
    # Reconnection attempts fail initially
    mock_driver.connect.return_value = False

    test_pid_file = tmp_path / "turzx-carousel-unplugged.pid"
    stop_event = threading.Event()

    def stop_after_retries():
        time.sleep(0.1)
        stop_event.set()
        reload_event.set()

    worker = threading.Thread(target=stop_after_retries)
    worker.start()

    run_carousel(
        interval=0.1,
        brightness=80,
        driver=mock_driver,
        stop_event=stop_event,
        pid_file=str(test_pid_file),
        reconnect_interval=0.02,
    )
    worker.join()

    assert mock_driver.connect.called
    assert not test_pid_file.exists()


def test_carousel_single_page_handles_error(tmp_path: Path):
    """Verify single page mode catches communication errors without crashing."""
    from turzx.protocol import TURZXProtocolError

    mock_driver = MagicMock()
    mock_driver.display_image.side_effect = TURZXProtocolError("Single page write failed")

    test_pid_file = tmp_path / "single_err.pid"

    run_carousel(
        single_page=0,
        driver=mock_driver,
        pid_file=str(test_pid_file),
    )
    assert mock_driver.display_image.called
    assert not test_pid_file.exists()

