# SPDX-License-Identifier: MIT
"""
kbctrl.daemon — Persistence daemon for ITE 8291 RGB keyboard + ITE 8233 lightbar.

Applies user profile on:
  • Startup (boot)
  • Resume from suspend/hibernate (SIGUSR1)
  • Profile file change (inotify watch)

Exposes a Unix domain socket for live commands from the TUI/CLI.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import fcntl
import json
import logging
import os
import select
import signal
import socket
import sys
from pathlib import Path

from .config import (
    PROFILE_PATH,
    decode_key_colors,
    encode_key_colors,
    load_profile,
    save_profile,
)
from .hardware import HardwareDriver, lb_color, lb_set
from .layout import load_key_matrix

log = logging.getLogger("kbctrld")

# ── Constants ──────────────────────────────────────────────────────────────────

SOCKET_DIR = Path("/run/kbctrl")
SOCKET_PATH = SOCKET_DIR / "kbctrl.sock"
PID_PATH = SOCKET_DIR / "kbctrld.pid"

SIG_WAKE = b"w"
SIG_QUIT = b"q"

# ── Inotify (ctypes, zero-dependency) ──────────────────────────────────────────

_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True) \
    if ctypes.util.find_library("c") else None

IN_CLOSE_WRITE = 0x00000008
IN_NONBLOCK = 0x00000800


class _INotifyEvent(ctypes.Structure):
    _fields_ = [
        ("wd",     ctypes.c_int),
        ("mask",   ctypes.c_uint32),
        ("cookie", ctypes.c_uint32),
        ("len",    ctypes.c_uint32),
    ]


def _inotify_init1(flags: int) -> int:
    ret = _libc.inotify_init1(ctypes.c_int(flags))
    if ret < 0:
        raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
    return ret


def _inotify_add_watch(fd: int, path: bytes, mask: int) -> int:
    ret = _libc.inotify_add_watch(
        ctypes.c_int(fd), ctypes.c_char_p(path), ctypes.c_uint32(mask)
    )
    if ret < 0:
        raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
    return ret


_INOTIFY_HDR = ctypes.sizeof(_INotifyEvent)


# ── Daemon ─────────────────────────────────────────────────────────────────────

def _default_socket_path() -> str:
    try:
        SOCKET_DIR.mkdir(parents=True, exist_ok=True)
        return str(SOCKET_PATH)
    except PermissionError:
        fallback = Path("/tmp/kbctrl")
        fallback.mkdir(parents=True, exist_ok=True)
        return str(fallback / "kbctrl.sock")


def _resolve_socket_path(path: str | None) -> str:
    if path:
        d = Path(path).parent
        d.mkdir(parents=True, exist_ok=True)
        return path
    return _default_socket_path()


class Daemon:
    """Persistent daemon that keeps keyboard RGB state applied."""

    def __init__(self, socket_path: str | None = None, no_socket: bool = False):
        self.socket_path = _resolve_socket_path(socket_path) if not no_socket else None
        self.no_socket = no_socket
        self._running = False

        self._hw: HardwareDriver | None = None

        # Self-pipe for signal delivery
        self._sig_r, self._sig_w = os.pipe()
        for fd in (self._sig_r, self._sig_w):
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        # Inotify watch
        self._ino_fd: int | None = None

        # Socket clients: fd -> (conn, buffer)
        self._clients: dict[int, tuple[socket.socket, bytearray]] = {}
        self._server: socket.socket | None = None

        self._setup_signals()
        self._setup_inotify()
        if self.socket_path:
            self._setup_socket()

    # -- setup ---------------------------------------------------------------

    def _setup_signals(self) -> None:
        signal.signal(signal.SIGUSR1, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)

    def _on_signal(self, signum: int, _frame) -> None:
        if signum == signal.SIGUSR1:
            os.write(self._sig_w, SIG_WAKE)
        elif signum in (signal.SIGTERM, signal.SIGINT):
            os.write(self._sig_w, SIG_QUIT)

    def _setup_inotify(self) -> None:
        if not _libc:
            log.warning("libc not found — inotify disabled")
            return
        try:
            self._ino_fd = _inotify_init1(IN_NONBLOCK)
            config_dir = PROFILE_PATH.parent
            config_dir.mkdir(parents=True, exist_ok=True)
            watch_path = str(config_dir.resolve()).encode()
            _inotify_add_watch(self._ino_fd, watch_path, IN_CLOSE_WRITE)
            log.info("watching %s for profile changes", config_dir)
        except OSError as e:
            log.warning("inotify setup failed (%s) — profile watch disabled", e)
            if self._ino_fd is not None:
                os.close(self._ino_fd)
                self._ino_fd = None

    def _setup_socket(self) -> None:
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass
        try:
            self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._server.bind(self.socket_path)
            # 0666: the IPC socket must be reachable from the user session
            # (dms plugin). Local RGB control is low-risk to expose.
            os.chmod(self.socket_path, 0o666)
            self._server.listen(5)
            self._server.setblocking(False)
            log.info("listening on %s", self.socket_path)
        except OSError as e:
            log.warning("socket setup failed (%s) — IPC disabled", e)
            self._server = None

    # -- hardware -----------------------------------------------------------

    def _ensure_hw(self) -> bool:
        if self._hw is None:
            self._hw = HardwareDriver()
        if not self._hw.connected():
            log.warning("hardware not available: %s",
                        self._hw.error if self._hw else "unknown")
            return False
        return True

    # -- pid file -----------------------------------------------------------

    def _write_pid(self) -> None:
        try:
            PID_PATH.parent.mkdir(parents=True, exist_ok=True)
            PID_PATH.write_text(str(os.getpid()))
        except OSError as e:
            log.warning("cannot write PID file: %s", e)

    def _remove_pid(self) -> None:
        try:
            PID_PATH.unlink()
        except OSError:
            pass

    # -- profile operations -------------------------------------------------

    def apply_profile(self) -> str:
        profile = load_profile()
        if not profile:
            return "no profile found"

        if not self._ensure_hw():
            return f"hardware unavailable: {self._hw.error if self._hw else 'unknown'}"

        results: list[str] = []

        key_colors = decode_key_colors(profile.get("key_colors", {}))
        brightness = profile.get("brightness", 25)

        if key_colors:
            try:
                self._hw.apply_per_key(key_colors, brightness=brightness)
                results.append(f"{len(key_colors)} key colors (brightness {brightness})")
            except Exception as e:
                results.append(f"key colors failed: {e}")
        else:
            try:
                self._hw.set_brightness(brightness)
                results.append(f"brightness {brightness}")
            except Exception as e:
                results.append(f"brightness failed: {e}")

        lb = profile.get("lightbar", {})
        if lb:
            level = lb.get("brightness", 25)
            color = lb.get("color")
            if color:
                err = lb_color(color[0], color[1], color[2], level)
                results.append(f"lightbar color bri {level}" if not err
                               else f"lightbar error: {err}")
            else:
                err = lb_set(level)
                if err:
                    results.append(f"lightbar error: {err}")
                else:
                    results.append(f"lightbar brightness {level}")

        return "; ".join(results) if results else "nothing applied"

    def apply_config(self, config: dict) -> str:
        if not self._ensure_hw():
            return f"hardware unavailable: {self._hw.error if self._hw else 'unknown'}"

        # flash_save: write to keyboard flash memory (survives power cycles),
        # mirroring the TUI's "Apply & Save" / "Push & Save".
        flash_save = bool(config.get("flash_save", False))
        results: list[str] = []
        effect = config.get("effect")

        if config.get("restore_palette"):
            self._hw.restore_palette()
            results.append("palette restored")

        key_colors = decode_key_colors(config.get("key_colors", {}))
        brightness = config.get("brightness")

        if key_colors:
            self._hw.apply_per_key(key_colors, brightness=brightness,
                                   save=flash_save)
            results.append(f"{len(key_colors)} key colors")
        elif brightness is not None and not effect:
            self._hw.set_brightness(brightness)
            results.append(f"brightness {brightness}")

        if effect:
            name = effect.get("name")
            try:
                self._hw.set_effect(
                    name,
                    speed=effect.get("speed", 5),
                    brightness=effect.get("brightness", 25),
                    color_idx=effect.get("color_idx", 8),
                    direction_idx=effect.get("dir_idx", 1),
                    reactive=bool(effect.get("reactive", False)),
                    save=bool(effect.get("save", flash_save)),
                )
                results.append(f"effect {name}")
            except Exception as e:
                results.append(f"effect failed: {e}")

        lb = config.get("lightbar", {})
        lb_level = None
        if lb:
            lb_level = lb.get("brightness")
            if lb_level is not None:
                lb_color_cfg = lb.get("color")
                if lb_color_cfg:
                    err = lb_color(lb_color_cfg[0], lb_color_cfg[1],
                                   lb_color_cfg[2], lb_level)
                    results.append(f"lightbar color bri {lb_level}" if not err
                                   else f"lightbar error: {err}")
                else:
                    err = lb_set(lb_level)
                    if err:
                        results.append(f"lightbar error: {err}")
                    else:
                        results.append(f"lightbar brightness {lb_level}")

        # Persist the applied state so boot/resume restore matches what was set.
        profile = load_profile()
        if key_colors:
            profile["key_colors"] = encode_key_colors(key_colors)
        if brightness is not None:
            profile["brightness"] = brightness
        if effect:
            profile["effect"] = {
                k: effect[k] for k in
                ("name", "speed", "brightness", "color_idx", "dir_idx", "reactive")
                if k in effect
            }
        if lb_level is not None:
            profile["lightbar"] = {"brightness": lb_level}
            if lb.get("color"):
                profile["lightbar"]["color"] = lb["color"]
        save_profile(profile)

        return "; ".join(results) if results else "nothing applied"

    # -- event loop ---------------------------------------------------------

    def run(self) -> None:
        self._running = True

        self._write_pid()
        status = self.apply_profile()
        log.info("startup: %s", status)

        poll = select.poll()
        poll.register(self._sig_r, select.POLLIN)
        if self._ino_fd is not None:
            poll.register(self._ino_fd, select.POLLIN)
        if self._server is not None:
            poll.register(self._server, select.POLLIN)

        while self._running:
            try:
                events = poll.poll(1000)
            except InterruptedError:
                continue

            for fd, event in events:
                if event & (select.POLLERR | select.POLLHUP | select.POLLNVAL):
                    self._drop_client(poll, fd)
                    continue
                if not (event & select.POLLIN):
                    continue

                if fd == self._sig_r:
                    self._on_signal_pipe()
                elif self._ino_fd is not None and fd == self._ino_fd:
                    self._on_inotify()
                elif self._server is not None and fd == self._server.fileno():
                    self._on_accept(poll)
                elif fd in self._clients:
                    self._on_recv(poll, fd)

        self._shutdown(poll)

    def _on_signal_pipe(self) -> None:
        try:
            data = os.read(self._sig_r, 64)
        except BlockingIOError:
            return
        if SIG_QUIT in data:
            log.info("shutting down")
            self._running = False
        if SIG_WAKE in data:
            log.info("SIGUSR1 — re-applying (resume)")
            status = self.apply_profile()
            log.info("resume: %s", status)

    def _on_inotify(self) -> None:
        try:
            buf = os.read(self._ino_fd, 4096)
        except BlockingIOError:
            return
        offset = 0
        while offset + _INOTIFY_HDR <= len(buf):
            evt = _INotifyEvent.from_buffer_copy(buf, offset)
            offset += _INOTIFY_HDR
            name = buf[offset:offset + evt.len].rstrip(b"\x00").decode(
                "utf-8", errors="replace"
            )
            offset += evt.len
            if (evt.mask & IN_CLOSE_WRITE) and name == PROFILE_PATH.name:
                log.info("profile changed — re-applying")
                status = self.apply_profile()
                log.info("reload: %s", status)

    def _on_accept(self, poll) -> None:
        try:
            conn, _addr = self._server.accept()
        except OSError:
            return
        conn.setblocking(False)
        fd = conn.fileno()
        self._clients[fd] = (conn, bytearray())
        poll.register(conn, select.POLLIN)

    def _on_recv(self, poll, fd: int) -> None:
        conn, buf = self._clients[fd]
        try:
            chunk = conn.recv(4096)
        except OSError:
            chunk = b""

        if not chunk:
            self._drop_client(poll, fd)
            return

        buf.extend(chunk)
        while b"\n" in buf:
            line, _, buf = buf.partition(b"\n")
            line = line.strip()
            if not line:
                continue
            response = self._exec(line.decode("utf-8", errors="replace"))
            try:
                conn.sendall((json.dumps(response) + "\n").encode("utf-8"))
            except OSError:
                break

        self._clients[fd] = (conn, buf)

    def _exec(self, cmd: str) -> dict:
        if cmd == "reload":
            return {"ok": True, "status": self.apply_profile()}

        if cmd == "status":
            profile = load_profile()
            layout = []
            for row in (load_key_matrix() or []):
                layout.append([[label, r, c, w] for (label, r, c, w) in row])
            return {
                "ok": True,
                "has_profile": bool(profile),
                "brightness": profile.get("brightness"),
                "effect": profile.get("effect"),
                "key_count": len(profile.get("key_colors", {})),
                "key_colors": profile.get("key_colors", {}),
                "lightbar": profile.get("lightbar"),
                "layout": layout,
                "connected": self._hw.connected() if self._hw else False,
            }

        if cmd.startswith("apply "):
            try:
                config = json.loads(cmd[6:])
            except json.JSONDecodeError as e:
                return {"ok": False, "error": f"invalid json: {e}"}
            return {"ok": True, "status": self.apply_config(config)}

        return {"ok": False, "error": f"unknown command: {cmd}"}

    def _drop_client(self, poll, fd: int) -> None:
        if fd not in self._clients:
            return
        conn = self._clients[fd][0]
        poll.unregister(fd)
        conn.close()
        del self._clients[fd]

    def _shutdown(self, poll) -> None:
        log.info("cleaning up")
        for fd in list(self._clients):
            self._drop_client(poll, fd)
        if self._server is not None:
            poll.unregister(self._server.fileno())
            self._server.close()
            try:
                os.unlink(self.socket_path)
            except (FileNotFoundError, TypeError):
                pass
        if self._ino_fd is not None:
            poll.unregister(self._ino_fd)
            os.close(self._ino_fd)
        poll.unregister(self._sig_r)
        os.close(self._sig_r)
        os.close(self._sig_w)
        self._remove_pid()
        log.info("shutdown complete")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="kbctrl persistence daemon")
    parser.add_argument("--socket", help="Unix socket path (default: /run/kbctrl/kbctrl.sock)")
    parser.add_argument("--no-socket", action="store_true", help="disable IPC socket")
    parser.add_argument("--debug", action="store_true", help="enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    daemon = Daemon(socket_path=args.socket, no_socket=args.no_socket)
    daemon.run()


if __name__ == "__main__":
    main()
