"""Why can this PC not talk to the robots?

    uv run python scripts/diagnose_robots.py

Written after the Windows deployment reached every robot at the TCP level
(`Test-NetConnection -Port 8000` said True) while every HTTP call timed out --
a combination that narrows to a very short list. The checks below walk down the
stack one layer at a time and say which layer is the one that breaks:

    socket  ->  raw HTTP  ->  httpx (env proxies)  ->  requests (+ registry)

If the raw socket fails, it is routing or a firewall. If the socket works but
raw HTTP does not, the daemon is not answering. If raw HTTP works but a library
does not, that library is sending the request somewhere else -- which on Windows
means a proxy, because `requests` reads the WinINET registry and httpx reads
HTTP_PROXY/ALL_PROXY.
"""

from __future__ import annotations

import os
import socket
import sys
import urllib.request

PORT = 8000
PATH = "/api/daemon/status"

# One from each exhibit is enough to tell a network fault from a robot fault.
HOSTS = [
    ("KORA   (Reachy_03)", "192.168.3.106"),
    ("ZURI   (Reachy_03)", "192.168.3.163"),
    ("r01    (Reachy_10)", "192.168.3.105"),
    ("r09    (Reachy_10)", "192.168.3.174"),
]

PROXY_VARS = [
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
]


def head(title: str) -> None:
    print(f"\n{'=' * 68}\n  {title}\n{'=' * 68}")


def show_environment() -> bool:
    head("proxy configuration")
    found = False

    for var in PROXY_VARS:
        value = os.environ.get(var)
        if value:
            print(f"  env  {var} = {value}")
            found = True
    if not found:
        print("  env  no proxy variables set")

    # On Windows this reads the WinINET settings out of the registry, which is
    # what `requests` picks up and what no amount of env-var hygiene will hide.
    try:
        proxies = urllib.request.getproxies()
    except Exception as exc:
        proxies = {}
        print(f"  getproxies() failed: {exc}")
    if proxies:
        found = True
        print("  system proxies (this is what `requests` obeys):")
        for scheme, url in proxies.items():
            print(f"    {scheme:<8} {url}")
    else:
        print("  system proxies: none")

    if found:
        print("\n  A proxy IS configured. Robot traffic must not go through it:")
        print("  a campus proxy has no route to 192.168.3.0/24, so every call")
        print("  turns into a timeout that reads like a dead robot.")
    return found


def check_socket(host: str) -> bool:
    try:
        with socket.create_connection((host, PORT), timeout=3.0):
            return True
    except Exception as exc:
        print(f"      socket        FAIL  {type(exc).__name__}: {exc}")
        return False


def check_raw_http(host: str) -> bool:
    """Speak HTTP/1.1 by hand. No library, so no proxy, no config, no opinions."""
    request = (
        f"GET {PATH} HTTP/1.1\r\n"
        f"Host: {host}:{PORT}\r\n"
        "Connection: close\r\n\r\n"
    ).encode()
    try:
        with socket.create_connection((host, PORT), timeout=5.0) as sock:
            sock.sendall(request)
            data = b""
            while len(data) < 4096:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
        status = data.split(b"\r\n", 1)[0].decode("latin-1")
        ok = b"200" in status.encode()
        print(f"      raw HTTP      {'ok  ' if ok else 'FAIL'}  {status}")
        return ok
    except Exception as exc:
        print(f"      raw HTTP      FAIL  {type(exc).__name__}: {exc}")
        return False


def check_httpx(host: str) -> tuple[bool, bool]:
    try:
        import httpx
    except ImportError:
        print("      httpx         (not installed in this venv)")
        return (False, False)

    results = []
    for trust_env in (True, False):
        try:
            r = httpx.get(f"http://{host}:{PORT}{PATH}", timeout=5.0,
                          trust_env=trust_env)
            ok = r.status_code == 200
            detail = f"HTTP {r.status_code}"
        except Exception as exc:
            ok = False
            detail = f"{type(exc).__name__}: {exc}"
        label = "trust_env=True " if trust_env else "trust_env=False"
        print(f"      httpx {label} {'ok  ' if ok else 'FAIL'}  {detail}")
        results.append(ok)
    return (results[0], results[1])


def check_requests(host: str) -> tuple[bool, bool]:
    try:
        import requests
    except ImportError:
        print("      requests      (not installed in this venv)")
        return (False, False)

    results = []
    for trust_env in (True, False):
        session = requests.Session()
        session.trust_env = trust_env
        try:
            r = session.get(f"http://{host}:{PORT}{PATH}", timeout=5.0)
            ok = r.status_code == 200
            detail = f"HTTP {r.status_code}"
        except Exception as exc:
            ok = False
            detail = f"{type(exc).__name__}: {exc}"
        finally:
            session.close()
        label = "trust_env=True " if trust_env else "trust_env=False"
        print(f"      requests {label} {'ok  ' if ok else 'FAIL'}  {detail}")
        results.append(ok)
    return (results[0], results[1])


def main() -> int:
    print("robot reachability -- layer by layer")
    print(f"  python {sys.version.split()[0]} on {sys.platform}")
    has_proxy = show_environment()

    head("per robot")
    tally = {"socket": 0, "raw": 0, "httpx_env": 0, "httpx_noenv": 0,
             "req_env": 0, "req_noenv": 0}
    for label, host in HOSTS:
        print(f"\n  {label}  {host}:{PORT}")
        if check_socket(host):
            tally["socket"] += 1
            print("      socket        ok")
        else:
            continue
        if check_raw_http(host):
            tally["raw"] += 1
        he, hn = check_httpx(host)
        tally["httpx_env"] += he
        tally["httpx_noenv"] += hn
        re_, rn = check_requests(host)
        tally["req_env"] += re_
        tally["req_noenv"] += rn

    total = len(HOSTS)
    head("verdict")
    print(f"  reachable by socket        {tally['socket']}/{total}")
    print(f"  answered raw HTTP          {tally['raw']}/{total}")
    print(f"  httpx  with env proxies    {tally['httpx_env']}/{total}")
    print(f"  httpx  ignoring proxies    {tally['httpx_noenv']}/{total}")
    print(f"  requests with proxies      {tally['req_env']}/{total}")
    print(f"  requests ignoring proxies  {tally['req_noenv']}/{total}")
    print()

    if tally["socket"] == 0:
        print("  -> Nothing connects at all. Routing or a firewall, not the apps.")
        print("     Check that Wi-Fi 2 holds a 192.168.3.x address.")
        return 1
    if tally["raw"] == 0:
        print("  -> TCP connects but the daemon does not answer HTTP.")
        print("     That is a robot-side problem, not this PC's.")
        return 1
    proxy_only = (tally["raw"] > 0
                  and (tally["httpx_noenv"] > tally["httpx_env"]
                       or tally["req_noenv"] > tally["req_env"]))
    if proxy_only:
        print("  -> CONFIRMED: a proxy was eating the robot traffic.")
        print("     Ignoring proxies works; obeying them does not. The fix is")
        print("     already in robot_admin.py / robot_worker.py / fleet/health.py.")
        return 0
    if tally["req_env"] == total and tally["httpx_env"] == total:
        print("  -> Everything works from Python right now. If the exhibit still")
        print("     failed, the difference is in when it ran, not in how.")
        return 0
    print("  -> Mixed result; read the per-robot lines above.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
