"""Can this PC reach the robot through the VPN? Shared by configure and the launcher.

Which Wi-Fi the PC is on (eduroam, home, dorm, ...) does not matter: the robot is
reached through a VPN (GlobalProtect), so the checks are about the VPN, not the Wi-Fi.
"""

import ipaddress
import os
import socket
import subprocess
import sys
from typing import Optional


def globalprotect_connected() -> Optional[bool]:
    """True/False from the GlobalProtect network adapter on Windows, None if unknown."""
    if sys.platform != "win32":
        return None
    # The built-in Windows PowerShell, not whatever "powershell" comes first on PATH.
    powershell = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                              "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    try:
        out = subprocess.run(
            [powershell, "-NoProfile", "-Command",
             "(Get-NetAdapter -InterfaceDescription 'PANGP*' -ErrorAction SilentlyContinue).Status -join ','"],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not out:
        return None  # no GlobalProtect adapter found: installed differently, cannot tell
    return "Up" in out.split(",")


def robot_unreachable_reason(ip: str, isolated_subnet: str = "") -> Optional[str]:
    """None if the robot's SSH port answers, otherwise what to fix.

    Reaching port 22 is the test: any VPN that routes to the robot works
    (GlobalProtect, Tailscale, ...). The GlobalProtect checks only explain a failure.
    """
    for _ in range(3):  # the VPN sometimes drops the first packets after (re)connecting
        try:
            socket.create_connection((ip, 22), timeout=5).close()
            return None
        except OSError:
            pass
    if globalprotect_connected() is False:
        return ("The robot is not reachable. If you use GlobalProtect, connect it (with your own account); "
                "if you use another VPN such as Tailscale, check that it routes to the robot.")
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((ip, 22))
        src = s.getsockname()[0]
        s.close()
    except OSError:
        return "No network route to the robot. Is your VPN (GlobalProtect or Tailscale) connected?"
    if isolated_subnet and ipaddress.ip_address(src) in ipaddress.ip_network(isolated_subnet):
        # With GlobalProtect up this subnet is routed into the VPN; going out through
        # the Wi-Fi directly means the VPN is not carrying it.
        return "Traffic to the robot is not going through the VPN. Connect your VPN and try again."
    return (f"The robot ({ip}) does not answer on port 22.\n"
            "        - Is your VPN (GlobalProtect or Tailscale) connected and routing to the robot?\n"
            "        - Is the robot powered on?\n"
            "        - Is the IP address right? It can change; ask your instructor.")
