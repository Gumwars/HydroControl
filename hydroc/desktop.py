# SPDX-License-Identifier: MIT
"""
hydroc.desktop — the standalone application window.

The EC needs root and a browser tab does not get root, so there is a privileged
daemon whichever way this is presented. What this adds is the part Control
Center has and we did not: a window you launch from the application menu
instead of a URL you have to remember and a terminal you have to keep open.

It loads `ui/index.html` from the running daemon completely unchanged. That is
deliberate -- the HTML is the contract between shell and app, which is what
makes it cheap to replace this window with a Tauri one later (DESIGN.md, "Next
steps") without touching a line of the UI.

When the daemon is not running it shows a small status page rather than a
WebKit error, and offers to start it through pkexec so the password prompt is a
desktop dialog rather than a terminal.

    hydrocontrol                     # installed launcher
    python3 -m hydroc.desktop        # from a checkout
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")

from gi.repository import GLib, Gtk, WebKit      # noqa: E402

APP_ID = "com.eluktronics.HydroControl"
HOST, PORT = "127.0.0.1", 8781
URL = f"http://{HOST}:{PORT}/"

SERVICE = "hydroc-server.service"
POLL_MS = 1000
CONNECT_TIMEOUT_S = 0.35


def daemon_up() -> bool:
    """Is anything listening on the loopback port?"""
    try:
        with socket.create_connection((HOST, PORT), CONNECT_TIMEOUT_S):
            return True
    except OSError:
        return False


def service_installed() -> bool:
    return os.path.exists(f"/etc/systemd/system/{SERVICE}")


def polkit_agent_running() -> bool:
    """Is there anything in this session that can show an auth dialog?

    Without an agent, pkexec fails with a message a newcomer cannot act on --
    or on some setups appears to do nothing at all. Better to say so up front
    than to let the Start button silently fail.
    """
    try:
        out = subprocess.run(["pgrep", "-af", "polkit.*agent|polkit-.*-authentication-agent"],
                             capture_output=True, text=True, timeout=5)
        if out.stdout.strip():
            return True
        # Some agents do not match that pattern; fall back to a broad look.
        out = subprocess.run(["pgrep", "-af", "polkit"],
                             capture_output=True, text=True, timeout=5)
        return "agent" in out.stdout.lower()
    except Exception:
        return True          # cannot tell -- do not cry wolf


def daemon_failure_reason() -> str:
    """Why the daemon is not up, in the user's words rather than systemd's."""
    if not service_installed():
        return ""
    try:
        r = subprocess.run(["systemctl", "is-active", SERVICE],
                           capture_output=True, text=True, timeout=10)
        state = r.stdout.strip()
        if state == "active":
            return ""
        j = subprocess.run(
            ["journalctl", "-u", SERVICE, "-n", "12", "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=10)
        lines = [l for l in j.stdout.splitlines() if l.strip()]
        if lines:
            return "\n".join(lines[-8:])
    except Exception:
        pass
    return ""


def launch_report() -> dict:
    """Classify what stands between the user and a working window.

    Deliberately narrower than `doctor`. "Running as root" is not a user
    problem here -- the daemon runs as root, this window does not. RGB devices
    being absent is not a reason to refuse to start. Only things that actually
    prevent, or visibly degrade, a launch appear.
    """
    try:
        from .cli import REPAIR_MODPROBE, REPAIR_MODULE, diagnose
        from .hardware import Hardware
        checks = diagnose(Hardware())
    except Exception as e:
        return {"fatal": None, "blockers": [], "degraded": [],
                "error": f"could not run the self-check: {e}"}

    # This window runs as the user; the daemon runs as root. Any check that
    # needs root is UNKNOWN here, not failed -- diagnose() marks those, and
    # drawing conclusions from them told a user their keyboard library was
    # missing when it was installed and working.
    IGNORE = {"running as root", "hwmon sensors", "battery charge modes"}
    fatal, blockers, degraded = None, [], []
    for c in checks:
        if (c["optional"] or c["ok"] or c.get("unknown")
                or c["name"] in IGNORE):
            continue
        if c["name"] == "supported model":
            fatal = c
        elif c.get("repair") in (REPAIR_MODULE, REPAIR_MODPROBE):
            blockers.append(c)
        elif c["name"].startswith("acpi_call"):
            blockers.append(c)            # real blocker, just not fixable by us
        else:
            degraded.append(c)
    return {"fatal": fatal, "blockers": blockers, "degraded": degraded,
            "error": None}


def launch_state(rep: dict, polkit: bool = True) -> dict:
    """Decide what the status page should say. Pure -- no widgets, no GTK.

    Kept separate from the rendering so the decision table can be tested
    without a display, which is the part worth being sure about: a newcomer
    meets this page before they meet anything else.
    """
    # 1. Wrong machine. Nothing to repair and running anyway is unsafe.
    if rep.get("fatal"):
        c = rep["fatal"]
        return {"title": "This is not a HYDROC-16",
                "detail": f"{c['detail']}.\n\n{c['remedy']}",
                "primary": None, "close": "Close", "action": None, "extra": None}

    # 2. Broken, and we can fix it.
    fixable = [c for c in rep.get("blockers", []) if c.get("repair")]
    if fixable:
        c = fixable[0]
        return {"title": "Something needs repairing",
                "detail": f"{c['name']}: {c['detail']}.\n\n{c['remedy']}",
                "primary": "Repair and start", "close": "Close",
                "action": "repair", "extra": None}

    # 3. Broken, and only the user can fix it -- a missing distribution package.
    unfixable = [c for c in rep.get("blockers", []) if not c.get("repair")]
    if unfixable:
        c = unfixable[0]
        return {"title": "A required piece is missing",
                "detail": f"{c['name']}: {c['detail']}.\n\n{c['remedy']}",
                "primary": None, "close": "Close", "action": None, "extra": None}

    # 4. Nothing wrong. It simply is not running yet.
    notes = []
    if rep.get("degraded"):
        notes.append("Some hardware is unavailable and those controls will be "
                     "hidden: " + ", ".join(c["name"] for c in rep["degraded"]))
    if not polkit:
        notes.append("No authentication agent seems to be running in this "
                     "session, so the permission prompt may not appear. If "
                     "nothing happens, start it from a terminal with:\n"
                     "sudo systemctl start hydroc-server")
    return {"title": "HydroControl is not running",
            "detail": "The background service holds the fans, power limits and "
                      "lighting. It needs to be started before this window has "
                      "anything to show.",
            "primary": "Start HydroControl", "close": "Close",
            "action": "start", "extra": "\n\n".join(notes) or None}


def repair_now() -> tuple[bool, str]:
    """Run the installer's module path with authentication."""
    if not shutil.which("pkexec"):
        return False, "pkexec is not installed, so this cannot ask for permission"
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(root, "install.sh")
    if not os.path.isfile(script):
        return False, f"the installer is not where it was expected ({script})"
    try:
        r = subprocess.run(["pkexec", "bash", script, "--module"],
                           capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            tail = "\n".join((r.stdout + r.stderr).splitlines()[-6:])
            return False, tail.strip() or "the rebuild did not succeed"
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "the rebuild took too long and was stopped"
    except OSError as e:
        return False, str(e)


def start_daemon() -> tuple[bool, str]:
    """Ask for the daemon to start, escalating through pkexec.

    Prefer systemd: it gives journal logging, a clean shutdown and one owner of
    the process. Falling back to spawning a detached root Python is worse in
    every one of those ways, so it is only used when the unit is absent.
    """
    if not shutil.which("pkexec"):
        return False, "pkexec not found -- start the daemon manually"

    if service_installed():
        cmd = ["pkexec", "systemctl", "start", SERVICE]
    else:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cmd = ["pkexec", "env", f"PYTHONPATH={root}",
               sys.executable, "-m", "hydroc.server"]
    try:
        if service_installed():
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
            if r.returncode != 0:
                return False, (r.stderr or r.stdout or "").strip() or "failed"
            return True, ""
        subprocess.Popen(cmd, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "timed out waiting for authentication"
    except OSError as e:
        return False, str(e)


class Window(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="HydroControl")
        self.set_default_size(1320, 900)

        self.stack = Gtk.Stack()
        self.set_child(self.stack)

        self.view = WebKit.WebView()
        self.stack.add_named(self.view, "app")
        self.stack.add_named(self._status_page(), "status")

        self._loaded = False
        self._waited: int | None = None
        self._action: str | None = "start"
        self._poll = GLib.timeout_add(POLL_MS, self._tick)
        self._tick()

    # -- the "something is wrong" page ------------------------------------

    def _status_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=13)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{m}")(44)

        self.status_title = Gtk.Label()
        self.status_title.set_markup("<span size='x-large' weight='bold'>"
                                     "HydroControl is not running</span>")
        box.append(self.status_title)

        self.status_detail = Gtk.Label()
        self.status_detail.set_justify(Gtk.Justification.CENTER)
        self.status_detail.set_wrap(True)
        self.status_detail.set_max_width_chars(64)
        self.status_detail.add_css_class("dim-label")
        box.append(self.status_detail)

        self.status_extra = Gtk.Label()
        self.status_extra.set_justify(Gtk.Justification.CENTER)
        self.status_extra.set_wrap(True)
        self.status_extra.set_max_width_chars(72)
        self.status_extra.add_css_class("dim-label")
        self.status_extra.set_visible(False)
        box.append(self.status_extra)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        buttons.set_halign(Gtk.Align.CENTER)
        self.primary = Gtk.Button(label="Start HydroControl")
        self.primary.add_css_class("suggested-action")
        self.primary.connect("clicked", self._on_primary)
        buttons.append(self.primary)

        self.quit_button = Gtk.Button(label="Close")
        self.quit_button.connect("clicked", lambda _b: self.close())
        buttons.append(self.quit_button)
        box.append(buttons)
        return box

    def _describe(self) -> None:
        """Render whatever launch_state decided."""
        st = launch_state(launch_report(), polkit_agent_running())
        self._action = st["action"]
        self.status_title.set_markup(
            f"<span size='x-large' weight='bold'>{GLib.markup_escape_text(st['title'])}</span>")
        self.status_detail.set_text(st["detail"])
        self.quit_button.set_label(st["close"])
        if st["primary"]:
            self.primary.set_visible(True)
            self.primary.set_label(st["primary"])
            self.primary.set_sensitive(True)
        else:
            self.primary.set_visible(False)
        if st["extra"]:
            self.status_extra.set_text(st["extra"])
            self.status_extra.set_visible(True)
        else:
            self.status_extra.set_visible(False)

    def _on_primary(self, _button: Gtk.Button) -> None:
        if self._action is None:
            return
        self.primary.set_sensitive(False)
        self.quit_button.set_sensitive(False)

        if self._action == "repair":
            self.primary.set_label("Repairing\u2026")
            ok, err = repair_now()
            if not ok:
                self.status_detail.set_text(f"The repair did not work.\n\n{err}")
                self.primary.set_label("Try again")
                self.primary.set_sensitive(True)
                self.quit_button.set_sensitive(True)
                return

        self.primary.set_label("Starting\u2026")
        ok, err = start_daemon()
        self.quit_button.set_sensitive(True)
        if not ok:
            reason = daemon_failure_reason()
            self.status_detail.set_text(
                f"It could not be started.\n\n{err}"
                + (f"\n\n{reason}" if reason else ""))
            self.primary.set_label("Try again")
            self.primary.set_sensitive(True)
            return
        self.primary.set_label("Waiting for it to start\u2026")
        self._waited = 0

    # -- polling -----------------------------------------------------------

    def _tick(self) -> bool:
        if daemon_up():
            if not self._loaded:
                self._loaded = True
                self._waited = None
                self.view.load_uri(URL)
                self.stack.set_visible_child_name("app")
            return GLib.SOURCE_CONTINUE

        if self._loaded:
            # It went away under us. Never leave a stale page up implying the
            # readings on screen are still live.
            self._loaded = False
            self.stack.set_visible_child_name("status")
            self._describe()
            self.status_title.set_markup("<span size='x-large' weight='bold'>"
                                         "HydroControl stopped</span>")
            self.primary.set_sensitive(True)
            return GLib.SOURCE_CONTINUE

        if self._waited is None:
            # First time on this page: work out what to say.
            self.stack.set_visible_child_name("status")
            self._describe()
            self._waited = -1
        elif self._waited >= 0:
            # We asked it to start. Do not spin forever pretending.
            self._waited += 1
            if self._waited > 20:
                reason = daemon_failure_reason()
                self.status_detail.set_text(
                    "It was started but never came up.\n\n"
                    + (reason or "There is nothing in the logs to explain why. "
                                 "Try starting it from a terminal to see the error:\n"
                                 "sudo systemctl start hydroc-server"))
                self.primary.set_label("Try again")
                self.primary.set_sensitive(True)
                self._waited = -1
        return GLib.SOURCE_CONTINUE


class App(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)

    def do_activate(self) -> None:
        win = self.props.active_window or Window(self)
        win.present()


def main() -> int:
    return App().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
