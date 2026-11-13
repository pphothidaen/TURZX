"""
Unit tests for TURZX Carousel Telemetry Display runner (turzx_carousel.py).
Tests real-time SIGUSR1 signal handling, reload event triggering, and loop responsiveness.
"""

import signal
import threading
import time
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


def test_render_page_codex_agy_template_layout(tmp_path: Path):
    """
    Verify render_page_codex formats codex agents using the AGY template layout,
    displaying Primary quota (e.g. 23% used) and Resets timestamp (e.g. 2026-11-17 14:53).
    """
    from generate_carousel_pages import render_page_codex
    from turzx.provisioner import AgentProvisioner

    reg_file = tmp_path / "registry.json"
    acc_dir = tmp_path / "accounts"
    prov = AgentProvisioner(registry_path=str(reg_file), accounts_root=str(acc_dir))

    # Verify default codex1 metrics
    codex_agents = prov.get_cluster_agents("codex")
    cdx1 = next(c for c in codex_agents if c["id"] == "codex1")
    assert cdx1["primary_used_pct"] == 23.0
    assert cdx1["primary_reset"] == "2026-11-17 14:53"
    assert cdx1["status"] == "healthy"
    assert cdx1["model"] == "gpt-5.6-terra"
    assert cdx1["claude_reqs_used"] == 0
    assert cdx1["gemini_tokens_used"] == 0
    assert cdx1["argon_units_used"] == 0

    # Render image
    img = render_page_codex(active_page=2, total_pages=3, prov=prov)
    assert img is not None
    assert img.size == (320, 480)
    assert img.mode == "RGB"


def test_codex_no_claude_argon_gemini(tmp_path: Path):
    """
    Verify Codex cluster strictly uses OpenAI models (e.g. gpt-5.6-terra)
    and has NO Claude, Argon, or Gemini sub-quotas/dependencies.
    """
    from turzx.provisioner import AgentProvisioner
    from agent_quota_manager import AgentQuotaManager
    from generate_carousel_pages import render_page_overview, PALETTE

    reg_file = tmp_path / "registry.json"
    acc_dir = tmp_path / "accounts"
    prov = AgentProvisioner(registry_path=str(reg_file), accounts_root=str(acc_dir))

    for cdx in prov.get_cluster_agents("codex"):
        assert "claude" not in cdx["model"].lower()
        assert "gemini" not in cdx["model"].lower()
        assert "argon" not in cdx["model"].lower()
        assert cdx["claude_reqs_used"] == 0
        assert cdx["claude_reqs_limit"] == 0
        assert cdx["gemini_tokens_used"] == 0
        assert cdx["gemini_tokens_limit"] == 0
        assert cdx["argon_units_used"] == 0
        assert cdx["argon_units_limit"] == 0
        assert cdx["model"].startswith("gpt-") or cdx["model"].startswith("o")

    # Verify dynamically added codex agents also have ZERO claude/argon/gemini
    dyn_cdx = prov.add_agent("codex", "CODEX4 Test", preset="codex_standard")
    assert dyn_cdx["claude_reqs_limit"] == 0
    assert dyn_cdx["gemini_tokens_limit"] == 0
    assert dyn_cdx["argon_units_limit"] == 0
    assert "claude" not in dyn_cdx["model"].lower()

    # Verify AgentQuotaManager default codex roles have no claude/argon/gemini
    mgr = AgentQuotaManager()
    res = mgr.sync_agent_quotas()
    for cid in ("codex1", "codex2", "codex3"):
        info = res.get("synced_agents", {}).get(cid, {})
        model = info.get("model", "")
        if model:
            assert "claude" not in model.lower()
            assert "gemini" not in model.lower()
            assert "argon" not in model.lower()

    # Verify overview page renders without errors
    ov_img = render_page_overview(0, 3, prov=prov)
    assert ov_img.size == (320, 480)


def test_render_page_codex_adaptive_count(tmp_path: Path):
    """
    Verify render_page_codex handles dynamic scaling (3, 4, 5, 6 agents) without crashing
    and maintains consistent canvas boundaries.
    """
    from generate_carousel_pages import render_page_codex
    from turzx.provisioner import AgentProvisioner

    reg_file = tmp_path / "registry.json"
    acc_dir = tmp_path / "accounts"
    prov = AgentProvisioner(registry_path=str(reg_file), accounts_root=str(acc_dir))

    # 3 agents (seed)
    img_3 = render_page_codex(2, 3, prov=prov)
    assert img_3.size == (320, 480)

    # 4 agents
    prov.add_agent("codex", "CODEX4 Dynamic", preset="codex_standard")
    img_4 = render_page_codex(3, 4, prov=prov)
    assert img_4.size == (320, 480)

    # 5 agents
    prov.add_agent("codex", "CODEX5 Dynamic", preset="codex_standard")
    img_5 = render_page_codex(3, 4, prov=prov)
    assert img_5.size == (320, 480)

    # 6 agents
    prov.add_agent("codex", "CODEX6 Dynamic", preset="codex_heavy")
    img_6 = render_page_codex(3, 4, prov=prov)
    assert img_6.size == (320, 480)


def test_format_reset_timestamp():
    """Verify reset timestamp formatting handles diverse input variations."""
    from generate_carousel_pages import format_reset_timestamp

    assert format_reset_timestamp("2026-11-17 14:53") == "2026-11-17 14:53"
    assert format_reset_timestamp("(Resets: 2026-11-17 14:53)") == "2026-11-17 14:53"
    assert format_reset_timestamp("Resets: 2026-11-17 14:53") == "2026-11-17 14:53"
    assert format_reset_timestamp("2026-11-17T14:53:00.123456Z") == "2026-11-17 14:53"
    assert format_reset_timestamp("2026-11-17T14:53:00Z") == "2026-11-17 14:53"
    assert format_reset_timestamp("05h 20m") == "05h 20m"
    assert format_reset_timestamp(None) == "2026-11-17 14:53"


def test_render_page_codex_zero_margin_leakage(tmp_path: Path):
    """
    Verify render_page_codex never leaks pixels outside the card boundary
    even at maximum (100%) quota usage.
    """
    import numpy as np
    from generate_carousel_pages import render_page_codex, WIDTH
    from turzx.provisioner import AgentProvisioner

    reg_file = tmp_path / "registry.json"
    acc_dir = tmp_path / "accounts"
    prov = AgentProvisioner(registry_path=str(reg_file), accounts_root=str(acc_dir))

    # Add agent with 100% usage on all tracks
    prov.add_agent("codex", "CODEX_MAX", preset="codex_standard")
    data = prov.get_all_agents()
    for c in data.get("codex", []):
        c["primary_used_pct"] = 100.0
        c["secondary_used_pct"] = 100.0
        c["usage_pct"] = 100.0
        c["reasoning_effort"] = "high"
        c["primary_reset"] = "2026-11-17 14:53"
    prov._save_registry_data(data)

    from generate_carousel_pages import get_gradient_background
    img = render_page_codex(2, 3, prov=prov)
    arr = np.array(img)
    bg = np.array(get_gradient_background())[38:380, 313:WIDTH]

    # Check right margin (x=313 to 319, y=38 to 380)
    margin_slice = arr[38:380, 313:WIDTH]
    diff = np.abs(margin_slice - bg).sum(axis=-1)
    non_bg = int(np.count_nonzero(diff > 0))
    assert non_bg == 0, f"Detected {non_bg} leaked pixels in card right margin"


def test_carousel_power_mode_and_profile_options(tmp_path: Path):
    """Verify run_carousel respects power mode and brightness profiles."""
    from unittest.mock import MagicMock
    from turzx_carousel import run_carousel

    mock_driver = MagicMock()
    mock_driver.display_image.return_value = True
    stop_event = threading.Event()
    stop_event.set()  # Stop immediately after single pass

    pid_file = str(tmp_path / "test-power.pid")
    run_carousel(
        interval=0.1,
        profile="work_environment",
        power_mode="efficient",
        driver=mock_driver,
        stop_event=stop_event,
        pid_file=pid_file,
    )

    # Work environment profile sets brightness to 55%
    mock_driver.set_brightness.assert_called_with(55)


def test_carousel_idle_dimming_and_activity(tmp_path: Path):
    """Verify carousel idle dimming and activity recovery."""
    from unittest.mock import MagicMock
    from turzx_carousel import run_carousel, reload_event

    mock_driver = MagicMock()
    mock_driver.display_image.return_value = True
    stop_event = threading.Event()

    pid_file = str(tmp_path / "test-idle.pid")

    def stop_after_delay():
        time.sleep(0.25)
        # Trigger reload / activity
        reload_event.set()
        time.sleep(0.1)
        stop_event.set()
        reload_event.set()

    t = threading.Thread(target=stop_after_delay)
    t.start()

    run_carousel(
        interval=0.05,
        brightness=60,
        idle_timeout=0.1,
        dim_brightness=15,
        driver=mock_driver,
        stop_event=stop_event,
        pid_file=pid_file,
    )
    t.join()

    # Verify both active brightness (60) and dim brightness (15) were set on driver
    calls = [call[0][0] for call in mock_driver.set_brightness.call_args_list]
    assert 60 in calls
    assert 15 in calls


def test_gradient_template_caching_and_palette():
    """Verify Deep Navy gradient template caching and palette colors (#050B14 -> #0A1128)."""
    from generate_carousel_pages import (
        get_gradient_background,
        GRADIENT_START,
        GRADIENT_END,
        _GRADIENT_TEMPLATE_CACHE,
        render_page_overview,
        render_page_agy,
        render_page_codex,
    )

    # 1. Background dimensions & RGB mode
    bg1 = get_gradient_background()
    assert bg1.size == (320, 480)
    assert bg1.mode == "RGB"

    # 2. Gradient colors (#050B14 at top, #0A1128 at bottom)
    assert bg1.getpixel((160, 0)) == GRADIENT_START
    assert bg1.getpixel((160, 479)) == GRADIENT_END

    # 3. Template memory caching
    cache_len = len(_GRADIENT_TEMPLATE_CACHE)
    assert cache_len >= 1
    bg2 = get_gradient_background()
    assert len(_GRADIENT_TEMPLATE_CACHE) == cache_len
    # Modifying bg2 does not mutate cache
    bg2.putpixel((0, 0), (255, 255, 255))
    bg3 = get_gradient_background()
    assert bg3.getpixel((0, 0)) == GRADIENT_START

    # 4. Overview, AGY, Codex pages all render cleanly on gradient background
    p0 = render_page_overview(0, 3)
    p1 = render_page_agy(1, 3)
    p2 = render_page_codex(2, 3)
    assert p0.size == (320, 480)
    assert p1.size == (320, 480)
    assert p2.size == (320, 480)


def test_skip_redundant_frames_hash_matching(tmp_path: Path):
    """
    Verify that when frame content is identical and no page switch occurred,
    the 307.2 KB USB bulk transfer is skipped (hash matching).
    """
    from datetime import datetime
    from unittest.mock import patch, MagicMock
    from turzx_carousel import run_carousel, reload_event
    from turzx.provisioner import AgentProvisioner

    reg_file = tmp_path / "registry.json"
    acc_dir = tmp_path / "accounts"
    prov = AgentProvisioner(registry_path=str(reg_file), accounts_root=str(acc_dir))

    mock_driver = MagicMock()
    mock_driver.connect.return_value = True
    mock_driver.display_image.return_value = True

    display_calls = []

    def track_display(raw):
        display_calls.append(len(raw))
        return True

    mock_driver.display_image.side_effect = track_display

    pid_file = str(tmp_path / "test-redundant.pid")
    stop_event = threading.Event()

    def simulate_signals():
        # Wait for first render
        time.sleep(0.08)
        # Signal without mutating agent data (hash remains identical, idx remains on page 0)
        reload_event.set()
        time.sleep(0.08)
        # Mutate agent data (adds an agent -> changes overview page hash)
        prov.add_agent("agy", "AGY6 New")
        reload_event.set()
        time.sleep(0.08)
        stop_event.set()
        reload_event.set()

    t = threading.Thread(target=simulate_signals)
    t.start()

    # Freeze datetime so clock tick across 1-second boundary does not alter frame hash
    fixed_now = datetime(2026, 1, 1, 12, 0, 0)
    with patch("turzx_carousel.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_now
        run_carousel(
            interval=5.0,  # Long interval so only signals advance
            brightness=70,
            driver=mock_driver,
            stop_event=stop_event,
            pid_file=pid_file,
            provisioner=prov,
        )
    t.join()

    # Call 1: Initial render of Page 0
    # Call 2 (redundant signal, no mutation): SKIPPED!
    # Call 3 (agent added -> hash changed): TRANSMITTED!
    assert len(display_calls) == 2, f"Expected exactly 2 frame transmissions (1 skipped), got {len(display_calls)}"


def test_carousel_screen_off_on_lock_and_wake(tmp_path: Path):
    """
    Verify screen_off is invoked when lock screen is detected,
    render loop is paused, and screen_on + render are invoked on unlock.
    """
    from datetime import datetime
    from unittest.mock import patch, MagicMock
    from turzx_carousel import run_carousel, reload_event

    mock_driver = MagicMock()
    mock_driver.connect.return_value = True
    mock_driver.display_image.return_value = True

    pid_file = str(tmp_path / "test-lock.pid")
    stop_event = threading.Event()

    lock_state = {"locked": True}

    def is_locked_mock():
        return lock_state["locked"]

    def unlock_after_delay():
        time.sleep(0.1)
        # User unlocks screen
        lock_state["locked"] = False
        time.sleep(0.1)
        stop_event.set()
        reload_event.set()

    t = threading.Thread(target=unlock_after_delay)
    t.start()

    with patch("turzx_carousel.is_macos_screen_locked", side_effect=is_locked_mock):
        with patch("turzx.power.is_macos_screen_locked", side_effect=is_locked_mock):
            run_carousel(
                interval=0.05,
                brightness=75,
                auto_screen_off=True,
                driver=mock_driver,
                stop_event=stop_event,
                pid_file=pid_file,
            )
    t.join()

    # Verify screen_off was called while locked, and screen_on was called on wake
    mock_driver.screen_off.assert_called()
    mock_driver.screen_on.assert_called()


def test_carousel_auto_brightness_integration(tmp_path: Path):
    """
    Verify run_carousel with auto_brightness=True queries MacBook screen brightness
    and applies mapped, clamped brightness (15-85%) with hysteresis.
    """
    from datetime import datetime
    from unittest.mock import patch, MagicMock
    from turzx_carousel import run_carousel

    mock_driver = MagicMock()
    mock_driver.connect.return_value = True
    mock_driver.display_image.return_value = True

    pid_file = str(tmp_path / "test-autobright.pid")
    stop_event = threading.Event()
    stop_event.set()  # Single iteration

    with patch("turzx.power.get_macbook_brightness", return_value=0.45):
        run_carousel(
            interval=0.1,
            auto_brightness=True,
            driver=mock_driver,
            stop_event=stop_event,
            pid_file=pid_file,
        )

    # 45% host brightness mapped to 45%
    mock_driver.set_brightness.assert_called_with(45)


def test_carousel_remains_asleep_on_sigusr1_while_screen_locked(tmp_path: Path):
    """
    Verify that when the screen is locked, SIGUSR1 reload signals do NOT prematurely
    wake the screen or transmit frame buffers to USB; the display remains off in low-power
    polling until the screen is unlocked, at which point it turns on and renders immediately.
    """
    from datetime import datetime
    from unittest.mock import patch, MagicMock
    from turzx_carousel import run_carousel, reload_event

    mock_driver = MagicMock()
    mock_driver.connect.return_value = True
    mock_driver.display_image.return_value = True

    pid_file = str(tmp_path / "test-lock-sigusr1.pid")
    stop_event = threading.Event()

    lock_state = {"locked": True}
    frame_writes = []

    def track_display_image(raw):
        frame_writes.append(time.time())
        return True

    mock_driver.display_image.side_effect = track_display_image
    thread_errs: list[BaseException] = []

    def simulate_background_signals_then_unlock():
        try:
            # Allow initial screen_off to be invoked
            time.sleep(0.08)
            assert mock_driver.screen_off.called

            # Fire SIGUSR1 multiple times while STILL LOCKED (simulating background agent mutations)
            reload_event.set()
            time.sleep(0.05)
            reload_event.set()
            time.sleep(0.05)

            # While locked, display_image must NOT have been called (screen is asleep)
            assert len(frame_writes) == 0
            assert not mock_driver.screen_on.called

            # Now simulate user unlocking MacBook
            lock_state["locked"] = False
            time.sleep(0.08)
        except BaseException as exc:
            thread_errs.append(exc)
        finally:
            # Stop runner after wake
            stop_event.set()
            reload_event.set()

    t = threading.Thread(target=simulate_background_signals_then_unlock)
    t.start()

    with patch("turzx_carousel.is_macos_screen_locked", side_effect=lambda: lock_state["locked"]):
        with patch("turzx.power.is_macos_screen_locked", side_effect=lambda: lock_state["locked"]):
            run_carousel(
                interval=0.05,
                brightness=75,
                auto_screen_off=True,
                driver=mock_driver,
                stop_event=stop_event,
                pid_file=pid_file,
            )
    t.join()
    if thread_errs:
        raise thread_errs[0]

    # Verify: Screen stayed off while locked, and only after unlock did it turn on and render
    mock_driver.screen_off.assert_called()
    mock_driver.screen_on.assert_called()
    assert len(frame_writes) >= 1, "Must render immediately upon unlock"


def test_header_shows_current_time_top_right():
    """
    Verify header renders time in the top-right position (e.g. '11:51:15').
    """
    from generate_carousel_pages import (
        draw_header_and_tabs,
        render_page_overview,
        render_page_agy,
        render_page_codex,
        WIDTH,
    )
    from PIL import Image, ImageDraw

    # 1. Direct test of draw_header_and_tabs with explicit time
    img1 = Image.new("RGB", (WIDTH, 480))
    draw1 = ImageDraw.Draw(img1)
    draw_header_and_tabs(draw1, "HERMES AGENT CLUSTERS", active_page=0, total_pages=3, timestamp="11:51:15")

    img2 = Image.new("RGB", (WIDTH, 480))
    draw2 = ImageDraw.Draw(img2)
    draw_header_and_tabs(draw2, "HERMES AGENT CLUSTERS", active_page=0, total_pages=3, timestamp="22:30:45")

    # Crop the top-right header region where timestamp is displayed (x: 250..315, y: 0..34)
    crop1 = img1.crop((250, 0, 315, 34))
    crop2 = img2.crop((250, 0, 315, 34))

    # Differing timestamps must produce differing pixel buffers in top-right header
    assert crop1.tobytes() != crop2.tobytes(), "Top-right header must reflect the current time"

    # 2. Test page renderers accept timestamp
    p0 = render_page_overview(0, 3, timestamp="11:51:15")
    p1 = render_page_agy(1, 3, timestamp="11:51:15")
    p2 = render_page_codex(2, 3, timestamp="11:51:15")
    assert p0.size == (320, 480)
    assert p1.size == (320, 480)
    assert p2.size == (320, 480)






