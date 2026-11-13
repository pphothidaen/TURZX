"""Unit tests for TURZX Remote USB Hub Supervisor (scripts/ensure_remote_hub.py)."""

from __future__ import annotations

import os
import signal
import subprocess
from unittest.mock import MagicMock, patch

import pytest

import scripts.ensure_remote_hub as supervisor


def test_is_vh_port_open_success():
    with patch("socket.create_connection") as mock_conn:
        mock_sock = MagicMock()
        mock_conn.return_value = mock_sock
        assert supervisor.is_vh_port_open("192.168.1.1", 7575, timeout=1.0) is True
        mock_conn.assert_called_once_with(("192.168.1.1", 7575), timeout=1.0)
        mock_sock.close.assert_called_once()


def test_is_vh_port_open_failure():
    with patch("socket.create_connection", side_effect=OSError("Connection refused")):
        assert supervisor.is_vh_port_open("192.168.1.1", 7575, timeout=1.0) is False


def test_ensure_vh_server_already_active():
    with patch.object(supervisor, "is_vh_port_open", return_value=True):
        assert supervisor.ensure_vh_server_on_router("192.168.1.1", 7575, force=False) is True


def test_ensure_vh_server_triggers_telnet_success():
    # Simulate port initially closed, then opening after Telnet setup
    port_open_seq = [False, True]

    def mock_is_open(ip, port, timeout=1.5):
        if port_open_seq:
            return port_open_seq.pop(0)
        return True

    mock_fac = MagicMock()
    mock_fac.negotiate.return_value = (("user1", "pass1"), "dev1", "sn1")

    mock_telnet = MagicMock()
    mock_sock = MagicMock()
    mock_run_cmd = MagicMock(return_value="")
    mock_telnet.create_session.return_value = (mock_sock, mock_run_cmd)

    with patch.object(supervisor, "is_vh_port_open", side_effect=mock_is_open), \
         patch.dict("sys.modules", {"core.factory_mode": MagicMock(FactoryModeClient=mock_fac),
                                    "core.telnet_client": MagicMock(TelnetClient=mock_telnet)}):
        result = supervisor.ensure_vh_server_on_router("192.168.1.1", 7575, force=False)
        assert result is True
        mock_fac.negotiate.assert_called_once_with("192.168.1.1")
        mock_telnet.create_session.assert_called_once_with("192.168.1.1", "user1", "pass1")
        assert mock_run_cmd.call_count >= 4
        mock_sock.close.assert_called_once()


def test_ensure_vh_server_import_error():
    with patch.object(supervisor, "is_vh_port_open", return_value=False), \
         patch.dict("sys.modules", {"core.factory_mode": None}):
        assert supervisor.ensure_vh_server_on_router("192.168.1.1", 7575) is False


def test_ensure_vh_client_on_mac_binary_missing():
    with patch("os.path.exists", return_value=False):
        assert supervisor.ensure_vh_client_on_mac() is False


def test_ensure_vh_client_on_mac_success():
    with patch("os.path.exists", return_value=True), \
         patch("subprocess.run") as mock_run, \
         patch.object(supervisor, "vh_client_cmd") as mock_vh_cmd:
        
        mock_run.return_value = MagicMock(stdout="12345\n", returncode=0)
        # Sequence: First LIST has device not in use; after USE, second LIST has in use
        list_responses = [
            "UsbMonitor (F6107A.31)\nAuto-Use All currently off\n",
            "UsbMonitor (F6107A.31)\nAuto-Use All currently on\n",
            "UsbMonitor (F6107A.31) (In-use by you)\nAuto-Use All currently on\n",
        ]

        def side_effect(args):
            if args == ["LIST"]:
                if list_responses:
                    return list_responses.pop(0)
                return "UsbMonitor (F6107A.31) (In-use by you)\nAuto-Use All currently on\n"
            return "OK"

        mock_vh_cmd.side_effect = side_effect

        assert supervisor.ensure_vh_client_on_mac(device_id="F6107A.31", timeout=2.0) is True
        mock_vh_cmd.assert_any_call(["AUTO USE ALL"])
        mock_vh_cmd.assert_any_call(["USE,F6107A.31"])


def test_ensure_vh_client_on_mac_already_in_use():
    with patch("os.path.exists", return_value=True), \
         patch("subprocess.run") as mock_run, \
         patch.object(supervisor, "vh_client_cmd") as mock_vh_cmd:
        
        mock_run.return_value = MagicMock(stdout="12345\n", returncode=0)
        # Device is already attached and in use locally
        mock_vh_cmd.side_effect = lambda args: (
            "UsbMonitor (F6107A.31) (In-use by you)\nAuto-Use All currently on\n"
            if args == ["LIST"] else "OK"
        )

        assert supervisor.ensure_vh_client_on_mac(device_id="F6107A.31", timeout=1.0) is True
        # USE must NOT be called if already in use to prevent disabling Auto-Use
        calls = [c[0][0] for c in mock_vh_cmd.call_args_list]
        assert ["USE,F6107A.31"] not in calls


def test_terminate_stale_carousel(tmp_path):
    pid_file = tmp_path / "turzx-carousel.pid"
    pid_file.write_text("99999", encoding="utf-8")

    with patch.object(supervisor, "CAROUSEL_PID_FILE", str(pid_file)), \
         patch("os.kill") as mock_kill, \
         patch("subprocess.run") as mock_run:
        
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        supervisor.terminate_stale_carousel()
        mock_kill.assert_any_call(99999, signal.SIGTERM)
        assert not pid_file.exists()


def test_main_once_flag():
    with patch.object(supervisor, "ensure_vh_server_on_router", return_value=True), \
         patch.object(supervisor, "ensure_vh_client_on_mac", return_value=True), \
         patch("sys.argv", ["ensure_remote_hub.py", "--once"]):
        assert supervisor.main() == 0


def test_main_once_flag_failure():
    with patch.object(supervisor, "ensure_vh_server_on_router", return_value=False), \
         patch("sys.argv", ["ensure_remote_hub.py", "--once"]):
        assert supervisor.main() == 1


def test_has_matching_pid():
    # Echo + prompt without numeric PID (e.g. process not running)
    out_no_pid = "pgrep -f watchdog.sh\r\nroot@F6107A:~# "
    assert supervisor.has_matching_pid(out_no_pid) is False

    # Echo + PID + prompt (process is running)
    out_with_pid = "pgrep -f watchdog.sh\r\n2250\r\nroot@F6107A:~# "
    assert supervisor.has_matching_pid(out_with_pid) is True

    # Empty string
    assert supervisor.has_matching_pid("") is False


def test_ensure_vh_client_does_not_toggle_auto_use_when_already_on():
    with patch("os.path.exists", return_value=True), \
         patch("subprocess.run") as mock_run, \
         patch.object(supervisor, "vh_client_cmd") as mock_vh_cmd:

        mock_run.return_value = MagicMock(stdout="12345\n", returncode=0)
        # Listing already includes 'Auto-Use All currently on'
        mock_vh_cmd.side_effect = lambda args: (
            "UsbMonitor (F6107A.31) (In-use by you)\nAuto-Use All currently on\n"
            if args == ["LIST"] else "OK"
        )

        assert supervisor.ensure_vh_client_on_mac(device_id="F6107A.31", timeout=1.0) is True
        # AUTO USE ALL must NOT be called to avoid toggling it off!
        calls = [c[0][0] for c in mock_vh_cmd.call_args_list]
        assert ["AUTO USE ALL"] not in calls


def test_ensure_vh_client_dynamic_device_id():
    with patch("os.path.exists", return_value=True), \
         patch("subprocess.run") as mock_run, \
         patch.object(supervisor, "vh_client_cmd") as mock_vh_cmd:

        mock_run.return_value = MagicMock(stdout="12345\n", returncode=0)
        # Device appears on port F6107A.99 and is initially not claimed
        list_seq = [
            "UsbMonitor (F6107A.99)\nAuto-Use All currently on\n",
            "UsbMonitor (F6107A.99) (In-use by you)\nAuto-Use All currently on\n",
        ]

        def side_effect(args):
            if args == ["LIST"]:
                if list_seq:
                    return list_seq.pop(0)
                return "UsbMonitor (F6107A.99) (In-use by you)\nAuto-Use All currently on\n"
            return "OK"

        mock_vh_cmd.side_effect = side_effect

        assert supervisor.ensure_vh_client_on_mac(device_id="F6107A.31", timeout=1.0) is True
        # USE should target the dynamically discovered F6107A.99 address
        mock_vh_cmd.assert_any_call(["USE,F6107A.99"])


def test_terminate_stale_carousel_posix_kill(tmp_path):
    pid_file = tmp_path / "turzx-carousel.pid"
    pid_file.write_text("12345", encoding="utf-8")

    alive_state = [True, True, False]

    def mock_kill(pid, sig):
        if sig == 0:
            if not alive_state.pop(0):
                raise ProcessLookupError()
        return None

    with patch.object(supervisor, "CAROUSEL_PID_FILE", str(pid_file)), \
         patch("os.kill", side_effect=mock_kill) as m_kill, \
         patch("subprocess.run") as mock_run:

        mock_run.return_value = MagicMock(stdout="", returncode=0)
        supervisor.terminate_stale_carousel()
        assert not pid_file.exists()

