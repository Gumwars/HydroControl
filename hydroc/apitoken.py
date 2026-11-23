# SPDX-License-Identifier: MIT
"""
hydroc.apitoken — who may change the hardware through the daemon.

The daemon runs as root and listens on loopback, and loopback is shared by
everything on the machine: other accounts, containers, and sandboxed apps,
which reach 127.0.0.1 by default. The Origin and Host checks stop web pages;
they never stopped a local process, which sends no Origin at all. So every
POST now carries a token, and the token is readable only by root and by the
person at the keyboard.

"The person at the keyboard" is logind's own answer, the one it already uses to
grant device access (uaccess): the active session on seat0, `ACTIVE_UID` in
/run/systemd/seats/seat0. The token lives in /run/hydroc/token, mode 0400,
owned by that user. When the active user changes, a new token is issued and the
file changes hands, so a token read by someone who has since switched away
stops working. Nothing to install, no group to join, no re-login.

GET stays open. It only reads, and the UI page itself has to load before it can
present a token. A client that cannot read the token gets a read-only app.

    curl -H "X-HydroControl-Token: $(cat /run/hydroc/token)" ...
"""

from __future__ import annotations

import hmac
import os
import secrets
import sys
import threading

HEADER = "X-HydroControl-Token"
RUN_DIR = "/run/hydroc"
TOKEN_PATH = os.path.join(RUN_DIR, "token")
SEAT = "/run/systemd/seats/seat0"


def seat_uid(seat: str = SEAT) -> int | None:
    """The uid of the active session on the seat, or None without one."""
    try:
        with open(seat) as fh:
            for line in fh:
                if line.startswith("ACTIVE_UID="):
                    return int(line.split("=", 1)[1])
    except (OSError, ValueError):
        pass
    return None


def token_path() -> str:
    """Where the token is for this process: /run/hydroc as root, else the
    user's runtime directory, which is how a development daemon run without
    root still hands its own user a token."""
    if os.geteuid() == 0:
        return TOKEN_PATH
    run = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(run, "hydroc-token")


def read_token() -> str | None:
    """The current token, for a client. None when it cannot be read."""
    for path in dict.fromkeys([TOKEN_PATH, token_path()]):
        try:
            with open(path) as fh:
                t = fh.read().strip()
            if t:
                return t
        except OSError:
            continue
    return None


class TokenKeeper(threading.Thread):
    """Holds the token, hands the file to the seat's active user, checks POSTs.

    The token exists from construction, so the handler can check requests
    (and tests can present it) before start() ever writes a file.
    """

    POLL = 2.0

    def __init__(self, path: str | None = None, seat: str = SEAT) -> None:
        super().__init__(daemon=True, name="api-token")
        self._path = path
        self._seat = seat
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.token = secrets.token_hex(32)
        self.uid: int | None = None
        self.error: str | None = None

    @property
    def path(self) -> str:
        return self._path or token_path()

    def check(self, presented: str | None) -> bool:
        with self._lock:
            return bool(presented) and hmac.compare_digest(presented, self.token)

    def refresh(self) -> None:
        """Re-issue the token if the seat's active user changed."""
        uid = seat_uid(self._seat) if os.geteuid() == 0 else os.getuid()
        if uid == self.uid and os.path.exists(self.path):
            return
        with self._lock:
            if self.uid is not None and uid != self.uid:
                self.token = secrets.token_hex(32)
            self._write(uid)
            self.uid = uid

    def _write(self, uid: int | None) -> None:
        """Replace the token file atomically, readable by `uid` alone."""
        path = self.path
        tmp = f"{path}.{os.getpid()}.tmp"
        try:
            d = os.path.dirname(path)
            os.makedirs(d, mode=0o755, exist_ok=True)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o400)
            try:
                os.write(fd, (self.token + "\n").encode())
                if os.geteuid() == 0:
                    # No active user: root alone. Never anyone else's file.
                    os.fchown(fd, uid if uid is not None else 0, 0)
            finally:
                os.close(fd)
            os.replace(tmp, path)
            self.error = None
        except OSError as e:
            self.error = f"{path}: {e}"
            print(f"api token: {self.error}", file=sys.stderr)
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        self.refresh()
        while not self._stop.wait(self.POLL):
            self.refresh()
