"""Finding, identifying and restarting the robot daemons.

Discovery here is a subnet sweep, not mDNS, and that is deliberate. Every
Reachy Mini ships announcing itself as `reachy-mini`, and a dozen of them share
this Wi-Fi; when two collide, the loser's mDNS registration is corrupted until
it reboots. Asking each IP directly sidesteps the whole problem.
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import locale
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

# Every call in here is to a robot on the same LAN, so each one passes
# `trust_env=False`. httpx otherwise honours HTTP_PROXY/ALL_PROXY, and this
# exhibit PC sits behind a campus proxy -- which cannot reach 192.168.3.0/24
# and turns "the robot is right there" into a bare timeout.
DAEMON_PORT = 8000
STATUS_TIMEOUT = 2.0


def _run_text(command: list[str], timeout: float) -> str:
    """Run `command` and decode its output without guessing wrong.

    `subprocess(text=True)` decodes with the *locale* codec, which on Japanese
    Windows is cp932 -- and PowerShell writes UTF-8. The mismatch does not raise
    where you would notice it: `_interfaces_windows` died on the third byte of
    the first interface alias (`\u30c8` = e3 83 88, and 0x88 has no valid cp932
    follow-on), the caller swallowed the UnicodeDecodeError, and `local_subnet()`
    reported "not on the robot Wi-Fi" on a machine that was.

    So take bytes and try codecs in order. `arp -a` is a native console program
    and really does emit the OEM codepage, so UTF-8 must be tried *strictly*
    first and fall through, not with errors="replace".
    """
    raw = subprocess.run(command, capture_output=True, timeout=timeout).stdout
    for codec in ("utf-8", locale.getpreferredencoding(False), "latin-1"):
        try:
            return raw.decode(codec)
        except (UnicodeDecodeError, LookupError, TypeError):
            continue
    return raw.decode("utf-8", errors="replace")


def daemon_url(host: str) -> str:
    return f"http://{host}:{DAEMON_PORT}/api"


def daemon_status(host: str, timeout: float = STATUS_TIMEOUT) -> dict | None:
    """The daemon's status dict, or None if it does not answer."""
    try:
        response = httpx.get(f"{daemon_url(host)}/daemon/status", timeout=timeout,
                             trust_env=False)
        if response.status_code == 200:
            return response.json() or {}
    except Exception:
        pass
    return None


# Interfaces that are never the robot Wi-Fi: loopback, container bridges and
# the virtual adapters VMware/Hyper-V/VirtualBox hand out. Each of those owns a
# private /24 of its own, so without this list a scan happily sweeps 192.168.16.0
# (VMnet1) and reports that no robots exist.
_VIRTUAL_IFACE = re.compile(
    r"^(lo|docker|br-|veth|virbr|vmnet|vboxnet)"
    r"|vmware|hyper-v|virtualbox|bluetooth|loopback|vethernet",
    re.I,
)
# The robots live on Wi-Fi (Speedefy_5G), so a wireless adapter is tried first
# when a machine has several private /24s. Japanese Windows keeps the alias
# "Wi-Fi" but may localise the Ethernet one, hence the kana.
_WIRELESS_IFACE = re.compile(r"^wl|wi-?fi|wlan|wireless|無線", re.I)


def _interfaces_linux() -> list[tuple[str, str]]:
    """[(name, 'ip/prefix')] from `ip -brief -4 addr`, UP interfaces only."""
    output = _run_text(["ip", "-brief", "-4", "addr"], timeout=5)

    interfaces = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[1] != "UP":
            continue
        interfaces.append((fields[0], fields[2]))
    return interfaces


def _interfaces_windows() -> list[tuple[str, str]]:
    """[(alias, 'ip/prefix')] from Get-NetIPAddress.

    The output is formatted by us rather than by PowerShell's own table writer,
    so this does not depend on the console locale -- the exhibit PC in Omaha
    runs Japanese Windows, where every built-in table header is translated.
    """
    script = (
        "Get-NetIPAddress -AddressFamily IPv4 "
        "| Where-Object { $_.AddressState -ne 'Invalid' } "
        "| ForEach-Object { \"$($_.InterfaceAlias)|$($_.IPAddress)/$($_.PrefixLength)\" }"
    )
    output = _run_text(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=15,
    )

    interfaces = []
    for line in output.splitlines():
        alias, _, cidr = line.strip().partition("|")
        if alias and cidr:
            interfaces.append((alias, cidr))
    return interfaces


def local_subnet(interface_hint: str = "") -> str | None:
    """The /24 this machine shares with the robots, e.g. '192.168.3.0/24'."""
    try:
        if sys.platform == "win32":
            candidates = _interfaces_windows()
        else:
            candidates = _interfaces_linux()
    except Exception:
        return None

    candidates = [
        (name, cidr) for name, cidr in candidates if not _VIRTUAL_IFACE.search(name)
    ]

    # Wireless first, then the hint if one was given -- the hint wins because it
    # is sorted last and a stable sort leaves it in front.
    candidates.sort(key=lambda item: not _WIRELESS_IFACE.search(item[0]))
    if interface_hint:
        candidates.sort(key=lambda item: item[0] != interface_hint)

    for name, cidr in candidates:
        try:
            network = ipaddress.ip_interface(cidr).network
        except ValueError:
            continue
        # Robots live on a private /24. Skip the campus network, and skip the
        # /32 a VPN client hands its own adapter -- it is private and it is
        # narrower than a /24, so the prefixlen test alone would let it pass.
        if network.prefixlen not in range(24, 31) or not network.is_private:
            continue
        if network.num_addresses < 4:
            continue
        return str(network)
    return None


def scan_subnet(subnet: str, workers: int = 64) -> list[str]:
    """Every IP on `subnet` answering as a Reachy daemon."""
    hosts = [str(ip) for ip in ipaddress.ip_network(subnet).hosts()]
    found: list[str] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for host, status in zip(hosts, pool.map(daemon_status, hosts)):
            if status is not None:
                found.append(host)
    return sorted(found, key=lambda ip: tuple(int(p) for p in ip.split(".")))


# `ip neigh show` prints "IP dev IFACE lladdr MAC STATE"; filtering with
# `dev IFACE` drops the "dev IFACE" part, so it is optional here.
_NEIGH_LINE = re.compile(r"^(\S+)(?:\s+dev\s+\S+)?\s+lladdr\s+(\S+)")
# Windows `arp -a` prints "  IP   aa-bb-cc-dd-ee-ff   dynamic" under a localised
# header. Matching the two data columns skips the header without reading it.
_ARP_LINE = re.compile(
    r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+"
    r"([0-9a-f]{2}(?:-[0-9a-f]{2}){5})\b",
    re.I,
)
# ...but `arp -a` prints one block per interface, and the block header carries
# that interface's own address: "Interface: 192.168.3.146 --- 0xc" (the label is
# translated, the shape is not, so match the shape). Reading every block
# indiscriminately is how all fourteen robots came back as
# 02-50-41-00-00-02 -- a locally administered MAC whose bytes 2-3 spell "PA":
# the GlobalProtect VPN answering ARP for a subnet it had swallowed while the
# Wi-Fi adapter was down. A MAC read off the wrong interface is worse than no
# MAC at all here, because re-mapping robots by MAC is what this is for.
_ARP_IFACE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})\s+---\s+0x[0-9a-f]+", re.I)


def _same_24(a: str, b: str) -> bool:
    return a.rsplit(".", 1)[0] == b.rsplit(".", 1)[0]


def arp_table(interface: str = "") -> dict[str, str]:
    """ip -> MAC, from the OS neighbour/ARP cache.

    Useful because the robots take DHCP leases: after a router reboot the IPs
    move, but the MACs in .env do not. Both platforms only know a robot that has
    been talked to recently, so call this after a scan, not before.
    """
    if sys.platform == "win32":
        command = ["arp", "-a"]
        if interface:
            command += ["-N", interface]
        pattern = _ARP_LINE
    else:
        command = ["ip", "neigh", "show"]
        if interface:
            command += ["dev", interface]
        pattern = _NEIGH_LINE

    try:
        output = _run_text(command, timeout=5)
    except Exception:
        return {}

    table = {}
    iface_ip = ""
    for line in output.splitlines():
        header = _ARP_IFACE.search(line)
        if header:
            iface_ip = header.group(1)
            continue
        match = pattern.match(line)
        if not match:
            continue
        # Keep a neighbour only where it is actually a neighbour: the interface
        # the block belongs to has to share its /24. On Linux there are no
        # blocks, `iface_ip` stays empty, and nothing is filtered.
        if iface_ip and not _same_24(iface_ip, match.group(1)):
            continue
        # Windows separates the octets with dashes; .env and `ip neigh` use
        # colons. Normalise so a MAC from either host compares equal.
        table[match.group(1)] = match.group(2).replace("-", ":").lower()
    return table


def restart_daemon(host: str, progress=print) -> tuple[bool, str]:
    """Stop, start and wake one robot's daemon, then enable its motors.

    Returns (ok, detail). Deliberately tolerant: a 409 from /daemon/stop just
    means it was already busy stopping.
    """
    base = daemon_url(host)
    try:
        if daemon_status(host, timeout=5.0) is None:
            return False, "daemon not reachable"

        stop = httpx.post(f"{base}/daemon/stop", params={"goto_sleep": "true"},
                          timeout=10.0, trust_env=False)
        if stop.status_code not in (200, 409):
            return False, f"stop returned {stop.status_code}"

        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            status = daemon_status(host)
            if status is not None and str(status.get("state")) in (
                "stopped",
                "not_initialized",
            ):
                break
            time.sleep(1.0)
        time.sleep(2.0)

        start = httpx.post(f"{base}/daemon/start", params={"wake_up": "true"},
                           timeout=10.0, trust_env=False)
        if start.status_code not in (200, 409):
            return False, f"start returned {start.status_code}"

        # Motors come up disabled; the control loop needs them enabled.
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{base}/motors/status", timeout=5.0,
                             trust_env=False).status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(1.0)

        httpx.post(f"{base}/motors/set_mode/enabled", timeout=10.0,
                   trust_env=False)
        return True, "restarted"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def restart_all(hosts: list[str], progress=print) -> dict[str, tuple[bool, str]]:
    """Restart several daemons at once."""
    results: dict[str, tuple[bool, str]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(hosts) or 1) as pool:
        futures = {pool.submit(restart_daemon, host): host for host in hosts}
        for future in concurrent.futures.as_completed(futures):
            host = futures[future]
            results[host] = future.result()
            ok, detail = results[host]
            progress(f"    {host}: {'OK' if ok else 'FAILED'} — {detail}")
    return results


def update_env_hosts(env_path: Path, hosts: dict[str, str]) -> None:
    """Rewrite the <ROLE>_HOST lines in .env, preserving everything else."""
    text = env_path.read_text(encoding="utf-8")
    for role, host in hosts.items():
        key = f"{role.upper()}_HOST"
        text, count = re.subn(rf"^{key}=.*$", f"{key}={host}", text, flags=re.M)
        if count == 0:
            text = text.rstrip("\n") + f"\n{key}={host}\n"
    env_path.write_text(text, encoding="utf-8")
