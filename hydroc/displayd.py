# SPDX-License-Identifier: MIT
"""
hydroc.displayd — holds the display colour mode on the built-in panel.

Runs as the USER, in the Hyprland session: it is a Wayland client, and a
Wayland connection belongs to whoever logged in. The root daemon reaches it
through a Unix socket, the same way it reaches the LPP sidecar.

    python3 -m hydroc.displayd                  # normally hydroc-display.service

The protocol is hyprland_ctm_control_v1: set a 3x3 matrix per wl_output, then
commit. When the client disconnects, Hyprland resets every matrix to
identity -- which is why this is a long-running process and not a one-shot.
Only one client may hold it at a time; a second gets `blocked` and is ignored,
so running hyprsunset alongside this is reported, not fought.

Stdlib only. The Wayland wire format is small enough that a binding library
would be the bigger dependency: a 32-bit object id, a 16-bit size and 16-bit
opcode, then 4-byte-aligned arguments.

State -- the active mode and each mode's sliders -- lives in
~/.config/hydroc/display.json, owned by the user, never touched by root.
"""

from __future__ import annotations

import json
import os
import select
import socket
import struct
import sys

from . import colorprofile, displaymode

CTM_IFACE = "hyprland_ctm_control_manager_v1"
STATE_PATH = os.path.expanduser("~/.config/hydroc/display.json")


def control_socket_path() -> str:
    run = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(run, "hydroc", "display.sock")


# --- Wayland wire format ------------------------------------------------------

def fixed(v: float) -> int:
    """wl_fixed_t: signed 24.8."""
    return int(round(v * 256))


def wl_string(s: str) -> bytes:
    b = s.encode() + b"\0"
    return struct.pack("<I", len(b)) + b + b"\0" * (-len(b) % 4)


def message(obj: int, opcode: int, payload: bytes = b"") -> bytes:
    return struct.pack("<II", obj, ((8 + len(payload)) << 16) | opcode) + payload


def parse_messages(buf: bytes) -> tuple[list[tuple[int, int, bytes]], bytes]:
    """Complete messages in `buf`, and the incomplete remainder."""
    out = []
    while len(buf) >= 8:
        obj, word = struct.unpack_from("<II", buf)
        size, opcode = word >> 16, word & 0xFFFF
        if size < 8 or len(buf) < size:
            break
        out.append((obj, opcode, buf[8:size]))
        buf = buf[size:]
    return out, buf


def read_string(payload: bytes, off: int = 0) -> tuple[str, int]:
    n = struct.unpack_from("<I", payload, off)[0]
    s = payload[off + 4:off + 4 + n].rstrip(b"\0").decode(errors="replace")
    return s, off + 4 + n + (-n % 4)


class WaylandError(Exception):
    pass


class CtmClient:
    """The few objects this needs: display, registry, outputs, CTM manager."""

    DISPLAY, REGISTRY = 1, 2

    def __init__(self, path: str | None = None):
        if path is None:
            name = os.environ.get("WAYLAND_DISPLAY", "wayland-0")
            run = os.environ.get("XDG_RUNTIME_DIR", "")
            path = name if os.path.isabs(name) else os.path.join(run, name)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(path)
        self.buf = b""
        self.next_id = 3
        self.globals: dict[int, tuple[str, int]] = {}   # name -> (iface, version)
        self.outputs: dict[int, dict] = {}              # object id -> {global, name}
        self.manager: int | None = None
        self.blocked = False
        self.on_outputs_changed = None
        self._done: set[int] = set()
        self.send(message(self.DISPLAY, 1, struct.pack("<I", self.REGISTRY)))
        self.roundtrip()        # globals arrive; outputs and the manager get bound
        self.roundtrip()        # their first events: output names, `blocked`

    def fileno(self) -> int:
        return self.sock.fileno()

    def _new_id(self) -> int:
        i = self.next_id
        self.next_id += 1
        return i

    def send(self, data: bytes) -> None:
        self.sock.sendall(data)

    def bind(self, gname: int, iface: str, version: int) -> int:
        oid = self._new_id()
        self.send(message(self.REGISTRY, 0, struct.pack("<I", gname)
                          + wl_string(iface) + struct.pack("<II", version, oid)))
        return oid

    def roundtrip(self) -> None:
        cb = self._new_id()
        self.send(message(self.DISPLAY, 0, struct.pack("<I", cb)))
        while cb not in self._done:
            self.dispatch(block=True)
        self._done.discard(cb)

    def dispatch(self, block: bool = False) -> None:
        if block or select.select([self.sock], [], [], 0)[0]:
            data = self.sock.recv(65536)
            if not data:
                raise WaylandError("compositor closed the connection")
            self.buf += data
        msgs, self.buf = parse_messages(self.buf)
        for obj, op, p in msgs:
            self._event(obj, op, p)

    def _event(self, obj: int, op: int, p: bytes) -> None:
        if obj == self.DISPLAY:
            if op == 0:                                   # error
                oid, code = struct.unpack_from("<II", p)
                msg, _ = read_string(p, 8)
                raise WaylandError(f"protocol error on object {oid} code {code}: {msg}")
            return                                        # delete_id
        if obj == self.REGISTRY:
            if op == 0:                                   # global
                gname = struct.unpack_from("<I", p)[0]
                iface, off = read_string(p, 4)
                version = struct.unpack_from("<I", p, off)[0]
                self.globals[gname] = (iface, version)
                if iface == "wl_output":
                    oid = self.bind(gname, "wl_output", min(version, 4))
                    self.outputs[oid] = {"global": gname, "name": None}
                elif iface == CTM_IFACE and self.manager is None:
                    self.manager = self.bind(gname, CTM_IFACE, min(version, 2))
            elif op == 1:                                 # global_remove
                gname = struct.unpack_from("<I", p)[0]
                self.globals.pop(gname, None)
                for oid in [o for o, v in self.outputs.items() if v["global"] == gname]:
                    del self.outputs[oid]
            return
        if obj in self.outputs:
            if op == 4:                                   # wl_output.name
                self.outputs[obj]["name"], _ = read_string(p)
                if self.on_outputs_changed:
                    self.on_outputs_changed()
            return
        if obj == self.manager and op == 0:               # blocked
            self.blocked = True
            return
        if op == 0:                                       # wl_callback.done
            self._done.add(obj)

    def set_ctm(self, targets: dict[str, tuple[float, ...]]) -> list[str]:
        """Matrix per connector name; every other output gets identity."""
        if self.manager is None:
            raise WaylandError(f"compositor does not offer {CTM_IFACE}")
        applied = []
        for oid, out in self.outputs.items():
            m = targets.get(out["name"])
            if m is None:
                continue
            self.send(message(self.manager, 0, struct.pack("<I", oid)
                              + b"".join(struct.pack("<i", fixed(v)) for v in m)))
            applied.append(out["name"])
        self.send(message(self.manager, 1))               # commit
        return applied


# --- the service -----------------------------------------------------------------

def load_state(path: str = STATE_PATH) -> dict:
    try:
        with open(path) as fh:
            return displaymode.normalise_state(json.load(fh))
    except (OSError, ValueError):
        return displaymode.new_state()


def save_state(state: dict, path: str = STATE_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


class Service:
    def __init__(self, client: CtmClient, state: dict, save=save_state):
        self.client = client
        self.state = state
        self.save = save
        self.applied: list[str] = []
        self.error: str | None = None
        client.on_outputs_changed = self.apply

    def panel(self) -> str | None:
        p = colorprofile.internal_panel()
        return p["connector"] if p else None

    def apply(self) -> None:
        conn = self.panel()
        if not conn:
            self.error = "built-in panel not found"
            return
        m = displaymode.matrix(self.state["modes"][self.state["mode"]])
        try:
            self.applied = self.client.set_ctm({conn: m})
            self.error = None if self.applied else f"{conn} is not a Wayland output yet"
        except (WaylandError, OSError) as e:
            self.error = str(e)

    def status(self) -> dict:
        return {"running": True, "mode": self.state["mode"],
                "modes": displaymode.describe(self.state),
                "limits": displaymode.LIMITS,
                "applied_to": self.applied, "blocked": self.client.blocked,
                "error": ("another program holds the colour matrix (hyprsunset?) "
                          "-- stop it, then restart hydroc-display"
                          if self.client.blocked else self.error)}

    def handle(self, req: dict) -> dict:
        op = req.get("op")
        if op == "status":
            return self.status()
        if op == "set":
            mode = req.get("mode", self.state["mode"])
            if mode not in displaymode.MODES:
                return {"ok": False, "error": f"unknown mode {mode!r}"}
            if isinstance(req.get("params"), dict):
                self.state["modes"][mode] = displaymode.clamp_params(
                    {**self.state["modes"][mode], **req["params"]})
            if req.get("reset"):
                self.state["modes"][mode] = dict(displaymode.MODES[mode]["params"])
            self.state["mode"] = mode
            self.apply()
            try:
                self.save(self.state)
            except OSError as e:
                return {"ok": False, "error": f"saving: {e}", **self.status()}
            return {"ok": self.error is None and not self.client.blocked,
                    **self.status()}
        return {"ok": False, "error": f"unknown op {op!r}"}


def serve() -> int:
    try:
        client = CtmClient()
    except OSError as e:
        print(f"hydroc-display: no Wayland session: {e}", file=sys.stderr)
        return 1
    if client.manager is None:
        print(f"hydroc-display: compositor has no {CTM_IFACE} (not Hyprland?)",
              file=sys.stderr)
        return 1
    svc = Service(client, load_state())
    svc.apply()
    print(f"hydroc-display: {svc.state['mode']} on {svc.applied or 'nothing'}"
          + (" -- BLOCKED by another CTM client" if client.blocked else ""),
          flush=True)

    path = control_socket_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    if os.path.exists(path):
        os.unlink(path)
    ctl = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    ctl.bind(path)
    os.chmod(path, 0o600)
    ctl.listen(4)
    try:
        while True:
            r, _, _ = select.select([client, ctl], [], [])
            if client in r:
                client.dispatch(block=True)
            if ctl in r:
                conn, _ = ctl.accept()
                with conn:
                    conn.settimeout(2)
                    try:
                        data = b""
                        while not data.endswith(b"\n") and len(data) < 65536:
                            chunk = conn.recv(4096)
                            if not chunk:
                                break
                            data += chunk
                        reply = svc.handle(json.loads(data or b"{}"))
                    except (ValueError, OSError) as e:
                        reply = {"ok": False, "error": str(e)}
                    try:
                        conn.sendall(json.dumps(reply).encode() + b"\n")
                    except OSError:
                        pass
    except WaylandError as e:
        # The compositor went away -- logout. Not a failure worth a restart loop.
        print(f"hydroc-display: {e}", file=sys.stderr)
        return 0
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def call(request: dict, path: str | None = None, timeout: float = 3.0) -> dict:
    """One request to a running service. Used by the CLI and the root daemon."""
    import glob
    paths = [path] if path else ([control_socket_path()]
                                 + sorted(glob.glob("/run/user/*/hydroc/display.sock")))
    for p in paths:
        if not p or not os.path.exists(p):
            continue
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect(p)
                s.sendall(json.dumps(request).encode() + b"\n")
                data = b""
                while not data.endswith(b"\n"):
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    data += chunk
            return json.loads(data)
        except (OSError, ValueError):
            continue
    return {"running": False, "ok": False,
            "error": "hydroc-display is not running in your session -- "
                     "systemctl --user start hydroc-display"}


if __name__ == "__main__":
    sys.exit(serve())
