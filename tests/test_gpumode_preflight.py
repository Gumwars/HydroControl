# SPDX-License-Identifier: MIT
"""The iGPU-only pre-flight check.

Reported 2026-09-10: switching a LUKS-encrypted machine to iGPU-only decrypted
the root filesystem and then never started a desktop session. Recovery was the
BIOS -- there is no Linux left to fix it from. That is the worst failure this
project can produce, because the app writes the setting and the consequence
arrives one reboot later on a black screen.

Two properties matter here.

It must fire on the real configuration that caused it: an EnvyControl
/etc/X11/xorg.conf pinning BusID "PCI:1:0:0", plus the fact that from dGPU-only
the integrated GPU is not on the bus at all, so nothing can verify it works
before the reboot that depends on it.

And it must not fire on anything else. These are heuristics over other people's
config files; a false positive gates a legitimate change behind a scary wall of
text, and the next one gets clicked through without reading. Dynamic and dGPU
are never checked, and a value naming mesa is not a pin.

No firmware and no real config files are touched.
"""

import os
import tempfile
import unittest
from unittest import mock

from hydroc import gpumode as gm


class Sandbox(unittest.TestCase):
    """Redirect every path preflight reads at a temp dir."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patch("XORG_CONFIGS", [])
        self.patch("XORG_GLOB", self.path("xorg.conf.d", "*.conf"))
        self.patch("MODPROBE_GLOB", self.path("modprobe.d", "*.conf"))
        self.patch("ENV_FILES", [])
        self.patch("ENV_GLOBS", [self.path("env.d", "*.conf")])
        # Both GPUs present by default: the machine is in Dynamic.
        self.topology(True, True)
        self.cmdline("quiet rw root=/dev/mapper/luks-x")

    def patch(self, name, value):
        p = mock.patch.object(gm, name, value)
        p.start()
        self.addCleanup(p.stop)

    def path(self, *parts):
        return os.path.join(self.tmp.name, *parts)

    def topology(self, igpu, dgpu):
        self.patch("topology", lambda: (igpu, dgpu))

    def cmdline(self, text):
        self._cmdline = text
        real = gm._reads

        def fake(path):
            return self._cmdline if path == "/proc/cmdline" else real(path)
        self.patch("_reads", fake)

    def put(self, subdir, name, text):
        d = self.path(subdir)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            fh.write(text)


class ScopeTest(Sandbox):
    """Only iGPU-only removes the GPU the running system was built against."""

    def test_dynamic_is_never_checked(self):
        self.put("xorg.conf.d", "10-nvidia.conf", 'Driver "nvidia"')
        self.assertEqual(gm.preflight("dynamic"), [])

    def test_dgpu_is_never_checked(self):
        self.put("xorg.conf.d", "10-nvidia.conf", 'Driver "nvidia"')
        self.assertEqual(gm.preflight("dgpu"), [])

    def test_a_clean_machine_reports_nothing(self):
        self.assertEqual(gm.preflight("igpu"), [])


class RiskTest(Sandbox):

    def names(self, mode="igpu"):
        return [r["name"] for r in gm.preflight(mode)]

    def test_absent_igpu_is_flagged(self):
        """From dGPU-only the Intel GPU is off the bus, so it cannot be tested
        before the reboot that depends on it."""
        self.topology(False, True)
        self.assertIn("the integrated GPU is not on the bus", self.names())

    def test_busid_in_an_xorg_config_is_flagged(self):
        """The actual EnvyControl output that caused the incident."""
        self.put("xorg.conf.d", "10-envy.conf",
                 'Section "Device"\n  Driver "nvidia"\n  BusID "PCI:1:0:0"\nEndSection')
        self.assertIn("an X config pins a specific GPU", self.names())

    def test_xorg_config_without_a_pin_is_not_flagged(self):
        self.put("xorg.conf.d", "00-keyboard.conf",
                 'Section "InputClass"\n  Driver "libinput"\nEndSection')
        self.assertNotIn("an X config pins a specific GPU", self.names())

    def test_env_pinned_to_nvidia_is_flagged(self):
        self.put("env.d", "10-gpu.conf", "GBM_BACKEND=nvidia-drm")
        self.assertIn("the environment pins the graphics stack", self.names())

    def test_env_pinned_to_a_card_node_is_flagged(self):
        self.put("env.d", "10-gpu.conf", "AQ_DRM_DEVICES=/dev/dri/card1")
        self.assertIn("the environment pins the graphics stack", self.names())

    def test_env_naming_mesa_is_not_a_pin(self):
        """The chwd rtd3 workaround sets a mesa fallback. Flagging it would be
        a false positive on a stock CachyOS install."""
        self.put("env.d", "10-gpu.conf",
                 "GBM_BACKEND=mesa\nexport __EGL_VENDOR_LIBRARY_FILENAMES="
                 "/usr/share/glvnd/egl_vendor.d/50_mesa.json")
        self.assertNotIn("the environment pins the graphics stack", self.names())

    def test_commented_lines_are_not_pins(self):
        self.put("env.d", "10-gpu.conf", "# GBM_BACKEND=nvidia-drm")
        self.assertNotIn("the environment pins the graphics stack", self.names())

    def test_modeset_on_the_cmdline_is_flagged(self):
        self.cmdline("quiet nvidia-drm.modeset=1 rw")
        self.assertIn("nvidia-drm modeset is forced on", self.names())

    def test_modeset_underscore_spelling_is_flagged(self):
        self.cmdline("quiet nvidia_drm.modeset=1 rw")
        self.assertIn("nvidia-drm modeset is forced on", self.names())

    def test_modeset_in_modprobe_d_is_flagged(self):
        self.put("modprobe.d", "nvidia.conf", "options nvidia-drm modeset=1")
        self.assertIn("nvidia-drm modeset is forced on", self.names())

    def test_every_risk_carries_a_remedy(self):
        """A warning a user cannot act on is just an obstacle."""
        self.topology(False, True)
        self.put("xorg.conf.d", "10-envy.conf", 'BusID "PCI:1:0:0"')
        risks = gm.preflight("igpu")
        self.assertTrue(risks)
        for r in risks:
            self.assertTrue(r["name"] and r["detail"] and r["remedy"])

    def test_an_unreadable_file_is_empty_not_an_exception(self):
        """preflight reads other people's config files; a permission error
        there must not be able to block a firmware write."""
        self.assertEqual(gm._reads("/nonexistent/path/xyz"), "")

    def test_a_directory_of_junk_does_not_raise(self):
        self.put("xorg.conf.d", "binary.conf", "\x00\xff not really text")
        self.put("env.d", "junk.conf", "= = =\n\n#\n")
        gm.preflight("igpu")            # must simply return


class GateTest(Sandbox):
    """set_mode must refuse a risky change until the risks have been seen."""

    def setUp(self):
        super().setUp()
        self.efi = tempfile.TemporaryDirectory()
        self.addCleanup(self.efi.cleanup)
        self.patch("EFIVARS", self.efi.name)
        p = mock.patch.object(gm.subprocess, "run", return_value=None)
        p.start()
        self.addCleanup(p.stop)
        for name, off, ln in (
                ("UniWillVariable-9f33f85c-13ca-4fd1-9c4a-96217722c593", 0x62, 180),
                ("TpvSetup-1c3483d5-1e7e-4450-9806-dede002c974b", 0x01, 11)):
            data = bytearray(ln)
            data[off] = 0x04                                   # dynamic
            with open(os.path.join(self.efi.name, name), "wb") as fh:
                fh.write((7).to_bytes(4, "little") + bytes(data))

    def test_risky_change_is_refused_without_acknowledgement(self):
        self.topology(False, True)
        with self.assertRaises(gm.GpuModeError) as cm:
            gm.set_mode("igpu", confirm=True)
        self.assertIn("acknowledge_risks", str(cm.exception))

    def test_the_refusal_names_the_risk_and_the_way_back(self):
        self.topology(False, True)
        with self.assertRaises(gm.GpuModeError) as cm:
            gm.set_mode("igpu", confirm=True)
        msg = str(cm.exception)
        self.assertIn("not on the bus", msg)
        self.assertIn("BIOS", msg)

    def test_refusal_writes_nothing(self):
        self.topology(False, True)
        with mock.patch.object(gm, "_write") as w:
            with self.assertRaises(gm.GpuModeError):
                gm.set_mode("igpu", confirm=True)
        w.assert_not_called()

    def test_acknowledged_change_goes_through(self):
        self.topology(False, True)
        out = gm.set_mode("igpu", confirm=True, acknowledge_risks=True)
        self.assertTrue(out["ok"])
        self.assertTrue(out["changed"])
        self.assertIn("BIOS", out["recovery"])

    def test_a_clean_machine_needs_no_acknowledgement(self):
        """The gate must not become a rubber stamp people always tick."""
        out = gm.set_mode("igpu", confirm=True)
        self.assertTrue(out["ok"])

    def test_structural_refusals_come_before_the_risk_gate(self):
        """A firmware structure we cannot interpret is a more fundamental
        refusal than "this might not boot", and must be reported as itself --
        otherwise a risky mode masks a genuinely broken variable pair."""
        self.topology(False, True)              # risks present
        tp = os.path.join(self.efi.name,
                          "TpvSetup-1c3483d5-1e7e-4450-9806-dede002c974b")
        data = bytearray(11)
        data[0x01] = 0x02                       # disagrees with UniWill's 0x04
        with open(tp, "wb") as fh:
            fh.write((7).to_bytes(4, "little") + bytes(data))
        with self.assertRaises(gm.GpuModeError) as cm:
            gm.set_mode("igpu", confirm=True)
        self.assertIn("disagree", str(cm.exception))
        self.assertNotIn("acknowledge_risks", str(cm.exception))

    def test_a_no_op_change_is_not_gated(self):
        """Re-selecting the current mode writes nothing, so there is no risk
        to acknowledge."""
        self.topology(True, True)
        out = gm.set_mode("dynamic", confirm=True)
        self.assertFalse(out["changed"])

    def test_confirm_is_still_required_first(self):
        self.topology(False, True)
        with self.assertRaises(gm.GpuModeError) as cm:
            gm.set_mode("igpu", acknowledge_risks=True)
        self.assertIn("confirm=True", str(cm.exception))


class StatusTest(Sandbox):

    def setUp(self):
        super().setUp()
        self.efi = tempfile.TemporaryDirectory()
        self.addCleanup(self.efi.cleanup)
        self.patch("EFIVARS", self.efi.name)
        for name, off, ln in (
                ("UniWillVariable-9f33f85c-13ca-4fd1-9c4a-96217722c593", 0x62, 180),
                ("TpvSetup-1c3483d5-1e7e-4450-9806-dede002c974b", 0x01, 11)):
            data = bytearray(ln)
            data[off] = 0x04
            with open(os.path.join(self.efi.name, name), "wb") as fh:
                fh.write((7).to_bytes(4, "little") + bytes(data))

    def test_status_carries_risks_per_mode_and_the_recovery_text(self):
        self.topology(False, True)
        st = gm.status()
        by = {m["id"]: m for m in st["modes"]}
        self.assertTrue(by["igpu"]["risks"])
        self.assertEqual(by["dynamic"]["risks"], [])
        self.assertIn("BIOS", st["recovery"])

    def test_status_survives_a_broken_preflight(self):
        """The reading must never be lost because a heuristic threw."""
        with mock.patch.object(gm, "preflight", side_effect=RuntimeError("boom")):
            st = gm.status()
        self.assertEqual(st["mode"], "dynamic")
        self.assertEqual(st["modes"][0]["risks"], [])


if __name__ == "__main__":
    unittest.main()
