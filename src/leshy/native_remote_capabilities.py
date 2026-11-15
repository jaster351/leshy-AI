"""Non-invasive detection of locally installed SSH and RDP clients.

This module reports capability, never opens a connection or installs software.
A native RDP launcher for a human is NOT an autonomous desktop controller.
"""
from __future__ import annotations

import os
import shutil
from typing import Callable


def remote_client_capabilities(
    *, platform_name: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> dict[str, object]:
    """Inspect local executables, without spawning subprocesses or reading secrets."""
    system = (platform_name or os.name).lower()
    if system in {"nt", "windows"}:
        ssh_candidates = ("ssh.exe", "ssh")
        rdp_candidates = ("mstsc.exe", "msrdc.exe")
        family = "windows"
    elif system in {"posix", "linux", "darwin", "freebsd", "openbsd", "netbsd"}:
        ssh_candidates = ("ssh",)
        # Optional existing system client. Never install it automatically.
        rdp_candidates = ("xfreerdp", "xfreerdp3", "wlfreerdp", "rdesktop")
        family = "unix"
    else:
        return {
            "platform": "unsupported",
            "ssh_client_available": False,
            "rdp_launcher_available": False,
            "ssh_client": None,
            "rdp_launcher": None,
            "autonomous_rdp_available": False,
            "rdp_status": "unsupported_platform",
        }

    def discover(candidates: tuple[str, ...]) -> str | None:
        for name in candidates:
            if which(name):
                # Return only the executable name; absolute installation paths can
                # contain usernames and must not appear in the status API.
                return name
        return None

    ssh = discover(ssh_candidates)
    rdp = discover(rdp_candidates)
    return {
        "platform": family,
        "ssh_client_available": ssh is not None,
        "rdp_launcher_available": rdp is not None,
        "ssh_client": ssh,
        "rdp_launcher": rdp,
        "autonomous_rdp_available": False,
        "rdp_status": "human_launcher_only" if rdp else "no_local_launcher",
    }


__all__ = ["remote_client_capabilities"]
