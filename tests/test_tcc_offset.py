# SPDX-License-Identifier: MIT
"""The CPU temperature limit -- the TCC offset.

Control Center's SetCpuTccOffset(offset, enable) writes EC 0x0786 =
0x80|offset, or 0 when disabled, and nothing else (TCC-OFFSET-TESTS.md). The
offset the CPU actually runs is read back through the kernel's "TCC Offset"
cooling device. These pin the byte, the read-back, and how the profile keys
apply and drift.
"""

import fcntl
import os
import tempfile
import threading
import unittest
from unittest import mock

from hydroc import ec as ec_mod
from hydroc import tcc
from hydroc.ec import EC, REG_TCC_OFFSET
from hydroc.hardware import Hardware


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


class KernelReadTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.thermal = os.path.join(self.tmp.name, "thermal")
        self.hwmon = os.path.join(self.tmp.name, "hwmon")

    def test_finds_the_tcc_device_among_others(self):
        _write(os.path.join(self.thermal, "cooling_device0", "type"), "Processor\n")
        _write(os.path.join(self.thermal, "cooling_device38", "type"), "TCC Offset\n")
        _write(os.path.join(self.thermal, "cooling_device38", "cur_state"), "5\n")
        _write(os.path.join(self.thermal, "cooling_device38", "max_state"), "63\n")
        self.assertEqual(tcc.applied(self.thermal), 5)
        self.assertEqual(tcc.maximum(self.thermal), 63)

    def test_no_device_is_none_not_an_error(self):
        os.makedirs(self.thermal)
        self.assertIsNone(tcc.device(self.thermal))
        self.assertIsNone(tcc.applied(self.thermal))

    def test_tjmax_from_coretemp(self):
        _write(os.path.join(self.hwmon, "hwmon1", "name"), "acpitz\n")
        _write(os.path.join(self.hwmon, "hwmon5", "name"), "coretemp\n")
        _write(os.path.join(self.hwmon, "hwmon5", "temp1_crit"), "100000\n")
        self.assertEqual(tcc.tjmax(self.hwmon), 100)


class EcByteTest(unittest.TestCase):
    """Byte for byte what Control Center writes."""

    def ec(self, raw=0):
        e = EC.__new__(EC)
        e.read = mock.Mock(return_value=raw)
        e.write_verify = mock.Mock()
        return e

    def test_enabled_is_0x80_or_offset(self):
        e = self.ec()
        e.set_tcc_offset(True, 10)
        e.write_verify.assert_called_once_with(REG_TCC_OFFSET, 0x8A)

    def test_disabled_writes_zero_whatever_the_offset(self):
        e = self.ec()
        e.set_tcc_offset(False, 20)
        e.write_verify.assert_called_once_with(REG_TCC_OFFSET, 0)

    def test_out_of_range_is_refused_before_any_write(self):
        e = self.ec()
        with self.assertRaises(ValueError):
            e.set_tcc_offset(True, 64)
        e.write_verify.assert_not_called()

    def test_read_decodes_enable_and_offset(self):
        self.assertEqual(self.ec(0x8A).get_tcc_offset(),
                         {"enabled": True, "offset": 10, "raw": 0x8A})

    def test_a_disabled_byte_reports_no_offset(self):
        # The low bits mean nothing without bit 7; reporting them would be a
        # phantom offset in the UI and phantom drift in the banner.
        self.assertEqual(self.ec(0x0A).get_tcc_offset()["offset"], 0)


class _EC:
    def __init__(self):
        self.set_tcc_offset = mock.Mock()

    def has_double_pl4(self):
        return True

    def quantize_power_limit(self, which, watts):
        return int(watts)


class _Stub:
    def __init__(self, actual):
        self._actual = actual
        self.ec = _EC()

    def read_state(self):
        return dict(self._actual)

    def driver_bound(self):
        return True

    def normalize(self, desired):
        return Hardware.normalize(self, desired)


class ApplyTest(unittest.TestCase):

    ACTUAL = {"cpu_tcc_enable": False, "cpu_tcc_offset": 0, "cpu_tcc_applied": 5}

    def apply(self, desired, actual=None):
        stub = _Stub(actual or self.ACTUAL)
        changes = Hardware.apply(stub, desired)
        return stub.ec.set_tcc_offset, [c for c in changes if c.setting == "cpu_tcc_offset"]

    def test_enabling_writes_once_for_both_keys(self):
        w, ch = self.apply({"cpu_tcc_enable": True, "cpu_tcc_offset": 10})
        w.assert_called_once_with(True, 10)
        self.assertEqual((ch[0].was, ch[0].now), ("firmware", "-10 °C"))

    def test_unset_keys_write_nothing(self):
        w, ch = self.apply({"cpu_tcc_enable": None, "cpu_tcc_offset": None})
        w.assert_not_called()
        self.assertEqual(ch, [])

    def test_disabling_hands_back_to_firmware(self):
        w, _ = self.apply({"cpu_tcc_enable": False, "cpu_tcc_offset": 10},
                          actual={"cpu_tcc_enable": True, "cpu_tcc_offset": 10})
        w.assert_called_once_with(False, 0)

    def test_matching_hardware_is_not_rewritten(self):
        w, _ = self.apply({"cpu_tcc_enable": True, "cpu_tcc_offset": 10},
                          actual={"cpu_tcc_enable": True, "cpu_tcc_offset": 10})
        w.assert_not_called()

    def test_an_offset_alone_keeps_the_enable_the_ec_holds(self):
        w, _ = self.apply({"cpu_tcc_offset": 12},
                          actual={"cpu_tcc_enable": True, "cpu_tcc_offset": 10})
        w.assert_called_once_with(True, 12)

    # A boot puts the mode default back in the CPU (T4), and the EC only acts
    # on a change to 0x0786 (T2/T3). So a matching EC byte is no proof.

    def test_a_cpu_that_lost_the_limit_gets_it_back_with_an_edge(self):
        w, ch = self.apply({"cpu_tcc_enable": True, "cpu_tcc_offset": 10},
                           actual={"cpu_tcc_enable": True, "cpu_tcc_offset": 10,
                                   "cpu_tcc_applied": 5})
        self.assertEqual(w.call_args_list, [mock.call(False), mock.call(True, 10)])
        self.assertIn("CPU at -5", ch[0].was)

    def test_a_cpu_that_agrees_is_left_alone(self):
        w, _ = self.apply({"cpu_tcc_enable": True, "cpu_tcc_offset": 10},
                          actual={"cpu_tcc_enable": True, "cpu_tcc_offset": 10,
                                  "cpu_tcc_applied": 10})
        w.assert_not_called()

    def test_the_firmware_default_is_not_enforced(self):
        # Disabled, the CPU runs whatever the EC's mode default is; that is
        # the EC's to decide, not ours to correct.
        w, _ = self.apply({"cpu_tcc_enable": False},
                          actual={"cpu_tcc_enable": False, "cpu_tcc_offset": 0,
                                  "cpu_tcc_applied": 10})
        w.assert_not_called()

    def test_an_unreadable_cpu_is_not_a_reason_to_write(self):
        w, _ = self.apply({"cpu_tcc_enable": True, "cpu_tcc_offset": 10},
                          actual={"cpu_tcc_enable": True, "cpu_tcc_offset": 10,
                                  "cpu_tcc_applied": None})
        w.assert_not_called()

    def test_not_owned_by_a_native_mode(self):
        # Control Center writes it in every mode.
        w, _ = self.apply({"native_mode": None, "cpu_tcc_enable": True,
                           "cpu_tcc_offset": 10})
        w.assert_called_once()


class DriftTest(unittest.TestCase):

    def drift(self, desired, actual):
        stub = _Stub(actual)
        return Hardware.drift(stub, desired, actual)

    def test_a_remembered_offset_under_a_disabled_limit_is_not_drift(self):
        self.assertEqual(self.drift({"cpu_tcc_enable": False, "cpu_tcc_offset": 10},
                                    {"cpu_tcc_enable": False, "cpu_tcc_offset": 0}), {})

    def test_an_ec_that_dropped_the_setting_is_drift(self):
        d = self.drift({"cpu_tcc_enable": True, "cpu_tcc_offset": 10},
                       {"cpu_tcc_enable": False, "cpu_tcc_offset": 0})
        self.assertIn("cpu_tcc_enable", d)

    def test_what_the_cpu_runs_is_never_drift(self):
        # cpu_tcc_applied is the EC's to set; it is evidence, not a setting.
        self.assertEqual(self.drift({"cpu_tcc_enable": True, "cpu_tcc_offset": 10},
                                    {"cpu_tcc_enable": True, "cpu_tcc_offset": 10,
                                     "cpu_tcc_applied": 5}), {})


class CardTest(unittest.TestCase):
    """The card speaks in degrees; the profile and EC keep the offset."""

    def setUp(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "hydroc", "ui", "index.html"), encoding="utf8") as fh:
            self.src = fh.read()

    def _bind(self):
        i = self.src.index("if(tccr){")
        return self.src[i:self.src.index("$('#tccset').onclick=set;", i)]

    def test_the_limit_is_typed_as_a_temperature_with_no_spinner(self):
        # A number input's arrows fought the user mid-entry; text has none.
        self.assertIn('type="text" class="tempin" id="tcc" data-keep', self.src)
        self.assertNotIn('type="number" class="tempin"', self.src)
        self.assertNotIn('type="range" id="tcc"', self.src)

    def test_only_set_or_enter_writes(self):
        b = self._bind()
        self.assertIn('id="tccset"', self.src)
        self.assertIn("e.key==='Enter'", b)
        for auto in ("tccr.onblur", "tccr.onchange", "setTimeout"):
            self.assertNotIn(auto, b, f"{auto} would write without Set")
        # Typing only records the draft.
        oninput = b[b.index("tccr.oninput"):b.index("const set=")]
        self.assertNotIn("applySettings", oninput)

    def test_the_temperature_is_converted_to_the_offset(self):
        # 95 on a 100 degree part is an offset of 5.
        self.assertIn("cpu_tcc_offset:tj-t", self._bind())

    def test_only_whole_degrees_inside_the_range_are_sent(self):
        self.assertIn(r"!/^\d+$/.test(raw)||t<lo||t>tj", self._bind())

    def test_a_refresh_keeps_what_is_being_typed(self):
        # The draft lives in S, and render() restores focus to data-keep.
        self.assertIn("value=\"${esc(draft??cur)}\"", self.src)
        self.assertIn("ae.hasAttribute('data-keep')", self.src)


class CrossProcessLockTest(unittest.TestCase):
    """The daemon and a probe share /proc/acpi/call; neither may read the
    other's answer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lock = os.path.join(self.tmp.name, "run", "ec.lock")
        self.call = os.path.join(self.tmp.name, "call")
        open(self.call, "w").close()
        p = mock.patch.object(ec_mod, "LOCK_PATH", self.lock)
        p.start()
        self.addCleanup(p.stop)
        old = EC._lockfd
        EC._lockfd = None
        self.addCleanup(self._restore, old)

    def _restore(self, old):
        if EC._lockfd not in (None, -1):
            os.close(EC._lockfd)
        EC._lockfd = old

    def test_a_call_waits_for_another_holder(self):
        EC._process_lock()                       # create the lock file
        other = os.open(self.lock, os.O_RDWR)    # another process's handle
        self.addCleanup(os.close, other)
        fcntl.flock(other, fcntl.LOCK_EX)
        done = threading.Event()

        def call():
            EC(self.call)._call("\\_SB.INOU.ECRR 0x786")
            done.set()

        t = threading.Thread(target=call, daemon=True)
        t.start()
        self.assertFalse(done.wait(0.2), "the call did not wait for the lock")
        fcntl.flock(other, fcntl.LOCK_UN)
        self.assertTrue(done.wait(2), "the call never got the lock")

    def test_without_a_lock_file_it_still_works(self):
        with mock.patch.object(ec_mod, "LOCK_PATH", "/proc/no-such/ec.lock"):
            EC._lockfd = None
            self.assertIsNone(EC._process_lock())
            EC(self.call)._call("\\_SB.INOU.ECRR 0x786")


if __name__ == "__main__":
    unittest.main()
