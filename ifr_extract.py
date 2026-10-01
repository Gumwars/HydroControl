#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
ifr_extract.py -- read AMI setup questions out of a UEFI firmware image.

WHY

Laptop vendors ship Intel's full overclocking and undervolting menu tree and
then suppress the pages. Prema Mod's value on this chassis is exposing them;
it writes none of it. The settings themselves live in NVRAM varstores, and a
suppressed question still reads its value from the same offset -- so the
pages can be reached without modifying the BIOS at all, by writing the
varstore offset from an EFI shell (setup_var.efi, RU.efi).

That needs the exact varstore and offset per question, which is what this
prints. Guessing is how you set CEP Disable while aiming for a voltage
offset: in this image they are neighbours.

WHAT IT PARSES

HII, per UEFI spec 2.x:

  * strings packages (type 0x04)   -> string id -> text
  * forms packages   (type 0x02)   -> IFR opcode stream

Within the IFR stream it tracks varstore declarations, form titles, and the
suppress/grayout/disable scopes a question sits inside, so the output says
whether a question is hidden as shipped.

    python3 ifr_extract.py IMAGE
    python3 ifr_extract.py IMAGE --grep voltage
    python3 ifr_extract.py IMAGE --grep 'voltage|cep|overclock' --hidden-only

IMAGE must already be decompressed. A stock AMI capsule is LZMA inside FFS;
--lzma will pull the streams out of a raw ROM first.

Read-only. It opens a file and prints.
"""

from __future__ import annotations

import argparse
import re
import struct
import sys

# ── IFR opcodes (UEFI spec 2.10, table "IFR Opcodes") ───────────────────────
OP_FORM          = 0x01
OP_SUBTITLE      = 0x02
OP_ONE_OF        = 0x05
OP_CHECKBOX      = 0x06
OP_NUMERIC       = 0x07
OP_ONE_OF_OPTION = 0x09
OP_SUPPRESS_IF   = 0x0A
OP_FORM_SET      = 0x0E
OP_GRAY_OUT_IF   = 0x19
OP_STRING        = 0x1C
OP_DISABLE_IF    = 0x1E
OP_VARSTORE      = 0x24
OP_VARSTORE_EFI  = 0x26
OP_END           = 0x29

QUESTIONS = {OP_ONE_OF: "oneof", OP_CHECKBOX: "checkbox",
             OP_NUMERIC: "numeric", OP_STRING: "string"}
CONDITIONS = {OP_SUPPRESS_IF: "suppress-if", OP_GRAY_OUT_IF: "grayout-if",
              OP_DISABLE_IF: "disable-if"}

# Numeric/one-of flags, bits 0:1 -- the width of the varstore field.
WIDTH = {0: 1, 1: 2, 2: 4, 3: 8}


def find_string_packages(d: bytes) -> list[dict]:
    """Strings packages, located via the null-terminated language name.

    EFI_HII_STRING_PACKAGE_HDR puts Language[] at a fixed offset 46:
    header(4) + HdrSize(4) + StringInfoOffset(4) + LanguageWindow(32) +
    LanguageName(2).
    """
    out = []
    for m in re.finditer(rb'en-US\x00', d):
        start = m.start() - 46
        if start < 0:
            continue
        val = struct.unpack_from('<I', d, start)[0]
        length, ptype = val & 0xFFFFFF, val >> 24
        if ptype != 0x04 or not (64 <= length <= len(d) - start):
            continue
        hdr_size, info_off = struct.unpack_from('<II', d, start + 4)
        if not (46 <= info_off <= length):
            continue
        out.append({"start": start, "length": length, "info": info_off})
    return out


def parse_strings(d: bytes, pkg: dict) -> dict[int, str]:
    """String blocks -> {id: text}. IDs start at 1 and advance per block."""
    strings: dict[int, str] = {}
    i = pkg["start"] + pkg["info"]
    end = pkg["start"] + pkg["length"]
    sid = 1
    while i < end:
        block = d[i]
        if block == 0x00:                                   # SIBT_END
            break
        if block in (0x14, 0x15):                           # STRING_UCS2[_FONT]
            off = i + 1 + (1 if block == 0x15 else 0)
            j = off
            while j + 1 < end and d[j:j+2] != b'\x00\x00':
                j += 2
            strings[sid] = d[off:j].decode('utf-16-le', 'replace')
            sid += 1
            i = j + 2
        elif block in (0x16, 0x17):                         # STRINGS_UCS2[_FONT]
            count = struct.unpack_from('<I', d, i + 1)[0]
            off = i + 5 + (1 if block == 0x17 else 0)
            for _ in range(count):
                j = off
                while j + 1 < end and d[j:j+2] != b'\x00\x00':
                    j += 2
                strings[sid] = d[off:j].decode('utf-16-le', 'replace')
                sid += 1
                off = j + 2
            i = off
        elif block == 0x20:                                 # DUPLICATE
            strings[sid] = strings.get(struct.unpack_from('<H', d, i + 1)[0], "")
            sid += 1
            i += 3
        elif block == 0x21:                                 # SKIP2
            sid += struct.unpack_from('<H', d, i + 1)[0]
            i += 3
        elif block == 0x22:                                 # SKIP1
            sid += d[i + 1]
            i += 2
        elif block == 0x30:                                 # EXT1
            i += d[i + 2]
        elif block == 0x31:                                 # EXT2
            i += struct.unpack_from('<H', d, i + 2)[0]
        elif block == 0x32:                                 # EXT4
            i += struct.unpack_from('<I', d, i + 2)[0]
        else:
            break                                           # unknown: stop here
    return strings


def find_form_packages(d: bytes) -> list[dict]:
    """Forms packages: type 0x02, and the first opcode must be a FORM_SET."""
    out = []
    for m in re.finditer(rb'\x0e', d):
        start = m.start() - 4
        if start < 0:
            continue
        val = struct.unpack_from('<I', d, start)[0]
        length, ptype = val & 0xFFFFFF, val >> 24
        if ptype == 0x02 and 0x40 <= length <= len(d) - start:
            out.append({"start": start, "length": length})
    return out


def walk_forms(d: bytes, pkg: dict) -> tuple[dict, list[dict]]:
    """Returns ({varstore_id: name}, [question, ...])."""
    varstores: dict[int, str] = {}
    questions: list[dict] = []
    last_oneof: dict | None = None
    scopes: list[str | None] = []          # condition name, or None
    form = ""
    i = pkg["start"] + 4
    end = pkg["start"] + pkg["length"]

    while i + 2 <= end:
        op = d[i]
        raw = d[i + 1]
        length, scope = raw & 0x7F, raw >> 7
        if length < 2:
            break

        if op == OP_VARSTORE:
            vid, size = struct.unpack_from('<HH', d, i + 18)
            name = d[i + 22:i + length].split(b'\x00')[0].decode('ascii', 'replace')
            varstores[vid] = name
        elif op == OP_VARSTORE_EFI:
            vid = struct.unpack_from('<H', d, i + 2)[0]
            if length >= 28:
                name = d[i + 26:i + length].split(b'\x00')[0].decode('ascii', 'replace')
                varstores.setdefault(vid, name or f"EfiVar{vid:#x}")
            else:
                varstores.setdefault(vid, f"EfiVar{vid:#x}")
        elif op == OP_FORM:
            form = struct.unpack_from('<H', d, i + 4)[0]       # title string id
        elif op in QUESTIONS and length >= 13:
            prompt, help_, qid, vsid, voff = struct.unpack_from('<HHHHH', d, i + 2)
            if op == OP_CHECKBOX:
                width = 1
            elif op == OP_STRING:
                width = d[i + 14] * 2 if length > 14 else 0    # MaxSize, CHAR16
            else:
                width = WIDTH[d[i + 13] & 0x03] if length >= 14 else 0
            questions.append({
                "op": QUESTIONS[op], "prompt": prompt, "help": help_,
                "qid": qid, "varstore": vsid, "offset": voff, "width": width,
                "form": form,
                "hidden": [s for s in scopes if s],
                "options": [],
            })
            last_oneof = questions[-1] if op == OP_ONE_OF else None
        elif op == OP_ONE_OF_OPTION and last_oneof is not None and length >= 7:
            # Which value means what. Writing the sign byte of a voltage
            # offset the wrong way round turns -40 mV into +40 mV, so the
            # option values are not optional detail.
            sid, _flags, vtype = struct.unpack_from('<HBB', d, i + 2)
            fmt = {0x00: '<B', 0x01: '<H', 0x02: '<I', 0x03: '<Q', 0x04: '<B'}.get(vtype)
            val = struct.unpack_from(fmt, d, i + 6)[0] if fmt and i + 6 + struct.calcsize(fmt) <= end else None
            last_oneof["options"].append((val, sid))

        if scope:
            scopes.append(CONDITIONS.get(op))
        if op == OP_END and scopes:
            scopes.pop()
        i += length
    return varstores, questions


def lzma_streams(raw: bytes) -> bytes:
    """Pull LZMA1 'alone' streams out of a raw ROM. UEFI's
    LzmaCustomDecompress sections embed the 13-byte alone header, so scanning
    for it beats walking the FFS tree."""
    import lzma
    out, i = [], 0
    while True:
        i = raw.find(b'\x5d', i)
        if i < 0 or i + 13 > len(raw):
            break
        dict_sz, usize = struct.unpack_from('<IQ', raw, i + 1)
        if 0x1000 <= dict_sz <= 0x10000000 and 0x1000 <= usize <= 0x8000000:
            try:
                dec = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
                blob = dec.decompress(raw[i:i + 0x2000000], max_length=usize)
                if len(blob) >= usize * 0.9:
                    out.append(blob)
            except Exception:
                pass
        i += 1
    return b''.join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("--grep", help="regex over the question prompt (case-insensitive)")
    ap.add_argument("--hidden-only", action="store_true",
                    help="only questions inside a suppress/grayout/disable scope")
    ap.add_argument("--options", action="store_true",
                    help="list the values a oneof accepts, and what each means")
    ap.add_argument("--lzma", action="store_true",
                    help="decompress LZMA streams out of a raw ROM first")
    a = ap.parse_args(argv)

    d = open(a.image, 'rb').read()
    if a.lzma:
        d = lzma_streams(d)
        print(f"# decompressed to {len(d)} bytes", file=sys.stderr)

    spkgs = find_string_packages(d)
    fpkgs = find_form_packages(d)
    print(f"# {len(spkgs)} strings packages, {len(fpkgs)} forms packages",
          file=sys.stderr)

    pat = re.compile(a.grep, re.I) if a.grep else None
    rows = []
    for fp in fpkgs:
        varstores, questions = walk_forms(d, fp)
        if not questions:
            continue
        # Pair with the nearest strings package; within a package list they
        # are adjacent, and picking by distance is right far more often than
        # merging every package's ids into one namespace would be.
        near = min(spkgs, key=lambda s: abs(s["start"] - fp["start"]), default=None)
        strings = parse_strings(d, near) if near else {}
        for q in questions:
            name = strings.get(q["prompt"], "")
            if not name:
                continue
            if pat and not pat.search(name):
                continue
            if a.hidden_only and not q["hidden"]:
                continue
            rows.append({
                "name": name,
                "form": strings.get(q["form"], ""),
                "varstore": varstores.get(q["varstore"], f"id{q['varstore']:#x}"),
                "offset": q["offset"], "width": q["width"],
                "type": q["op"],
                "hidden": ",".join(sorted(set(q["hidden"]))),
                "help": strings.get(q["help"], ""),
                "options": [(v, strings.get(sid, "")) for v, sid in q["options"]],
            })

    seen, uniq = set(), []
    for r in rows:
        k = (r["varstore"], r["offset"], r["name"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    uniq.sort(key=lambda r: (r["varstore"], r["offset"]))

    if not uniq:
        print("no matching questions found", file=sys.stderr)
        return 1
    print(f"{'varstore':<14} {'offset':>8} {'w':>2} {'type':<9} "
          f"{'hidden':<12} name")
    print("-" * 100)
    for r in uniq:
        print(f"{r['varstore']:<14} 0x{r['offset']:04X}   {r['width']:>2} "
              f"{r['type']:<9} {r['hidden'] or '-':<12} {r['name']}")
        if a.options and r["options"]:
            for v, text in r["options"]:
                print(f"{'':<14} {'':>8} {'':>2} "
                      f"    = {v}  {text}")
    print(f"\n{len(uniq)} questions", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
