# SPDX-License-Identifier: MIT
"""Instruction boundaries, and why they were worth building a tool for.

Four attempts to find what calls the charge-ceiling routine used byte scanning,
and byte scanning on a variable-length instruction set fails both ways.

The false negative that mattered: the ceiling code is entered by `70 08`, a
JNZ. Searching for LCALL/ACALL/LJMP/AJMP/SJMP misses conditional branches
entirely, so every search reported no references -- in the G1 image and in the
G2 where the feature reportedly works. An identical empty answer from a build
where the code demonstrably runs should have condemned the method on the spot.

The false positive that wasted a step: `80 11` is SJMP +17, but a scanner
reading every byte as a possible opcode sees 0x11 and reports an ACALL. That
produced a call chain that looped back on itself.

Both classes are tested here against the real bytes from 117.ELUK.
"""

import importlib.util
import os
import unittest

_SPEC = importlib.util.spec_from_file_location(
    "ec_disasm",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "ec_disasm.py"))


def load():
    m = importlib.util.module_from_spec(_SPEC)
    _SPEC.loader.exec_module(m)
    return m


class LengthTableTest(unittest.TestCase):
    """The table is the whole tool. A wrong entry desynchronises everything
    after it."""

    def setUp(self):
        self.m = load()

    def test_the_opcodes_this_investigation_tripped_over(self):
        for op, n, why in (
                (0x80, 2, "SJMP rel -- its operand was read as an ACALL"),
                (0x11, 2, "ACALL"),
                (0x70, 2, "JNZ rel -- how the ceiling code is entered"),
                (0x60, 2, "JZ rel"),
                (0x12, 3, "LCALL"),
                (0x02, 3, "LJMP"),
                (0x90, 3, "MOV DPTR,#imm16 -- the thunk's first instruction"),
                (0x22, 1, "RET"),
                (0xE0, 1, "MOVX A,@DPTR"),
                (0xF0, 1, "MOVX @DPTR,A"),
                (0x54, 2, "ANL A,#imm"),
                (0x44, 2, "ORL A,#imm"),
                (0x75, 3, "MOV dir,#imm"),
                (0xB4, 3, "CJNE A,#imm,rel"),
                (0xD5, 3, "DJNZ dir,rel")):
            self.assertEqual(self.m.LEN[op], n, f"{op:#04x}: {why}")

    def test_every_acall_and_ajmp_page_is_two_bytes(self):
        for page in range(8):
            self.assertEqual(self.m.LEN[(page << 5) | 0x11], 2)
            self.assertEqual(self.m.LEN[(page << 5) | 0x01], 2)

    def test_no_opcode_has_a_nonsense_length(self):
        for op in range(256):
            self.assertIn(self.m.LEN[op], (1, 2, 3), f"opcode {op:#04x}")


class BankingTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def test_the_window_maps_by_bank(self):
        """file = bank * 0x8000 + logical.

        This was 0x10000 and the error hid perfectly: it yields the same file
        offset with the bank number halved, so every disassembly came out
        right and every bank number came out wrong. Thunks are keyed on
        (bank, target), so lookups never matched -- the charge-ceiling task is
        bank 2 and was searched for in bank 1, which is why it read as
        unreachable in this image AND in the G2 where the feature works.
        """
        self.assertEqual(self.m.phys(2, 0xC88C), 0x1C88C)
        self.assertEqual(self.m.phys(2, 0x86C2), 0x186C2)
        self.assertEqual(self.m.phys(0, 0x8000), 0x8000)

    def test_the_old_wrong_mapping_would_still_look_plausible(self):
        """Pinned so nobody 'fixes' it back. bank1 at 0x10000 and bank2 at
        0x8000 land on the same byte; only the bank number differs, and the
        bank number is what thunk resolution depends on."""
        self.assertEqual(self.m.phys(2, 0xC88C), 1 * 0x10000 + 0xC88C)

    def test_common_memory_is_not_banked(self):
        for bank in range(4):
            self.assertEqual(self.m.phys(bank, 0x1100), 0x1100)

    def test_the_four_dispatchers_are_the_four_banks(self):
        self.assertEqual(self.m.DISPATCHER,
                         {0x1100: 0, 0x1114: 1, 0x1128: 2, 0x113C: 3})


def img_from(m, chunks):
    """A synthetic 4-bank image with bytes placed at file offsets."""
    buf = bytearray(b"\x00" * 0x40000)
    for off, data in chunks.items():
        buf[off:off + len(data)] = data
    return m.Image(bytes(buf))


class DecodeTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def test_a_conditional_branch_is_an_edge(self):
        """The false negative. `70 08` at 0xC8B0 reaches 0xC8BA."""
        i = img_from(self.m, {0x1C8B0: bytes([0x70, 0x08])})
        n, edges = i.decode(2, 0xC8B0)
        self.assertEqual(n, 2)
        self.assertIn(("branch", 2, 0xC8BA), edges)

    def test_a_backward_branch_resolves_signed(self):
        """0xFE is -2, so this SJMP targets itself. Relative offsets are the
        other half of the boundary problem: read as unsigned, every backward
        branch lands 256 bytes too far forward.

        SJMP is reported as "branch" rather than "jump". It is unconditional,
        but it is decoded with the relative-offset group and the call graph
        does not care which label a control transfer carries.
        """
        i = img_from(self.m, {0x1C000: bytes([0x80, 0xFE])})
        _, edges = i.decode(2, 0xC000)
        self.assertEqual(edges, [("branch", 2, 0xC000)])

    def test_a_sweep_does_not_invent_a_call_from_an_operand(self):
        """The false positive. `80 11` is SJMP +17; the 0x11 is data."""
        i = img_from(self.m, {0x1C8DC: bytes([0x80, 0x11])})
        calls, _ = i.sweep(2)
        for (_b, _t), srcs in calls.items():
            self.assertNotIn((2, 0xC8DD), srcs,
                             "decoded the SJMP operand as an ACALL")

    def test_a_real_acall_is_still_found(self):
        i = img_from(self.m, {0x1C800: bytes([0x11, 0xBA])})
        _, edges = i.decode(2, 0xC800)
        self.assertEqual(edges, [("call", 2, 0xC8BA)])

    def test_a_call_through_a_thunk_resolves_to_its_bank_and_target(self):
        """LCALL <thunk> is really call bank:target, and must not stop at the
        thunk -- that is what made the ceiling look unreachable cross-bank."""
        thunk = bytes([0x90, 0xC8, 0xBA, 0x02, 0x11, 0x28])   # -> bank 2
        i = img_from(self.m, {0x1500: thunk,
                              0x1C000: bytes([0x12, 0x15, 0x00])})
        self.assertEqual(i.thunks.get(0x1500), (2, 0xC8BA))
        _, edges = i.decode(2, 0xC000)
        self.assertEqual(edges, [("call", 2, 0xC8BA)])

    def test_thunks_are_only_taken_from_common_memory(self):
        """A matching byte pattern up in a bank is data, not a thunk."""
        i = img_from(self.m, {0x1C500: bytes([0x90, 0xC8, 0xBA, 0x02, 0x11, 0x14])})
        self.assertEqual(i.thunks, {})


class SweepTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def test_a_sweep_steps_by_instruction_length(self):
        """MOV DPTR is three bytes; its operands must not be decoded."""
        i = img_from(self.m, {0x18000: bytes([0x90, 0x12, 0x34, 0x22])})
        calls, _ = i.sweep(2)
        self.assertEqual(calls, {}, "decoded 0x12 0x34 inside MOV DPTR")

    def test_a_sweep_covers_the_whole_window(self):
        i = img_from(self.m, {0x1FFF0: bytes([0x12, 0xC8, 0xBA])})
        calls, _ = i.sweep(2)
        self.assertIn((2, 0xC8BA), calls)


if __name__ == "__main__":
    unittest.main()
