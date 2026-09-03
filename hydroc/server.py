# SPDX-License-Identifier: MIT
"""
hydroc.server — local HTTP bridge between the UI and the hardware.

The UI is unprivileged HTML; EC access needs root. Rather than give the browser
privileges, this small server runs as root, binds to loopback only, and exposes
a narrow JSON surface:

    GET  /api/status      what is available (driver, hwmon, EC)
    GET  /api/telemetry   live readings, ~1 Hz
    GET  /api/state       actual hardware state + drift vs saved profile
    POST /api/apply       reconcile hardware to a settings dict
    POST /api/profile     persist the saved profile

Deliberately no external dependencies -- stdlib only, so it runs anywhere the
rest of the tooling does.

    sudo python3 -m hydroc.server            # http://127.0.0.1:8781
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .cli import (PROFILE_PATHS, REPAIR_MODPROBE, REPAIR_MODULE, REPAIR_RELOAD,
                  diagnose, load_profile, save_profile)
from .hardware import Hardware
from . import fancurve, gpumode, presets, rgb
from .hotkeys import ProfileButton

# The LPP dock lives behind a sidecar daemon (hydroc.lppd) because BLE is async
# and the dock is optional hardware. This is a thin proxy: no bleak import here,
# so a machine without a dock never pays for one.
LPP_SOCKET = "/run/hydroc/lpp.sock"


def lpp_call(request: dict, timeout: float = 12.0) -> dict:
    """One request/response over the sidecar's Unix socket.

    A missing socket is the normal state on a machine with no dock, so it is
    reported as "not running" rather than raised -- the UI hides the section.
    """
    import socket
    if not os.path.exists(LPP_SOCKET):
        return {"ok": False, "running": False,
                "error": "hydroc-lpp is not running"}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(LPP_SOCKET)
            sock.sendall((json.dumps(request) + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
        if not buf.strip():
            return {"ok": False, "running": True, "error": "no reply from hydroc-lpp"}
        return {"running": True, **json.loads(buf)}
    except (OSError, ValueError) as e:
        return {"ok": False, "running": False, "error": f"hydroc-lpp: {e}"}

UI_DIR = os.path.join(os.path.dirname(__file__), "ui")
HOST, PORT = "127.0.0.1", 8781

_hw = Hardware()


def apply_preset(name: str) -> dict:
    """Apply a named preset and record it as intent.

    Shared by the HTTP route and the physical button so both paths behave
    identically -- the button is not a second, subtly different way to change
    the machine.
    """
    settings = presets.settings_for(name)
    if settings is None:
        return {"ok": False, "error": f"unknown preset {name!r}"}
    # A preset carries a fan curve too, but only push it if the user is
    # actually in manual mode -- picking a power preset must not quietly take
    # the fans off firmware control. In auto, the curve is stored as intent and
    # lands the moment they switch to manual.
    fan = presets.fan_for(name)
    if fan and _hw.read_state().get("fan_mode") == "manual":
        settings = dict(settings, fan_mode="manual",
                        fan_curve_cpu=fan["cpu"], fan_curve_gpu=fan["gpu"])

    changes = _hw.apply(settings)
    failed = {c.setting for c in changes if not c.ok}
    profile, _ = load_profile()
    for k, v in _hw.normalize(settings).items():
        if k not in failed:
            profile[k] = v
    try:
        save_profile(profile)
    except OSError:
        pass
    state = _hw.read_state()
    return {"ok": not failed, "preset": name, "active": presets.match(state),
            "changes": [c.__dict__ for c in changes], "state": state}


def _on_profile_button() -> None:
    """Physical button: advance to the next preset in the cycle.

    Resolved from live hardware rather than a remembered name, so a press after
    the EC reverted at power-on moves from where the machine actually is.
    """
    current = presets.match(_hw.read_state())
    apply_preset(presets.next_in_cycle(current))


_button = ProfileButton(_on_profile_button)


class Handler(BaseHTTPRequestHandler):
    server_version = "hydrocd/0.1"

    def log_message(self, fmt, *args):        # quieter than the default
        if "--verbose" in sys.argv:
            super().log_message(fmt, *args)

    # -- helpers ----------------------------------------------------------

    def _json(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except ValueError:
            return {}

    def _file(self, rel: str):
        path = os.path.normpath(os.path.join(UI_DIR, rel.lstrip("/")))
        if not path.startswith(UI_DIR) or not os.path.isfile(path):
            self.send_error(404)
            return
        ctype = ("text/html" if path.endswith(".html") else
                 "text/css" if path.endswith(".css") else
                 "application/javascript" if path.endswith(".js") else
                 "application/octet-stream")
        with open(path, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # -- routes -----------------------------------------------------------

    def do_GET(self):
        route = self.path.split("?")[0]
        if route == "/api/status":
            return self._json(_hw.status())
        if route == "/api/telemetry":
            return self._json(_hw.telemetry())
        if route == "/api/rgb/status":
            kb_ok, kb_why = rgb.keyboard_available()
            cb_ok, cb_why = rgb.chinbar_available()
            return self._json({"keyboard": kb_ok, "keyboard_error": kb_why,
                               "chinbar": cb_ok, "chinbar_error": cb_why})
        if route == "/api/health":
            checks = diagnose(_hw)
            failed = [c for c in checks if not c["ok"] and not c["optional"]
                      and not c.get("unknown")]
            return self._json({
                "checks": checks,
                "ok": not failed,
                "failed": len(failed),
                "repairable": any(c.get("repair") for c in failed),
            })
        if route == "/api/presets":
            state = _hw.read_state()
            return self._json({"presets": presets.describe(),
                               "active": presets.match(state),
                               "button": _button.status()})
        if route == "/api/gpu":
            return self._json(gpumode.status())

        if route == "/api/fan":
            state = _hw.read_state()
            return self._json({
                "mode": state.get("fan_mode"),
                "split": state.get("fan_split"),
                "cpu": state.get("fan_curve_cpu"),
                "gpu": state.get("fan_curve_gpu"),
                "presets": {n: presets.fan_for(n) for n in presets.CYCLE},
                "min_duty": fancurve.MIN_DUTY,
                "points": fancurve.TABLE_LEN,
                # Measured, not assumed: the EC ramps toward a new duty at a
                # fixed rate, so the UI can tell the user why the fans have not
                # reacted yet instead of letting them think it failed.
                "slew_pct_per_s": 1.66,
            })

        if route == "/api/rgb/effects":
            return self._json(rgb.effect_params())
        if route == "/api/lpp":
            return self._json(lpp_call({"op": "status"}))
        if route == "/api/state":
            profile, src = load_profile()
            return self._json({
                "state": _hw.read_state(),
                "profile": profile,
                "profile_source": src,
                "drift": _hw.drift(profile),
                "status": _hw.status(),
            })
        if route in ("/", "/index.html"):
            return self._file("index.html")
        return self._file(route)

    def do_POST(self):
        route = self.path.split("?")[0]
        payload = self._body()

        if route == "/api/gpu/set":
            # Not folded into /api/apply on purpose. Everything that route
            # touches is volatile EC state a power cycle undoes; this writes
            # non-volatile firmware. It gets its own endpoint and its own
            # explicit confirm so it cannot be reached by a stray settings dict.
            # Note: `payload` is already read above. Reading the body a second
            # time here blocks forever -- the client has sent exactly
            # Content-Length bytes and is waiting on us, so rfile.read() never
            # returns and the request hangs with no reply and no write.
            try:
                return self._json(gpumode.set_mode(payload.get("mode", ""),
                                                   confirm=bool(payload.get("confirm"))))
            except gpumode.GpuModeError as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            except OSError as e:
                return self._json({"ok": False, "error": f"firmware write failed: {e}"}, 500)

        if route == "/api/apply":
            desired = payload.get("settings")
            if desired is None:                    # apply the saved profile
                desired, _ = load_profile()
            changes = _hw.apply(desired, dry_run=bool(payload.get("dry_run")))

            # A user changing a control is a change of INTENT, so it must be
            # recorded in the profile too -- otherwise drift() compares the new
            # hardware value against the stale profile, reports it as drift,
            # and "Re-apply" helpfully undoes what the user just asked for.
            # Only "Re-apply" itself (persist=false) writes hardware without
            # touching intent, because it is restoring the profile, not changing it.
            persisted = None
            if payload.get("persist") and not payload.get("dry_run"):
                failed = {c.setting for c in changes if not c.ok}
                profile, _ = load_profile()
                # Persist what the hardware can hold, not what was asked for --
                # an unrepresentable value (odd PL4) would read back different
                # forever and show as drift nothing can clear.
                for k, v in _hw.normalize(desired or {}).items():
                    if k not in failed:
                        profile[k] = v
                try:
                    persisted = save_profile(profile)
                except OSError as e:
                    persisted = f"failed: {e}"

            return self._json({
                "changes": [c.__dict__ for c in changes],
                "ok": all(c.ok for c in changes),
                "state": _hw.read_state(),
                "persisted": persisted,
            })

        if route == "/api/repair":
            # Re-running the installer is what fixes a kernel-update breakage.
            # Only the module path is offered: it is the common cause and the
            # narrowest fix. The daemon is already root, so no escalation --
            # but it is gated on a check actually having failed, so the button
            # cannot be used to reinstall on a healthy system by accident.
            checks = diagnose(_hw)
            # A module that only needs loading is a modprobe, not a rebuild.
            # A bound-but-deaf EC needs the driver reloaded, not rebuilt.
            reload_needed = [c for c in checks
                             if not c["ok"] and c.get("repair") == REPAIR_RELOAD]
            if reload_needed:
                # Never unload without a file to load back. A kernel upgrade
                # since boot removes the running kernel's module tree: the
                # unload succeeds, the reload cannot, and the machine is left
                # with no driver and 0x0741 bit 0 clear until a reboot. Check
                # before touching anything -- this is the one ordering that
                # cannot be undone.
                from .kmod import safe_to_reload
                _ok, _why = safe_to_reload("uniwill-laptop")
                if not _ok:
                    return self._json({
                        "ok": False,
                        "output": f"Refusing to reload the driver: {_why}\n"
                                  "Reboot, then try this again.",
                        "checks": diagnose(_hw)})
                import subprocess
                out = []
                for cmd in (["modprobe", "-r", "uniwill-laptop"],
                            ["modprobe", "uniwill-laptop"]):
                    r = subprocess.run(cmd, capture_output=True, text=True,
                                       timeout=60)
                    out.append(f"$ {' '.join(cmd)}\n{(r.stdout + r.stderr).strip()}")
                    if r.returncode != 0:
                        return self._json({"ok": False, "output": "\n".join(out),
                                           "checks": diagnose(_hw)})
                # Re-arming host control leaves the EC at its defaults, so put
                # the user's profile back rather than leaving them at 0/0/0.
                profile, _ = load_profile()
                _hw.apply(profile)
                out.append("re-applied saved profile")
                return self._json({"ok": True, "output": "\n".join(out),
                                   "checks": diagnose(_hw)})

            probe = [c for c in checks
                     if not c["ok"] and c.get("repair") == REPAIR_MODPROBE]
            if probe:
                import subprocess
                r = subprocess.run(["modprobe", "acpi_call"],
                                   capture_output=True, text=True, timeout=60)
                return self._json({"ok": r.returncode == 0,
                                   "output": (r.stderr or "loaded").strip(),
                                   "checks": diagnose(_hw)})
            wanted = [c for c in checks
                      if not c["ok"] and c.get("repair") == REPAIR_MODULE]
            degraded = [c for c in checks
                        if c["ok"] and c.get("repair") == REPAIR_MODULE]
            if not wanted and not degraded:
                return self._json({"ok": False,
                                   "error": "nothing to repair"}, 409)

            script = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "install.sh")
            if not os.path.isfile(script):
                return self._json({"ok": False,
                                   "error": f"installer not found at {script}"}, 500)
            try:
                import subprocess
                r = subprocess.run(["bash", script, "--module"],
                                   capture_output=True, text=True, timeout=600)
                tail = "\n".join((r.stdout + r.stderr).splitlines()[-30:])
                return self._json({"ok": r.returncode == 0, "output": tail,
                                   "checks": diagnose(_hw)})
            except subprocess.TimeoutExpired:
                return self._json({"ok": False,
                                   "error": "the rebuild took too long"}, 504)
            except OSError as e:
                return self._json({"ok": False, "error": str(e)}, 500)

        if route == "/api/preset":
            return self._json(apply_preset(payload.get("preset", "")))

        if route == "/api/rgb/perkey":
            return self._json(rgb.apply_per_key(
                payload.get("colors") or {},
                brightness=payload.get("brightness"),
                save=bool(payload.get("save"))))

        if route == "/api/rgb/effect":
            return self._json(rgb.apply_effect(
                payload.get("name", "rainbow"),
                speed=int(payload.get("speed", 5)),
                brightness=int(payload.get("brightness", 25)),
                color_idx=int(payload.get("color_idx", 8)),
                direction_idx=int(payload.get("direction_idx", 1)),
                save=bool(payload.get("save"))))

        if route == "/api/rgb/chinbar":
            mode = payload.get("mode", "static")
            colour = payload.get("color", "#8CBF73")
            bri = int(payload.get("brightness", 100))
            speed = int(payload.get("speed", 5))
            result = rgb.chinbar(mode, hexcol=colour, brightness=bri, speed=speed)

            # Changing a control is a change of intent, so record it -- same rule
            # as /api/apply. The bar is volatile and reports nothing back, so the
            # profile is the only place this setting can survive a power cycle.
            if result.get("ok"):
                profile, _ = load_profile()
                profile.update({"chin_mode": mode, "chin_color": colour,
                                "chin_brightness": bri, "chin_speed": speed})
                try:
                    result["persisted"] = save_profile(profile)
                except OSError as e:
                    result["persisted"] = f"failed: {e}"
            return self._json(result)

        if route == "/api/lpp/set":
            req = {"op": "set"}
            if payload.get("fan") is not None:
                req["fan"] = payload["fan"]
            if payload.get("pump") is not None:
                req["pump"] = payload["pump"]
            return self._json(lpp_call(req))

        if route == "/api/lpp/reconnect":
            # A rescan plus connect can take the full scan timeout twice over.
            return self._json(lpp_call({"op": "reconnect"}, timeout=40.0))

        if route == "/api/profile":
            dest = PROFILE_PATHS[0]
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "w") as fh:
                    json.dump(payload.get("profile", {}), fh, indent=2)
                    fh.write("\n")
            except OSError as e:
                return self._json({"ok": False, "error": str(e)}, 500)
            return self._json({"ok": True, "path": dest})

        self.send_error(404)


def main() -> int:
    if not os.path.isdir(UI_DIR):
        raise SystemExit(f"UI not found at {UI_DIR}")
    if os.geteuid() != 0:
        print("warning: not root -- EC values and writes will be unavailable",
              file=sys.stderr)
    _button.start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"hydroc UI on http://{HOST}:{PORT}  (loopback only, Ctrl-C to stop)")
    st = _button.status()
    print("profile button: " + (f"listening on {st['device']}" if st["listening"]
                                else f"not active -- {st['error']}"))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        # A curve outliving the process that set it is the failure mode that
        # could actually hurt someone: nothing would then be watching
        # temperatures, and the EC would keep running whatever we last wrote.
        # Firmware control is the safe resting state, and this is cheap.
        try:
            if fancurve.is_enabled(_hw.ec):
                print("returning fans to firmware control")
                fancurve.disable(_hw.ec)
        except Exception:                                  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
