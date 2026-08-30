# SPDX-License-Identifier: MIT
"""
hydroc.lppd — sidecar daemon owning the Bluetooth LE link to the LPP dock.

Why this is a separate process rather than part of `hydroc.server`:

  * BLE is async. `bleak` requires an asyncio loop; the HTTP bridge is a
    synchronous ThreadingHTTPServer holding the EC lock. Marrying them would
    put a reconnect backoff in the same process as EC access.
  * The dock is optional hardware. Most owners do not have one, and `bleak`
    should not become a hard import for a machine that will never use it.
  * One owner per device. A single process holds the BLE connection, the same
    rule the chin bar follows (DESIGN.md §"One owner per device").

The UI reaches it through `hydroc.server`, which proxies /api/lpp/* to the
Unix socket below. If this daemon is not running, the UI hides the section --
that is the whole failure mode, and nothing else degrades.

    sudo python3 -m hydroc.lppd

Protocol credit: LibreLPP (https://github.com/Sebastian-Alexis/LibreLPP, MIT,
Copyright (c) 2024 sra). See hydroc/lpp.py.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time

from . import lpp

SOCKET_PATH = "/run/hydroc/lpp.sock"
STATE_PATH = "/etc/hydroc/lpp.json"

SCAN_TIMEOUT = 8.0
KEEPALIVE_SECONDS = 30.0
RECONNECT_BACKOFF = (2, 5, 10, 20, 30, 60)   # seconds, then repeats the last


def log(msg: str) -> None:
    print(f"lppd: {msg}", flush=True)


class Dock:
    """The BLE link and the intent we want the dock to be in.

    The dock forgets its settings when it loses power, exactly like the EC
    forgets its power limits -- so the state file here is *intent*, and it is
    re-sent on every successful connect. That is the same split the rest of
    the project draws between a saved profile and live hardware.
    """

    def __init__(self) -> None:
        self.client = None
        self.connected = False
        self.address: str | None = None
        self.fan: int = 60
        self.pump: str = "medium"
        self.last_error: str | None = None
        self.last_notification: str | None = None
        self.notifications: list[dict] = []      # ring buffer, newest last
        self.connected_since: float | None = None
        self._load_state()

    # -- intent ------------------------------------------------------------

    def _load_state(self) -> None:
        try:
            with open(STATE_PATH) as fh:
                saved = json.load(fh)
        except (OSError, ValueError):
            return
        addr = saved.get("address")
        if isinstance(addr, str) and addr:
            self.address = addr
        if isinstance(saved.get("fan"), int):
            self.fan = max(lpp.FAN_MIN, min(lpp.FAN_MAX, saved["fan"]))
        pump = saved.get("pump")
        if pump in lpp.PUMP_MODES:
            self.pump = pump
        elif pump in lpp.PUMP_MODES_REV:       # a raw code from an older file
            self.pump = lpp.PUMP_MODES_REV[pump]

    def _save_state(self) -> None:
        try:
            os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
            tmp = STATE_PATH + ".tmp"
            with open(tmp, "w") as fh:
                json.dump({"fan": self.fan, "pump": self.pump,
                           "address": self.address}, fh, indent=2)
                fh.write("\n")
            os.replace(tmp, STATE_PATH)        # never leave a half-written file
        except OSError as e:
            log(f"could not save state: {e}")

    # -- transport ---------------------------------------------------------

    def _on_disconnect(self, _client) -> None:
        log("disconnected")
        self.connected = False
        self.connected_since = None

    def _on_notify(self, _sender, data: bytearray) -> None:
        """Capture what the dock says, so the channel can be decoded later.

        Nothing is known about this payload -- LibreLPP logs these and never
        parses them. But Control Center has `strPumpStatus` and `LCPUMP_DUTY`,
        so the dock evidently *does* report state, and this is where it would
        arrive. Keep a short history with timestamps so frames can be
        correlated against commands we sent, which is a far cheaper route to
        the format than disassembling a .NET Native binary.
        """
        frame = bytes(data)
        self.last_notification = frame.hex(" ")
        self.notifications.append({"t": round(time.time(), 3),
                                   "hex": frame.hex(" "), "len": len(frame)})
        del self.notifications[:-40]          # keep the last 40

    async def send(self, frame: bytes) -> bool:
        """Write one frame. False if the link is down.

        The NUS TX characteristic is write-without-response, so a True here
        means "handed to the adapter", NOT "the dock acted on it". There is no
        acknowledgement to check, and status() says so rather than implying a
        readback this transport cannot provide.
        """
        if not self.connected or self.client is None:
            return False
        try:
            await self.client.write_gatt_char(lpp.NUS_TX_UUID, frame, response=False)
            return True
        except Exception as e:
            self.last_error = str(e)
            self.connected = False
            return False

    @staticmethod
    def _dbus_path(address: str) -> str | None:
        """BlueZ's D-Bus object path for a known address, if it exists.

        `/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF`. Confirmed against the live
        object tree rather than assumed, because a path we invent is exactly
        the kind of plausible-but-unchecked value this project keeps getting
        burned by.
        """
        node = "dev_" + address.upper().replace(":", "_")
        try:
            r = subprocess.run(["busctl", "--system", "tree", "org.bluez"],
                               capture_output=True, text=True, timeout=15)
        except Exception:
            return None
        for line in r.stdout.splitlines():
            # tree output is drawn with box characters; take the path itself
            m = line.strip().split("/org/bluez/")
            if len(m) < 2:
                continue
            path = "/org/bluez/" + m[1].strip()
            if path.endswith(node):
                return path
        return None

    async def _attach_by_path(self, address: str, path: str) -> bool:
        """Connect using BlueZ's existing device object, skipping discovery.

        bleak's BlueZ backend only scans when it does not already know the
        D-Bus path:

            if self._device_path is None:
                device = await BleakScanner.find_device_by_address(...)

        Handing it a BLEDevice whose details carry the path takes that branch
        out of play. That matters because this dock stops advertising once it
        has been connected and dropped, so discovery can never find it again
        even though BlueZ still holds a complete device object, GATT services
        and all.
        """
        from bleak import BleakClient
        from bleak.backends.device import BLEDevice
        try:
            dev = BLEDevice(address, lpp.DEVICE_NAME,
                            {"path": path, "props": {}})
            self.client = BleakClient(dev,
                                      disconnected_callback=self._on_disconnect)
            await self.client.connect()
            return True
        except Exception as e:
            self.last_error = f"attach via {path} failed: {e}"
            self.client = None
            return False

    @staticmethod
    def _bluez_connect(address: str) -> bool:
        """Ask BlueZ to open a directed connection to a known address.

        bleak's connect() needs the device present in BlueZ's discovered-object
        tree, so it fails with "Device ... was not found" for a dock that is
        cached but not advertising. This dock stops advertising once it has
        been connected and then disconnected -- which made every sidecar
        restart look like it needed a power cycle. `bluetoothctl connect` does
        a directed connection from the cache and succeeds where a scan cannot,
        and once BlueZ holds the link bleak can attach to it normally.
        """
        try:
            r = subprocess.run(["bluetoothctl", "connect", address],
                               capture_output=True, text=True, timeout=30)
            return "successful" in (r.stdout + r.stderr).lower()
        except Exception:
            return False

    async def _attach(self, address: str) -> bool:
        """Open the link to one address. Sets last_error on failure."""
        from bleak import BleakClient
        try:
            self.client = BleakClient(address,
                                      disconnected_callback=self._on_disconnect)
            await self.client.connect()
            return True
        except Exception as e:
            self.last_error = (f"connect to {address} failed: {e}. If LibreLPP is "
                               "installed, stop it: systemctl --user stop lpp-daemon")
            self.client = None
            return False

    @staticmethod
    def _known_addresses() -> list[str]:
        """Addresses BlueZ already knows about, newest listing first.

        Covers the case this daemon cannot otherwise reach: a dock that is
        already connected (so it does not advertise and a scan cannot see it)
        and whose address we have not saved yet. bluetoothctl is not elegant,
        but it needs no extra dependency and it is what BlueZ will answer with.
        """
        out: list[str] = []
        for args in (["devices", "Connected"], ["devices", "Paired"], ["devices"]):
            try:
                r = subprocess.run(["bluetoothctl", *args], capture_output=True,
                                   text=True, timeout=10)
            except Exception:
                continue
            for line in r.stdout.splitlines():
                parts = line.split(maxsplit=2)
                if len(parts) >= 3 and parts[0] == "Device" \
                        and lpp.DEVICE_NAME.lower() in parts[2].lower():
                    if parts[1] not in out:
                        out.append(parts[1])
        return out

    async def connect(self) -> bool:
        from bleak import BleakScanner

        want_mac = os.environ.get("HYDROC_LPP_MAC", "").strip().lower()

        # Try a known address FIRST, before scanning.
        #
        # A BLE peripheral that is already connected stops advertising, so
        # `discover()` cannot see it -- and BlueZ keeps the link up after this
        # daemon exits. Restarting the sidecar therefore scanned, found
        # nothing, and gave up while a perfectly good connection sat idle.
        # Connecting by address works whether or not the dock is advertising.
        candidates = [want_mac or None, self.address] + self._known_addresses()
        for candidate in dict.fromkeys(c for c in candidates if c):
            log(f"trying {candidate} ...")
            if await self._attach(candidate):
                self.address = candidate
                break
            log(f"  {self.last_error}")          # never fail silently

            # bleak could not see it, because it insists on discovery and this
            # dock does not advertise once it has been connected and dropped.
            # Go around discovery: have BlueZ open the link, then attach to the
            # D-Bus object BlueZ already has.
            log(f"  asking BlueZ to connect {candidate} directly ...")
            self._bluez_connect(candidate)
            path = self._dbus_path(candidate)
            if path:
                log(f"  attaching via {path}")
                if await self._attach_by_path(candidate, path):
                    self.address = candidate
                    log("  attached through BlueZ's existing device object")
                    break
                log(f"  {self.last_error}")
            else:
                log("  BlueZ has no device object for it")
        else:
            try:
                found = await BleakScanner.discover(timeout=SCAN_TIMEOUT)
            except Exception as e:
                self.last_error = f"BLE scan failed: {e}"
                return False

            address = None
            for d in found:
                if want_mac and d.address.lower() == want_mac:
                    address = d.address
                    break
                if not want_mac and d.name and lpp.DEVICE_NAME.lower() in d.name.lower():
                    address = d.address
                    break

            if address is None:
                # BlueZ caching the device is not the same as the device being
                # reachable: it keeps previously-seen addresses long after the
                # dock has dropped its link and stopped advertising.
                cached = "known to BlueZ but not advertising" \
                    if self._known_addresses() else "not known to BlueZ at all"
                self.last_error = (
                    f"no BLE device named {lpp.DEVICE_NAME!r} is advertising "
                    f"({cached}). The dock stops advertising once it has been "
                    "connected and idle -- POWER CYCLE THE DOCK to wake its "
                    "radio, then this will find it. Set HYDROC_LPP_MAC to "
                    "target a specific address."
                    if not want_mac else f"no BLE device at {want_mac}")
                return False

            if not await self._attach(address):
                return False
            self.address = address
        self.connected = True
        self.connected_since = time.time()
        self.last_error = None
        self._save_state()          # remember the address for next time
        log(f"connected to {self.address}")

        try:
            await self.client.start_notify(lpp.NUS_RX_UUID, self._on_notify)
        except Exception as e:
            log(f"notifications unavailable: {e}")     # not fatal

        await self.initialise()
        await self.reapply()
        return True

    async def initialise(self) -> None:
        """The dock ignores commands until it has seen the init sequence.

        The framed commands go twice -- the first pass is unreliable on a cold
        connection -- but the trailing `sw` goes exactly ONCE.

        We originally sent it on both passes and the dock connected, accepted
        the init, and then ignored every fan and pump command. `sw` reads as
        *switch*: sending it a second time appears to toggle the dock back out
        of the mode the first one selected. LibreLPP sends `init_cmds` then
        `init_cmds[:-1]`, and that asymmetry is load-bearing, not an accident
        of how the loop was written.
        """
        for frame in lpp.INIT_SEQUENCE:
            await self.send(frame)
            await asyncio.sleep(0.1)
        await self.send(lpp.INIT_TRAILER)
        await asyncio.sleep(0.1)

        await asyncio.sleep(0.5)

        for frame in lpp.INIT_SEQUENCE:          # no trailer this time
            await self.send(frame)
            await asyncio.sleep(0.1)

    async def reapply(self) -> None:
        """Push saved intent back onto a dock that just came up blank."""
        await self.send(lpp.fan_frame(self.fan))
        await asyncio.sleep(0.1)
        await self.send(lpp.pump_frame(self.pump))
        log(f"re-applied fan {self.fan}%, pump {self.pump}")

    # -- operations --------------------------------------------------------

    async def set_fan(self, speed: int) -> bool:
        frame = lpp.fan_frame(speed)           # raises ValueError if out of range
        ok = await self.send(frame)
        if ok:
            self.fan = int(speed)
            self._save_state()
        return ok

    async def set_pump(self, mode: str) -> bool:
        frame = lpp.pump_frame(mode)
        ok = await self.send(frame)
        if ok:
            self.pump = mode
            self._save_state()
        return ok

    def status(self) -> dict:
        return {
            "present": self.connected,
            "address": self.address,
            "fan": self.fan,
            "pump": self.pump,
            "pump_modes": lpp.PUMP_ORDER,
            "uptime": round(time.time() - self.connected_since, 1)
                      if self.connected_since else None,
            "last_error": self.last_error,
            "last_notification": self.last_notification,
            "notifications": self.notifications,
            # The transport cannot confirm a write, so these values are what we
            # asked for, not what the dock reports. Say so where the UI can see it.
            "verified": False,
        }


async def handle(dock: Dock, reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter) -> None:
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        if not line:
            return
        try:
            req = json.loads(line)
        except ValueError:
            reply = {"ok": False, "error": "malformed JSON"}
        else:
            reply = await dispatch(dock, req)
        writer.write((json.dumps(reply) + "\n").encode())
        await writer.drain()
    except (asyncio.TimeoutError, ConnectionError):
        pass
    finally:
        writer.close()


async def dispatch(dock: Dock, req: dict) -> dict:
    op = req.get("op")

    if op == "status":
        return {"ok": True, **dock.status()}

    if op == "reconnect":
        if dock.client is not None:
            try:
                await dock.client.disconnect()
            except Exception:
                pass
        dock.connected = False
        ok = await dock.connect()
        return {"ok": ok, **dock.status()}

    if op == "set":
        if not dock.connected:
            return {"ok": False, "error": dock.last_error or "dock not connected",
                    **dock.status()}
        results = {}
        try:
            if req.get("fan") is not None:
                results["fan"] = await dock.set_fan(int(req["fan"]))
            if req.get("pump") is not None:
                results["pump"] = await dock.set_pump(str(req["pump"]))
        except ValueError as e:
            return {"ok": False, "error": str(e), **dock.status()}
        if not results:
            return {"ok": False, "error": "nothing to set", **dock.status()}
        return {"ok": all(results.values()), "sent": results, **dock.status()}

    return {"ok": False, "error": f"unknown op {op!r}"}


async def keepalive(dock: Dock) -> None:
    """Poll, and rebuild the link when it drops.

    The dock goes quiet rather than erroring when it wanders out of range, so
    a periodic status frame is the only thing that notices.
    """
    delay_idx = 0
    while True:
        if dock.connected:
            delay_idx = 0
            await dock.send(lpp.status_frame())
            await asyncio.sleep(KEEPALIVE_SECONDS)
            continue

        wait = RECONNECT_BACKOFF[min(delay_idx, len(RECONNECT_BACKOFF) - 1)]
        if await dock.connect():
            continue
        log(f"{dock.last_error} -- retrying in {wait}s")
        delay_idx += 1
        await asyncio.sleep(wait)


async def serve() -> int:
    try:
        import bleak      # noqa: F401
    except ImportError:
        # This runs as root under systemd, so bleak must be importable BY ROOT.
        # A --user pip install is invisible here -- the same trap that made the
        # keyboard library look missing (DESIGN.md §4.4). The remedy text lives
        # in hydroc.deps so this and install.sh cannot drift apart again.
        from .deps import remedy
        print("lppd: bleak is not importable by root -- LPP control unavailable.\n"
              "      " + remedy("bleak").replace("\n", "\n      "),
              file=sys.stderr)
        return 1

    dock = Dock()

    os.makedirs(os.path.dirname(SOCKET_PATH), exist_ok=True)
    if os.path.exists(SOCKET_PATH):
        # Is it stale, or is another sidecar alive on it? Unlinking blindly
        # let a hand-started copy silently steal the socket from the systemd
        # one -- both then held BLE connections, and killing the terminal took
        # the socket with it while the service kept running, unreachable.
        # One owner per device, and that has to include ourselves.
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(1.0)
        try:
            probe.connect(SOCKET_PATH)
        except OSError:
            os.unlink(SOCKET_PATH)      # genuinely stale
        else:
            probe.close()
            print(f"lppd: another sidecar is already running on {SOCKET_PATH}.\n"
                  "      Only one may hold the dock. If that is the service:\n"
                  "        sudo systemctl status hydroc-lpp\n"
                  "      To take over deliberately:\n"
                  "        sudo systemctl stop hydroc-lpp", file=sys.stderr)
            return 1
        finally:
            probe.close()
    server = await asyncio.start_unix_server(
        lambda r, w: handle(dock, r, w), path=SOCKET_PATH)
    os.chmod(SOCKET_PATH, 0o660)
    log(f"listening on {SOCKET_PATH}")

    task = asyncio.create_task(keepalive(dock))
    try:
        async with server:
            await server.serve_forever()
    finally:
        task.cancel()
        if dock.client is not None:
            try:
                await dock.client.disconnect()
            except Exception:
                pass
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
    return 0


def main() -> int:
    if os.geteuid() != 0:
        print("lppd: must run as root (it binds a socket in /run/hydroc)",
              file=sys.stderr)
        return 1
    try:
        return asyncio.run(serve())
    except KeyboardInterrupt:
        log("stopped")
        return 0


if __name__ == "__main__":
    sys.exit(main())
