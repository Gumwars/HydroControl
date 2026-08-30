# SPDX-License-Identifier: MIT
"""
kbctrl.ctl — Client helper for communicating with the kbctrld daemon.

Usage:
    from kbctrl.ctl import send

    result = send("reload")
    result = send("status")
    result = send('apply {"brightness": 50}')
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

SOCKET_PATHS = [
    Path("/run/kbctrl/kbctrl.sock"),
    Path("/tmp/kbctrl/kbctrl.sock"),
]


def _find_socket() -> str:
    for p in SOCKET_PATHS:
        if p.is_socket():
            return str(p)
    raise FileNotFoundError(
        "kbctrld socket not found at " +
        " or ".join(str(p) for p in SOCKET_PATHS) +
        " — is the daemon running?"
    )


def send(command: str, socket_path: str | None = None) -> dict:
    """Send a command to the kbctrld daemon and return the JSON response."""
    path = socket_path or _find_socket()
    if not command.endswith("\n"):
        command += "\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(5)
        sock.connect(path)
        sock.sendall(command.encode("utf-8"))
        response = sock.recv(65536)
    return json.loads(response.decode("utf-8").strip())


def main() -> None:
    """CLI entry point: kbctrl-ctl <command> [args...]"""
    import sys as _sys
    if len(_sys.argv) < 2:
        print("usage: kbctrl-ctl <command> [args...]", file=_sys.stderr)
        print("  commands: reload, status, apply <json>", file=_sys.stderr)
        _sys.exit(1)

    cmd = " ".join(_sys.argv[1:])
    try:
        result = send(cmd)
    except FileNotFoundError as e:
        # JSON on stdout so the dms plugin can surface errors
        print(json.dumps({"ok": False, "error": str(e)}))
        _sys.exit(1)
    except (ConnectionRefusedError, OSError) as e:
        print(json.dumps({"ok": False, "error": f"cannot connect to daemon: {e}"}))
        _sys.exit(1)

    print(json.dumps(result, indent=2))
    if not result.get("ok", False):
        _sys.exit(1)


if __name__ == "__main__":
    main()
