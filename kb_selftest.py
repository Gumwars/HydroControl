#!/usr/bin/env python3
"""
kb_selftest.py — End-to-end proof the ITE 8291 keyboard RGB works on this unit.

For every firmware effect, this sets it and then READS IT BACK via GET_EFFECT
(the firmware answers with the exact effect bytes it accepted). If readback
matches what we wrote, the control path is proven bidirectionally. You confirm
visually that the keyboard shows the right animation.

Effects (firmware IDs per ite8291r3 / tuxedo ite_8291):
    0x02 breathing  0x03 wave     0x04 random  0x05 rainbow
    0x06 ripple     0x09 marquee  0x0A raindrop 0x0E aurora
    0x11 fireworks  0x33 user mode (static color)
Plus: static color (red/green/blue) and a per-key stripe.

Run: sudo python3 kb_selftest.py
"""

import glob
import os
import sys
import time

for _p in glob.glob(
    "/home/gumwars/.local/share/pipx/venvs/ite8291r3-ctl/lib/python*/site-packages"
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ite8291r3_ctl import ite8291r3 as _ite  # noqa: E402

EFFECTS = ["breathing", "wave", "random", "rainbow", "ripple",
           "marquee", "raindrop", "aurora", "fireworks"]


def get_device():
    return _ite.get()


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("must run as root (sudo python3 kb_selftest.py)")

    dev = get_device()
    print("ITE 8291 keyboard connected.")
    fw = dev.get_fw_version()
    print(f"  firmware version: {fw[0]}.{fw[1]}.{fw[2]}.{fw[3]}")
    cur = dev.get_effect()
    print(f"  current effect bytes: {[hex(x) for x in cur]} "
          f"(effect=0x{cur[0]:02x}, speed={cur[1]}, bri={cur[2]}, color={cur[3]})")
    print("\nPer effect: answer  s=works (right animation)  o=off/dark  ?=wrong/other  q=quit")

    results = []
    for name in EFFECTS:
        eff_cls = _ite.effects[name](**{})
        dev.set_effect(eff_cls)
        time.sleep(0.4)
        rb = dev.get_effect()
        written = eff_cls[0]
        readback = rb[0]
        ok_write = (readback == written)
        print(f"  {name:11s} (0x{written:02x}) -> readback 0x{readback:02x} "
              f"{'[firmware accepted]' if ok_write else '[MISMATCH]'}")
        time.sleep(1.2)
        try:
            obs = input(f"    visible? [s/o/?]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            obs = "q"
        results.append({"effect": name, "id": written, "readback": readback,
                        "accepted": ok_write, "obs": obs})
        if obs == "q":
            break

    print("\nStatic color (user mode 0x33):")
    for label, color in (("RED", (255, 0, 0)), ("GREEN", (0, 255, 0)),
                         ("BLUE", (0, 0, 255))):
        dev.set_color(color)
        time.sleep(0.6)
        rb = dev.get_effect()
        accepted = rb[0] == 0x33
        print(f"  {label:5s} -> readback effect 0x{rb[0]:02x} "
              f"{'[user mode]' if accepted else '[MISMATCH]'}")
        time.sleep(1.2)
        try:
            obs = input(f"    all keys {label}? [s/o/?]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            obs = "q"
        results.append({"effect": f"static_{label.lower()}", "id": 0x33,
                        "readback": rb[0], "accepted": accepted, "obs": obs})
        if obs == "q":
            break

    print("\nPer-key stripe: row 2 (home row), columns 0-10 solid cyan:")
    stripe = {(2, c): (0, 255, 255) for c in range(0, 11)}
    dev.set_key_colors(stripe)
    time.sleep(1.5)
    try:
        obs = input("    cyan stripe on the home row only? [s/o/?]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        obs = "q"
    results.append({"effect": "perkey_stripe", "obs": obs})

    print("\nRestoring breathing effect + brightness 25...")
    dev.set_effect(_ite.effects["breathing"](brightness=25))
    dev.set_brightness(25)

    passed = all(r.get("accepted", True) for r in results if "accepted" in r)
    seen = sum(1 for r in results if r.get("obs") == "s")
    print("\n" + "=" * 56)
    print(f"Firmware accepted {sum(1 for r in results if r.get('accepted', True))}"
          f"/{sum(1 for r in results if 'accepted' in r)} writes (readback match).")
    print(f"You visually confirmed {seen}/{len(results)} as correct.")
    print("=" * 56)


if __name__ == "__main__":
    main()