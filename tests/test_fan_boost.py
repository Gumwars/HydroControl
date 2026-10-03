# SPDX-License-Identifier: MIT
"""Fan boost: bit 6 of 0x0751, on top of whatever mode is selected.

It was readable and not settable -- read_state() reported it and the UI
showed a tag, because apply_native() took a mode and nothing else. Wiring it
up has one shape worth getting right: boost must not cost a mode re-apply.

nativemode.apply() rewrites both fan tables, which is 96 register writes.
Spending that to move one bit would put back the delay that made the mode
button feel broken. BIT_FAN_BOOST is bit 6 and MODE_MASK is bits 7, 5 and 4,
so the two are disjoint and a read-modify-write of 0x0751 leaves the mode
alone.
"""

import unittest
from unittest import mock

from hydroc import nativemode as nm


class FakeEC:
    def __init__(self, raw=0x00, latched=False):
        self.mem = {nm.REG_FAN_MODE: raw}
        self.log = []
        self._latched = latched

    def read(self, addr):
        return self.mem.get(addr, 0)

    def write_verify(self, addr, value):
        self.log.append((addr, value))
        self.mem[addr] = value

    write = write_verify

    def custom_profile_enabled(self):
        return self._latched


class SetBoostTest(unittest.TestCase):

    def test_it_sets_bit_6(self):
        ec = FakeEC(nm.MODE_BEAST)
        nm.set_boost(ec, True)
        self.assertEqual(ec.mem[nm.REG_FAN_MODE],
                         nm.MODE_BEAST | nm.BIT_FAN_BOOST)

    def test_it_clears_bit_6(self):
        ec = FakeEC(nm.MODE_BEAST | nm.BIT_FAN_BOOST)
        nm.set_boost(ec, False)
        self.assertEqual(ec.mem[nm.REG_FAN_MODE], nm.MODE_BEAST)

    def test_the_mode_survives_both_ways(self):
        """The whole reason this is a read-modify-write. Office is 0xA0 --
        bit 7 is the one that stopped the fans with empty tables, so clearing
        it by accident is not a cosmetic bug."""
        for mode in (nm.MODE_OFFICE, nm.MODE_BALANCED, nm.MODE_BEAST):
            for on in (True, False):
                ec = FakeEC(mode)
                nm.set_boost(ec, on)
                self.assertEqual(ec.mem[nm.REG_FAN_MODE] & nm.MODE_MASK,
                                 mode & nm.MODE_MASK,
                                 f"mode 0x{mode:02X} disturbed by boost={on}")

    def test_a_no_op_writes_nothing(self):
        """Every write to this register is a mode write as far as the EC is
        concerned; not making one is better than making a redundant one."""
        ec = FakeEC(nm.MODE_BEAST | nm.BIT_FAN_BOOST)
        nm.set_boost(ec, True)
        self.assertEqual(ec.log, [])

    def test_it_does_not_touch_the_fan_tables(self):
        ec = FakeEC(nm.MODE_BEAST)
        nm.set_boost(ec, True)
        touched = [a for a, _ in ec.log if 0x0F00 <= a <= 0x0F5F]
        self.assertEqual(touched, [], "boost rewrote the fan tables")

    def test_current_reports_it_back(self):
        ec = FakeEC(nm.MODE_OFFICE)
        nm.set_boost(ec, True)
        self.assertTrue(nm.current(ec)["boost"])
        self.assertEqual(nm.current(ec)["mode"], "office")


class ApplyIntegrationTest(unittest.TestCase):
    """How hardware.apply() routes it."""

    @staticmethod
    def _hw():
        from hydroc import hardware
        return hardware

    def run_apply(self, desired, actual):
        hw_mod = self._hw()
        hw = hw_mod.Hardware.__new__(hw_mod.Hardware)
        hw.ec = mock.MagicMock()
        with mock.patch.object(hw_mod.Hardware, "read_state",
                               return_value=actual), \
             mock.patch.object(hw_mod.Hardware, "normalize",
                               side_effect=lambda d: dict(d)), \
             mock.patch.object(hw_mod.nativemode, "apply") as m_apply, \
             mock.patch.object(hw_mod.nativemode, "set_boost") as m_boost:
            changes = hw_mod.Hardware.apply(hw, desired)
        return changes, m_apply, m_boost

    def test_boost_alone_does_not_re_apply_the_mode(self):
        """The point of the whole exercise: 1 register write, not 96."""
        _, m_apply, m_boost = self.run_apply(
            {"native_mode": "beast", "fan_boost": True},
            {"native_mode": "beast", "fan_boost": False})
        m_apply.assert_not_called()
        m_boost.assert_called_once()
        self.assertIs(m_boost.call_args.args[1], True)

    def test_a_mode_change_carries_the_boost_with_it(self):
        """One reconcile, not two -- and no second write to 0x0751."""
        _, m_apply, m_boost = self.run_apply(
            {"native_mode": "beast", "fan_boost": True},
            {"native_mode": "office", "fan_boost": False})
        m_apply.assert_called_once()
        self.assertIs(m_apply.call_args.kwargs["boost"], True)
        m_boost.assert_not_called()

    def test_an_unchanged_boost_writes_nothing(self):
        _, m_apply, m_boost = self.run_apply(
            {"native_mode": "beast", "fan_boost": True},
            {"native_mode": "beast", "fan_boost": True})
        m_apply.assert_not_called()
        m_boost.assert_not_called()

    def test_omitting_boost_leaves_it_alone(self):
        """A request that says nothing about boost must not turn it off."""
        _, _, m_boost = self.run_apply(
            {"native_mode": "beast"},
            {"native_mode": "beast", "fan_boost": True})
        m_boost.assert_not_called()


class UIWiringTest(unittest.TestCase):

    @staticmethod
    def _ui():
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "hydroc", "ui", "index.html"),
                  encoding="utf-8") as fh:
            return fh.read()

    def test_the_toggle_exists_and_posts(self):
        ui = self._ui()
        self.assertIn('id="fanboost"', ui)
        self.assertIn("boost:!S.state.fan_boost", ui)

    def test_it_sends_the_active_mode_with_it(self):
        """/api/native takes a mode; sending boost without one would be a
        request the route cannot honour."""
        self.assertIn("mode:S.nativeActive", self._ui())

    def test_it_is_disabled_without_a_native_mode(self):
        ui = self._ui()
        i = ui.index('id="fanboost"')
        self.assertIn("disabled", ui[i:i + 200])

    def test_the_read_only_tag_is_gone(self):
        self.assertNotIn("fan boost on", self._ui())


if __name__ == "__main__":
    unittest.main()
