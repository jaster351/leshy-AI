"""Native SSH/RDP discovery has no network effects and never claims agent control."""
from __future__ import annotations

from leshy.native_remote_capabilities import remote_client_capabilities


def _which(*present: str):
    names = set(present)
    return lambda name: ("C:/tools/" + name) if name in names else None


def test_windows_ssh_rdp_available():
    state = remote_client_capabilities(
        platform_name="windows", which=_which("ssh.exe", "mstsc.exe")
    )
    assert state["platform"] == "windows"
    assert state["ssh_client_available"] is True
    assert state["rdp_launcher_available"] is True
    assert state["ssh_client"] == "ssh.exe"
    assert state["rdp_launcher"] == "mstsc.exe"
    assert state["autonomous_rdp_available"] is False
    assert "C:/tools" not in str(state)


def test_linux_optional_rdp_not_installed():
    state = remote_client_capabilities(platform_name="linux", which=_which("ssh"))
    assert state["ssh_client_available"] is True
    assert state["rdp_launcher_available"] is False
    assert state["autonomous_rdp_available"] is False
    assert state["rdp_status"] == "no_local_launcher"


def test_linux_existing_rdp_client():
    state = remote_client_capabilities(
        platform_name="posix", which=_which("xfreerdp3", "ssh")
    )
    assert state["rdp_launcher"] == "xfreerdp3"
    assert state["rdp_status"] == "human_launcher_only"


def test_unknown_platform_fail_closed():
    state = remote_client_capabilities(platform_name="plan9", which=_which("ssh"))
    assert state["ssh_client_available"] is False
    assert state["rdp_launcher_available"] is False
    assert state["autonomous_rdp_available"] is False


def test_windows_without_optional_openssh():
    state = remote_client_capabilities(platform_name="nt", which=_which("mstsc.exe"))
    assert state["ssh_client_available"] is False
    assert state["rdp_launcher_available"] is True
