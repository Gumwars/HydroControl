# SPDX-License-Identifier: MIT
"""
hydroc.colorprofile — the panel's factory colour calibration.

Each unit's panel was measured at the factory and the result is an ICC profile
held on Uniwill's server, looked up per machine. Control Center's Display ->
Color Management -> "Restore" is nothing more than fetching it. Decompiled
(`GCUService.exe`, `ColorProfileInfo` / `MySettingManager.DownloadProfile`):

    GET http://iccprofile.uniwill.com.tw/api/iccprofile/<PANEL>_<SERIAL>
    GET http://iccprofile.uniwill.com.tw/api/iccprofile/<PANEL>_<MAC>

    PANEL   EDID PnP ID + product code, as Windows writes it in the monitor's
            HardwareID: "BOE0B87" for this machine's NE160QDM-NZA
    SERIAL  SMBIOS system serial number -- /sys/class/dmi/id/product_serial
    MAC     the first wired NIC, uppercase hex, no separators -- .NET's
            PhysicalAddress.ToString()

Both are tried and the first non-empty reply wins; a 404 is Control Center's
"Serial number is not recognized". It then calls WcsSetDefaultColorProfile,
which on Linux is the desktop's business, not the daemon's.

The split follows from that. Fetching needs root, because product_serial is
mode 0400, so the daemon fetches and stores the profile world-readable under
STORE_DIR. *Using* it belongs to the user session -- Hyprland reads it from a
monitor rule, GNOME and KDE through colord -- so this module only reports what
to do there and never edits a compositor's config.

The fetch is plain HTTP because that is all the server offers. Nothing about
the reply is trusted beyond being bytes: it is checked to be a well-formed ICC
display profile before it is stored, and it is never executed or parsed past
the header.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import time
import urllib.error
import urllib.request

SERVER = "http://iccprofile.uniwill.com.tw/api/iccprofile/"
DRM = "/sys/class/drm"
NET = "/sys/class/net"
SERIAL = "/sys/class/dmi/id/product_serial"
STORE_DIR = "/var/lib/hydroc/icc"
MAX_SIZE = 4 * 1024 * 1024                  # a display profile is a few KB


class ColorProfileError(Exception):
    pass


# --- identity ----------------------------------------------------------------

def panel_id(edid: bytes) -> str | None:
    """"BOE0B87" from an EDID, or None if it is not one.

    Three 5-bit letters packed big-endian in bytes 8-9, then the product code
    little-endian in bytes 10-11, printed as four uppercase hex digits --
    exactly what Windows puts after MONITOR\\ in the HardwareID.
    """
    if len(edid) < 128 or edid[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        return None
    m = (edid[8] << 8) | edid[9]
    letters = "".join(chr(((m >> s) & 0x1F) + 64) for s in (10, 5, 0))
    if not letters.isalpha():
        return None
    return f"{letters}{edid[10] | (edid[11] << 8):04X}"


def panel_name(edid: bytes) -> str | None:
    """The 0xFE (unspecified text) or 0xFC (name) descriptor, if present."""
    for off in range(54, 126, 18):
        d = edid[off:off + 18]
        if len(d) == 18 and d[:3] == b"\x00\x00\x00" and d[3] in (0xFC, 0xFE):
            return d[5:].split(b"\n")[0].decode("ascii", "replace").strip() or None
    return None


def internal_panel(drm: str = DRM) -> dict | None:
    """{"connector", "panel_id", "name"} for the built-in panel.

    eDP connectors with an empty EDID are skipped: on a hybrid machine the
    panel is listed under both GPUs and only the one driving it has the EDID.
    """
    try:
        entries = sorted(os.listdir(drm))
    except OSError:
        return None
    for entry in entries:
        if "-eDP-" not in entry:
            continue
        try:
            with open(os.path.join(drm, entry, "edid"), "rb") as fh:
                edid = fh.read()
        except OSError:
            continue
        pid = panel_id(edid)
        if pid:
            return {"connector": entry.split("-", 1)[1], "panel_id": pid,
                    "name": panel_name(edid)}
    return None


def system_serial(path: str = SERIAL) -> str | None:
    try:
        with open(path) as fh:
            s = fh.read().strip()
    except OSError:
        return None
    # Placeholder serials would fetch someone else's panel, or nobody's.
    if not s or s.lower() in ("default string", "to be filled by o.e.m.",
                              "system serial number", "0123456789"):
        return None
    return s


def ethernet_mac(net: str = NET) -> str | None:
    """First physical wired NIC as .NET prints it: "B025AA71AEEC"."""
    try:
        names = sorted(os.listdir(net))
    except OSError:
        return None
    for name in names:
        base = os.path.join(net, name)
        if not os.path.exists(os.path.join(base, "device")):
            continue                                   # virtual
        if os.path.exists(os.path.join(base, "wireless")) or \
                os.path.exists(os.path.join(base, "phy80211")):
            continue                                   # Windows: Wireless80211
        try:
            with open(os.path.join(base, "type")) as fh:
                if fh.read().strip() != "1":           # ARPHRD_ETHER
                    continue
            with open(os.path.join(base, "address")) as fh:
                mac = fh.read().strip().replace(":", "").upper()
        except OSError:
            continue
        if len(mac) == 12 and mac != "000000000000":
            return mac
    return None


def candidate_names(panel: str, serial: str | None,
                    mac: str | None) -> list[str]:
    """The names Control Center asks the server for, in its order."""
    return [f"{panel}_{x}" for x in (serial, mac) if x]


# --- validation ----------------------------------------------------------------

def parse_header(data: bytes) -> dict:
    """Check `data` is an ICC profile and return the header fields we show.

    Raises ColorProfileError otherwise. An HTML error page served with a 200
    is the realistic failure, and it must not end up as a monitor profile.
    """
    if len(data) < 132:
        raise ColorProfileError(f"{len(data)} bytes is too short for an ICC profile")
    if data[36:40] != b"acsp":
        raise ColorProfileError("not an ICC profile (no 'acsp' signature)")
    size = struct.unpack(">I", data[0:4])[0]
    if size != len(data):
        raise ColorProfileError(f"header says {size} bytes, got {len(data)}")
    major, minor = data[8], data[9] >> 4
    return {
        "size": size,
        "version": f"{major}.{minor}",
        "class": data[12:16].decode("ascii", "replace"),
        "colour_space": data[16:20].decode("ascii", "replace").strip(),
        "pcs": data[20:24].decode("ascii", "replace").strip(),
        "description": _description(data),
    }


def _description(data: bytes) -> str | None:
    """The 'desc' tag text, v2 (desc) or v4 (mluc). Best effort."""
    try:
        count = struct.unpack(">I", data[128:132])[0]
        for i in range(min(count, 100)):
            sig, off, ln = struct.unpack(">4sII", data[132 + 12 * i:144 + 12 * i])
            if sig != b"desc":
                continue
            tag = data[off:off + ln]
            if tag[:4] == b"desc":
                n = struct.unpack(">I", tag[8:12])[0]
                return tag[12:12 + n].split(b"\0")[0].decode("ascii", "replace") or None
            if tag[:4] == b"mluc":
                rlen, roff = struct.unpack(">II", tag[20:28])
                return tag[roff:roff + rlen].decode("utf-16-be", "replace") or None
    except (struct.error, IndexError):
        pass
    return None


# --- fetch and store -----------------------------------------------------------

def _get(url: str, timeout: float) -> bytes | None:
    """Body of a 200, None for a 404. Anything else raises."""
    req = urllib.request.Request(url, headers={"User-Agent": "hydroc"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(MAX_SIZE + 1)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise ColorProfileError(f"server replied HTTP {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        raise ColorProfileError(f"network error: {getattr(e, 'reason', e)}") from e


def fetch(panel: str, serial: str | None, mac: str | None,
          timeout: float = 15.0, get=_get) -> tuple[str, bytes, dict]:
    """(name that answered, profile bytes, header) or ColorProfileError."""
    names = candidate_names(panel, serial, mac)
    if not names:
        raise ColorProfileError("no system serial number or wired MAC to look up "
                                "(the serial needs root)")
    tried = []
    for name in names:
        data = get(SERVER + name, timeout)
        tried.append("serial" if serial and name.endswith("_" + serial) else "MAC")
        if not data:
            continue
        if len(data) > MAX_SIZE:
            raise ColorProfileError("reply is too large to be a display profile")
        return name, data, parse_header(data)
    raise ColorProfileError(
        f"the server has no profile for this {panel} panel "
        f"(looked up by {' and '.join(tried)}) -- Control Center reports this "
        "as 'Serial number is not recognized'")


def stored_path(panel: str, store: str = STORE_DIR) -> str:
    return os.path.join(store, f"{panel}-factory.icc")


def store(panel: str, data: bytes, source: str,
          store_dir: str = STORE_DIR) -> str:
    """Write the profile world-readable, with a small sidecar saying where from."""
    header = parse_header(data)
    os.makedirs(store_dir, mode=0o755, exist_ok=True)
    dest = stored_path(panel, store_dir)
    tmp = dest + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.chmod(tmp, 0o644)
    os.replace(tmp, dest)
    meta = {"panel_id": panel, "source": source, "stored": int(time.time()),
            **header}
    with open(dest + ".json", "w") as fh:
        json.dump(meta, fh, indent=2)
        fh.write("\n")
    os.chmod(dest + ".json", 0o644)
    return dest


def import_file(path: str, panel: str, store_dir: str = STORE_DIR) -> str:
    """Store a profile you already have -- e.g. the .icm from a Windows install."""
    with open(path, "rb") as fh:
        data = fh.read(MAX_SIZE + 1)
    if len(data) > MAX_SIZE:
        raise ColorProfileError(f"{path} is too large to be a display profile")
    return store(panel, data, f"imported from {os.path.basename(path)}", store_dir)


def status(drm: str = DRM, store_dir: str = STORE_DIR) -> dict:
    panel = internal_panel(drm)
    out = {"panel": panel, "stored": None, "hyprland_rule": None}
    if not panel:
        out["error"] = "built-in panel not found (no eDP connector with an EDID)"
        return out
    path = stored_path(panel["panel_id"], store_dir)
    if os.path.exists(path):
        meta = {}
        try:
            with open(path + ".json") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            pass
        out["stored"] = {"path": path, **meta}
        out["hyprland_rule"] = hyprland_rule(panel, path)
    return out


# --- using it: the user session's job ------------------------------------------

def hyprland_rule(panel: dict, path: str) -> str:
    """The `icc` field to add to the panel's existing hl.monitor() rule.

    Matched by description, as hyprmoncfg writes it (vendor, space, product
    code in hex), so it
    survives the connector being renamed when the GPU mode changes.
    """
    pid = panel["panel_id"]
    return f'icc = "{path}",  -- in the hl.monitor rule for "desc:{pid[:3]} 0x{pid[3:]}"'


# hyprmoncfg owns the monitor rules on a machine that uses it, and its daemon
# regenerates the live file on every display change -- so an `icc` line added
# there by hand is gone at the next hotplug. Its saved profiles carry ICC per
# output (`"icc"` in the JSON, rendered as `icc = ...` into the rule), and the
# daemon re-reads them at every reconciliation. That is where it has to go.
#
# The catch: with no saved profile matching the connected displays, the daemon
# renders a draft from live Hyprland state, and Hyprland does not report ICC.
# So the profile only sticks in layouts that are saved as profiles.

HYPRMONCFG_DIR = "~/.config/hyprmoncfg"
HYPRMONCFG_TARGETS = ("~/.config/hypr/hyprmoncfg-monitors.lua",
                      "~/.config/hypr/hyprmoncfg-monitors.conf")


def _desc(panel_id: str) -> tuple[str, str]:
    """(make, model) as Hyprland and hyprmoncfg name them: vendor, then "0x" + product."""
    return panel_id[:3], "0x" + panel_id[3:]


def _backup_once(path: str) -> None:
    bak = path + ".hydroc-bak"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)


def _write_atomic(path: str, text: str) -> None:
    tmp = path + ".hydroc-tmp"
    with open(tmp, "w") as fh:
        fh.write(text)
    shutil.copymode(path, tmp)
    os.replace(tmp, path)


def set_rule_icc(text: str, panel_id: str, icc: str) -> str:
    """Put `icc` into this panel's monitor block of a rendered rule file.

    Handles hyprmoncfg's two renderings: Lua `hl.monitor({ ... })` and
    hyprlang `monitorv2 { ... }`. Replaces an existing icc line, otherwise
    adds one just before the block closes. Other blocks are untouched.
    """
    make, model = _desc(panel_id)
    target = f"desc:{make} {model}"
    lines = text.split("\n")
    out, in_block, lua, has_icc = [], False, False, False
    for line in lines:
        s = line.strip()
        if not in_block and target in s and s.startswith("output"):
            # The opening line precedes this; the block is the panel's.
            in_block, has_icc = True, False
            lua = s.endswith(",")
        elif in_block and s.startswith("icc"):
            line = f'  icc = "{icc}",' if lua else f"  icc = {icc}"
            has_icc = True
        elif in_block and s in ("})", "}"):
            if not has_icc:
                out.append(f'  icc = "{icc}",' if lua else f"  icc = {icc}")
            in_block = False
        out.append(line)
    return "\n".join(out)


def apply_hyprmoncfg(icc: str, panel_id: str, config_dir: str | None = None,
                     targets: tuple[str, ...] | None = None) -> dict:
    """Give this panel `icc` in every saved hyprmoncfg profile, and in the
    live generated file so it takes effect now. Backs each file up once
    (`*.hydroc-bak`) before the first change. Runs as the user.

    Returns {"profiles": [names changed], "unchanged": [...], "live": path|None}.
    """
    cdir = os.path.expanduser(config_dir or HYPRMONCFG_DIR)
    pdir = os.path.join(cdir, "profiles")
    if not os.path.isdir(pdir):
        raise ColorProfileError(f"no hyprmoncfg profiles in {pdir}")
    make, model = _desc(panel_id)
    result = {"profiles": [], "unchanged": [], "live": None}

    for fn in sorted(os.listdir(pdir)):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(pdir, fn)
        with open(path) as fh:
            prof = json.load(fh)
        hit = changed = False
        for o in prof.get("outputs", []):
            if (o.get("make"), o.get("model")) == (make, model):
                hit = True
                if o.get("icc") != icc:
                    o["icc"] = icc
                    changed = True
        name = prof.get("name", fn[:-5])
        if not hit:
            continue
        if not changed:
            result["unchanged"].append(name)
            continue
        _backup_once(path)
        _write_atomic(path, json.dumps(prof, indent=2) + "\n")
        # The .lua/.conf sidecars are hyprmoncfg's renderings of the JSON for
        # people who stop using it. Keep them truthful.
        for ext in (".lua", ".conf"):
            side = path[:-5] + ext
            if os.path.exists(side):
                with open(side) as fh:
                    text = fh.read()
                new = set_rule_icc(text, panel_id, icc)
                if new != text:
                    _backup_once(side)
                    _write_atomic(side, new)
        result["profiles"].append(name)

    for t in targets or HYPRMONCFG_TARGETS:
        live = os.path.expanduser(t)
        if not os.path.exists(live):
            continue
        with open(live) as fh:
            text = fh.read()
        new = set_rule_icc(text, panel_id, icc)
        if new != text:
            _backup_once(live)
            _write_atomic(live, new)
            result["live"] = live
        break
    return result


def install_user(path: str, home: str | None = None) -> str:
    """Copy into ~/.local/share/icc, where colord and most desktops look."""
    home = home or os.path.expanduser("~")
    dest_dir = os.path.join(home, ".local", "share", "icc")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(path))
    shutil.copyfile(path, dest)
    return dest
