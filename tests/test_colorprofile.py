# SPDX-License-Identifier: MIT
"""The factory colour profile: the right lookup, and nothing but a profile stored.

The lookup names must match Control Center's byte for byte or the server
answers 404 for a profile that exists. And the reply comes over plain HTTP, so
the realistic failure is an error page served with a 200 -- that must never be
stored as a monitor profile.
"""

import json
import os
import stat
import struct
import tempfile
import unittest

from hydroc import colorprofile as cp


def edid(letters="BOE", product=(0x87, 0x0B), name=b"NE160QDM-NZA"):
    m = 0
    for ch in letters:
        m = (m << 5) | (ord(ch) - 64)
    d = bytearray(128)
    d[0:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    d[8], d[9] = m >> 8, m & 0xFF
    d[10], d[11] = product
    d[90:108] = b"\x00\x00\x00\xfe\x00" + name.ljust(13, b"\n")[:13]
    return bytes(d)


def icc(desc=b"factory"):
    """A minimal well-formed v2 display profile with a desc tag."""
    tag = b"desc" + b"\0" * 4 + struct.pack(">I", len(desc) + 1) + desc + b"\0"
    body = struct.pack(">I", 1) + struct.pack(">4sII", b"desc", 144, len(tag)) + tag
    size = 128 + len(body)
    head = bytearray(128)
    head[0:4] = struct.pack(">I", size)
    head[8], head[9] = 2, 0x10
    head[12:16], head[16:20], head[20:24] = b"mntr", b"RGB ", b"XYZ "
    head[36:40] = b"acsp"
    return bytes(head) + body


class PanelIdTest(unittest.TestCase):

    def test_matches_the_windows_hardware_id(self):
        self.assertEqual(cp.panel_id(edid()), "BOE0B87")
        self.assertEqual(cp.panel_name(edid()), "NE160QDM-NZA")

    def test_not_an_edid(self):
        self.assertIsNone(cp.panel_id(b""))
        self.assertIsNone(cp.panel_id(b"\0" * 128))

    def test_internal_panel_skips_the_empty_edp(self):
        with tempfile.TemporaryDirectory() as d:
            for conn, data in (("card0-eDP-2", b""), ("card1-eDP-1", edid()),
                               ("card1-HDMI-A-1", edid("DEL"))):
                os.makedirs(os.path.join(d, conn))
                with open(os.path.join(d, conn, "edid"), "wb") as fh:
                    fh.write(data)
            p = cp.internal_panel(d)
        self.assertEqual((p["connector"], p["panel_id"]), ("eDP-1", "BOE0B87"))


class LookupTest(unittest.TestCase):

    def test_serial_first_then_mac(self):
        self.assertEqual(cp.candidate_names("BOE0B87", "SN1", "B025AA71AEEC"),
                         ["BOE0B87_SN1", "BOE0B87_B025AA71AEEC"])

    def test_missing_identifiers_are_skipped(self):
        self.assertEqual(cp.candidate_names("BOE0B87", None, "MAC"), ["BOE0B87_MAC"])

    def test_placeholder_serial_is_no_serial(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("Default string\n")
        try:
            self.assertIsNone(cp.system_serial(fh.name))
        finally:
            os.unlink(fh.name)

    def test_mac_is_the_wired_nic_as_dotnet_prints_it(self):
        with tempfile.TemporaryDirectory() as d:
            def nic(name, mac, wireless=False, physical=True):
                base = os.path.join(d, name)
                os.makedirs(base)
                if physical:
                    os.makedirs(os.path.join(base, "device"))
                if wireless:
                    os.makedirs(os.path.join(base, "wireless"))
                for f, v in (("type", "1"), ("address", mac)):
                    with open(os.path.join(base, f), "w") as fh:
                        fh.write(v + "\n")
            nic("docker0", "02:42:ac:11:00:02", physical=False)
            nic("enp59s0", "b0:25:aa:71:ae:ec")
            nic("aaawlan0", "11:22:33:44:55:66", wireless=True)
            self.assertEqual(cp.ethernet_mac(d), "B025AA71AEEC")


class FetchTest(unittest.TestCase):

    def test_falls_back_from_serial_to_mac(self):
        asked = []

        def get(url, timeout):
            asked.append(url)
            return icc() if url.endswith("_MAC") else None
        name, data, header = cp.fetch("BOE0B87", "SN1", "MAC", get=get)
        self.assertEqual(asked, [cp.SERVER + "BOE0B87_SN1", cp.SERVER + "BOE0B87_MAC"])
        self.assertEqual(name, "BOE0B87_MAC")
        self.assertEqual(header["description"], "factory")

    def test_unknown_machine_is_an_error_not_an_empty_profile(self):
        with self.assertRaises(cp.ColorProfileError):
            cp.fetch("BOE0B87", "SN1", "MAC", get=lambda u, t: None)

    def test_an_error_page_is_never_accepted(self):
        page = b"<html><body>Service Unavailable</body></html>" * 4
        with self.assertRaises(cp.ColorProfileError):
            cp.fetch("BOE0B87", "SN1", None, get=lambda u, t: page)

    def test_a_consistent_size_alone_does_not_make_a_profile(self):
        fake = bytearray(icc())
        fake[36:40] = b"html"
        with self.assertRaises(cp.ColorProfileError):
            cp.parse_header(bytes(fake))

    def test_truncated_profile_is_rejected(self):
        with self.assertRaises(cp.ColorProfileError):
            cp.parse_header(icc()[:-4])


class StoreTest(unittest.TestCase):

    def test_store_is_world_readable_and_reported(self):
        with tempfile.TemporaryDirectory() as d:
            drm = os.path.join(d, "drm", "card1-eDP-1")
            os.makedirs(drm)
            with open(os.path.join(drm, "edid"), "wb") as fh:
                fh.write(edid())
            store = os.path.join(d, "icc")
            path = cp.store("BOE0B87", icc(), "test", store)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o644)
            st = cp.status(os.path.join(d, "drm"), store)
            self.assertEqual(st["stored"]["path"], path)
            self.assertEqual(st["stored"]["source"], "test")
            self.assertIn(path, st["hyprland_rule"])

    def test_store_refuses_a_non_profile(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(cp.ColorProfileError):
                cp.store("BOE0B87", b"not a profile" * 20, "test", d)
            self.assertEqual(os.listdir(d), [])


LUA = """-- Generated by hyprmoncfg

hl.monitor({
  output = "desc:BOE 0x0B87",
  scale = 1.6,
})

hl.monitor({
  output = "desc:Samsung Electric Company Odyssey G95C",
  scale = 1,
})

hl.workspace_rule({ workspace = "1", monitor = "desc:BOE 0x0B87" })
"""

CONF = """monitorv2 {
  output = desc:BOE 0x0B87
  scale = 1.6
  icc = /old.icc
}
"""


class HyprmoncfgTest(unittest.TestCase):
    """hyprmoncfg regenerates the live file on every display change, so the
    setting must land in its saved profiles -- and only in this panel's."""

    def test_lua_rule_gains_icc_only_in_the_panel_block(self):
        out = cp.set_rule_icc(LUA, "BOE0B87", "/p.icc")
        boe, samsung = out.split("hl.monitor(")[1:3]
        self.assertIn('icc = "/p.icc",', boe)
        self.assertNotIn("icc", samsung)
        self.assertEqual(out.count("icc ="), 1)

    def test_conf_rule_replaces_an_existing_icc(self):
        out = cp.set_rule_icc(CONF, "BOE0B87", "/p.icc")
        self.assertIn("  icc = /p.icc", out)
        self.assertNotIn("/old.icc", out)

    def test_profiles_and_live_file_are_updated_with_backups(self):
        with tempfile.TemporaryDirectory() as d:
            pdir = os.path.join(d, "profiles")
            os.makedirs(pdir)
            prof = {"name": "Home", "outputs": [
                {"make": "BOE", "model": "0x0B87", "scale": 1.6},
                {"make": "Samsung Electric Company", "model": "Odyssey G95C"}]}
            other = {"name": "Desk", "outputs": [{"make": "DEL", "model": "U2720Q"}]}
            for name, data in (("home", prof), ("desk", other)):
                with open(os.path.join(pdir, name + ".json"), "w") as fh:
                    json.dump(data, fh)
            with open(os.path.join(pdir, "home.lua"), "w") as fh:
                fh.write(LUA)
            live = os.path.join(d, "live.lua")
            with open(live, "w") as fh:
                fh.write(LUA)

            res = cp.apply_hyprmoncfg("/p.icc", "BOE0B87", d, (live,))
            self.assertEqual(res["profiles"], ["Home"])
            self.assertEqual(res["live"], live)
            with open(os.path.join(pdir, "home.json")) as fh:
                outs = json.load(fh)["outputs"]
            self.assertEqual(outs[0]["icc"], "/p.icc")
            self.assertNotIn("icc", outs[1])
            with open(os.path.join(pdir, "desk.json")) as fh:
                self.assertEqual(json.load(fh), other)
            for f in ("home.json", "home.lua"):
                self.assertTrue(os.path.exists(os.path.join(pdir, f + ".hydroc-bak")))
            self.assertTrue(os.path.exists(live + ".hydroc-bak"))

            again = cp.apply_hyprmoncfg("/p.icc", "BOE0B87", d, (live,))
            self.assertEqual((again["profiles"], again["unchanged"], again["live"]),
                             ([], ["Home"], None))


if __name__ == "__main__":
    unittest.main()
