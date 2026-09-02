# SPDX-License-Identifier: MIT
"""Firmware GPU mode.

This is the only write in the project without the power-cycle backstop, so the
module is built to refuse rather than to try. These pin the refusals: unknown
modes, disagreeing variables, unexpected structure sizes, values the firmware
never produced, and -- most importantly -- writing at all without an explicit
confirm, so it cannot be reached by a stray settings dict.

No firmware is touched: the variable directory is redirected at a temp dir.
"""

import os
import tempfile
import unittest
from unittest import mock

from hydroc import gpumode as gm

UW = "UniWillVariable-9f33f85c-13ca-4fd1-9c4a-96217722c593"
TP = "TpvSetup-1c3483d5-1e7e-4450-9806-dede002c974b"


class Fake(unittest.TestCase):
    """A temp dir standing in for efivarfs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(gm, "EFIVARS", self.tmp.name)
        p.start()
        self.addCleanup(p.stop)
        # chattr does not exist for a temp file; the real call is best-effort.
        p2 = mock.patch.object(gm.subprocess, "run", return_value=None)
        p2.start()
        self.addCleanup(p2.stop)

    def write(self, byte_uw=0x04, byte_tp=None, uw_len=180, tp_len=11):
        byte_tp = byte_uw if byte_tp is None else byte_tp
        for name, off, val, ln in ((UW, 0x62, byte_uw, uw_len),
                                   (TP, 0x01, byte_tp, tp_len)):
            data = bytearray(ln)
            if off < ln:
                data[off] = val
            with open(os.path.join(self.tmp.name, name), "wb") as fh:
                fh.write((7).to_bytes(4, "little") + bytes(data))


class StatusTest(Fake):

    def test_reads_each_known_mode(self):
        for name, val in gm.MODES.items():
            self.write(val)
            with mock.patch.object(gm, "topology", return_value=gm.EXPECT[name]):
                st = gm.status()
            self.assertEqual(st["mode"], name)
            self.assertFalse(st["reboot_pending"])

    def test_disagreeing_variables_report_no_mode(self):
        self.write(0x04, 0x02)
        st = gm.status()
        self.assertIsNone(st["mode"])
        self.assertIn("disagree", st["error"])

    def test_unknown_value_is_not_interpreted(self):
        self.write(0x09)
        st = gm.status()
        self.assertIsNone(st["mode"])
        self.assertIn("0x09", st["error"])

    def test_written_but_not_rebooted_is_named_not_a_fault(self):
        self.write(gm.MODES["igpu"])
        with mock.patch.object(gm, "topology", return_value=(True, True)):
            st = gm.status()
        self.assertEqual(st["mode"], "igpu")
        self.assertTrue(st["reboot_pending"])

    def test_status_never_raises(self):
        # doctor and the UI call this; it must degrade, not explode.
        with mock.patch.object(gm, "EFIVARS", "/nonexistent"):
            self.assertFalse(gm.status()["supported"])
        self.write()
        with mock.patch.object(gm, "_current", side_effect=OSError("boom")):
            self.assertIn("error", gm.status())


class RefusalTest(Fake):

    def test_write_requires_explicit_confirm(self):
        self.write(0x04)
        with self.assertRaises(gm.GpuModeError) as cm:
            gm.set_mode("igpu")
        self.assertIn("confirm=True", str(cm.exception))

    def test_refusing_without_confirm_writes_nothing(self):
        self.write(0x04)
        before = open(os.path.join(self.tmp.name, UW), "rb").read()
        with self.assertRaises(gm.GpuModeError):
            gm.set_mode("igpu")
        self.assertEqual(open(os.path.join(self.tmp.name, UW), "rb").read(), before)

    def test_unknown_mode_is_refused(self):
        self.write(0x04)
        with self.assertRaises(gm.GpuModeError):
            gm.set_mode("turbo", confirm=True)

    def test_disagreeing_variables_block_a_write(self):
        self.write(0x04, 0x02)
        with self.assertRaises(gm.GpuModeError) as cm:
            gm.set_mode("igpu", confirm=True)
        self.assertIn("disagree", str(cm.exception))

    def test_unrecognised_current_value_blocks_a_write(self):
        # Never overwrite a structure we cannot interpret.
        self.write(0x09)
        with self.assertRaises(gm.GpuModeError):
            gm.set_mode("dynamic", confirm=True)

    def test_unexpected_variable_size_blocks_a_write(self):
        self.write(0x04, uw_len=64)
        with self.assertRaises(gm.GpuModeError) as cm:
            gm.set_mode("igpu", confirm=True)
        self.assertIn("does not recognise", str(cm.exception))


class WriteTest(Fake):

    def test_a_change_writes_both_variables(self):
        self.write(gm.MODES["dynamic"])
        r = gm.set_mode("dgpu", confirm=True)
        self.assertTrue(r["ok"])
        self.assertTrue(r["changed"])
        for name, off in ((UW, 0x62), (TP, 0x01)):
            raw = open(os.path.join(self.tmp.name, name), "rb").read()
            self.assertEqual(raw[4 + off], gm.MODES["dgpu"], f"{name} not written")

    def test_only_the_mode_byte_changes(self):
        self.write(gm.MODES["dynamic"])
        before = bytearray(open(os.path.join(self.tmp.name, UW), "rb").read())
        gm.set_mode("igpu", confirm=True)
        after = bytearray(open(os.path.join(self.tmp.name, UW), "rb").read())
        diff = [i for i in range(len(before)) if before[i] != after[i]]
        self.assertEqual(diff, [4 + 0x62], f"touched more than the mode byte: {diff}")

    def test_attributes_are_preserved(self):
        self.write(gm.MODES["dynamic"])
        gm.set_mode("dgpu", confirm=True)
        raw = open(os.path.join(self.tmp.name, UW), "rb").read()
        self.assertEqual(int.from_bytes(raw[:4], "little"), 7)

    def test_setting_the_current_mode_is_a_no_op(self):
        self.write(gm.MODES["dgpu"])
        r = gm.set_mode("dgpu", confirm=True)
        self.assertFalse(r["changed"])

    def test_only_firmware_produced_values_are_ever_written(self):
        self.assertEqual(sorted(gm.MODES.values()), [0x01, 0x02, 0x04])


if __name__ == "__main__":
    unittest.main()
