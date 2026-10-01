# SPDX-License-Identifier: MIT
"""IFR parsing, against HII built by hand.

The output of this tool is a list of byte offsets someone will write into
NVRAM from an EFI shell. A parser that is off by one does not fail loudly --
it reports a plausible offset for the wrong question, and in this firmware the
question four bytes along is IA CEP Enable. So the offset arithmetic is tested
against structures assembled here from the UEFI spec layouts, where the right
answer is known by construction.
"""

import importlib.util
import os
import struct
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _mod():
    path = os.path.join(_ROOT, "ifr_extract.py")
    spec = importlib.util.spec_from_file_location("ifr_extract", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def strings_package(texts):
    """EFI_HII_STRING_PACKAGE_HDR + SIBT_STRING_UCS2 blocks. Language[] sits
    at a fixed offset 46, which is how the scanner finds it."""
    body = b""
    for t in texts:
        body += b"\x14" + t.encode("utf-16-le") + b"\x00\x00"
    body += b"\x00"                                    # SIBT_END
    lang = b"en-US\x00"
    info_off = 46 + len(lang)
    hdr = (struct.pack("<II", 46, info_off)
           + b"\x00" * 32                              # LanguageWindow
           + struct.pack("<H", 0)                      # LanguageName
           + lang)
    length = 4 + len(hdr) + len(body)
    return struct.pack("<I", (0x04 << 24) | length) + hdr + body


def op(opcode, payload=b"", scope=0):
    length = 2 + len(payload)
    return bytes([opcode, (length & 0x7F) | (scope << 7)]) + payload


def varstore(vid, size, name):
    return op(0x24, b"\x00" * 16 + struct.pack("<HH", vid, size)
              + name.encode() + b"\x00")


def question(opcode, prompt, help_, qid, vsid, voff, width_code=1):
    return op(opcode, struct.pack("<HHHHH", prompt, help_, qid, vsid, voff)
              + bytes([0, width_code]) + b"\x00" * 6)


def oneof_option(sid, value):
    return op(0x09, struct.pack("<HBB", sid, 0, 0x00) + bytes([value]))


def forms_package(ops):
    body = op(0x0E, b"\x00" * 16 + struct.pack("<HH", 1, 2) + b"\x00") + ops
    length = 4 + len(body)
    return struct.pack("<I", (0x02 << 24) | length) + body


class StringTableTest(unittest.TestCase):

    def test_ids_start_at_one_and_advance(self):
        m = _mod()
        blob = strings_package(["Setup", "Core Voltage Offset", "Offset Prefix"])
        pkg = m.find_string_packages(blob)
        self.assertEqual(len(pkg), 1)
        s = m.parse_strings(blob, pkg[0])
        self.assertEqual(s[1], "Setup")
        self.assertEqual(s[2], "Core Voltage Offset")
        self.assertEqual(s[3], "Offset Prefix")


class QuestionOffsetTest(unittest.TestCase):
    """VarStoreInfo sits 10 bytes into the opcode, after prompt, help,
    question id and varstore id. One field's slip moves every offset."""

    def setUp(self):
        self.m = _mod()
        ops = (varstore(0x1234, 0x400, "CpuSetup")
               + question(0x07, 2, 0, 0x100, 0x1234, 0x01E0, width_code=1)
               + question(0x05, 3, 0, 0x101, 0x1234, 0x01E2, width_code=0)
               + oneof_option(4, 0) + oneof_option(5, 1))
        self.blob = (strings_package(
            ["Setup", "Core Voltage Offset", "Offset Prefix", "+", "-"])
            + forms_package(ops))
        spkgs = self.m.find_string_packages(self.blob)
        fpkgs = [p for p in self.m.find_form_packages(self.blob)]
        self.strings = self.m.parse_strings(self.blob, spkgs[0])
        self.vs, self.qs = None, None
        for fp in fpkgs:
            vs, qs = self.m.walk_forms(self.blob, fp)
            if qs:
                self.vs, self.qs = vs, qs
                break

    def test_the_varstore_name_is_read(self):
        self.assertEqual(self.vs.get(0x1234), "CpuSetup")

    def test_the_offsets_are_exact(self):
        got = {self.strings[q["prompt"]]: q["offset"] for q in self.qs}
        self.assertEqual(got["Core Voltage Offset"], 0x01E0)
        self.assertEqual(got["Offset Prefix"], 0x01E2)

    def test_the_width_comes_from_the_numeric_flags(self):
        byname = {self.strings[q["prompt"]]: q for q in self.qs}
        self.assertEqual(byname["Core Voltage Offset"]["width"], 2)   # code 1
        self.assertEqual(byname["Offset Prefix"]["width"], 1)         # code 0

    def test_oneof_options_attach_with_their_values(self):
        """0 means plus. Reporting this backwards turns a -40 mV request
        into +40 mV."""
        byname = {self.strings[q["prompt"]]: q for q in self.qs}
        opts = {v: self.strings[sid]
                for v, sid in byname["Offset Prefix"]["options"]}
        self.assertEqual(opts, {0: "+", 1: "-"})

    def test_options_do_not_leak_onto_a_numeric(self):
        byname = {self.strings[q["prompt"]]: q for q in self.qs}
        self.assertEqual(byname["Core Voltage Offset"]["options"], [])


class SuppressScopeTest(unittest.TestCase):
    """Whether a question is hidden is the whole claim about Prema's mod."""

    def setUp(self):
        self.m = _mod()

    def build(self, ops):
        blob = strings_package(["Setup", "Hidden One", "Plain One"]) + forms_package(ops)
        for fp in self.m.find_form_packages(blob):
            vs, qs = self.m.walk_forms(blob, fp)
            if qs:
                return qs
        return []

    def test_a_question_inside_suppress_if_is_marked(self):
        qs = self.build(
            varstore(1, 0x100, "CpuSetup")
            + op(0x0A, b"", scope=1)                       # SUPPRESS_IF
            + question(0x07, 2, 0, 1, 1, 0x50)
            + op(0x29)                                     # END
            + question(0x07, 3, 0, 2, 1, 0x60))
        by_off = {q["offset"]: q for q in qs}
        self.assertEqual(by_off[0x50]["hidden"], ["suppress-if"])
        self.assertEqual(by_off[0x60]["hidden"], [])

    def test_the_scope_closes_so_later_questions_are_not_tainted(self):
        """Failing to pop the scope would report the whole rest of the
        formset as hidden, which would make the headline claim unfalsifiable."""
        qs = self.build(
            varstore(1, 0x100, "CpuSetup")
            + op(0x19, b"", scope=1)                       # GRAY_OUT_IF
            + question(0x07, 2, 0, 1, 1, 0x50)
            + op(0x29)
            + question(0x07, 3, 0, 2, 1, 0x60)
            + question(0x07, 3, 0, 3, 1, 0x70))
        hidden = [q["offset"] for q in qs if q["hidden"]]
        self.assertEqual(hidden, [0x50])


if __name__ == "__main__":
    unittest.main()
