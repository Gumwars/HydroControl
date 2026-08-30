#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, fcntl, array, sys, time

HIDIOCSFEATURE = 0xC0094806

def open_ec():
    for i in range(10):
        try:
            fd = os.open(f"/dev/hidraw{i}", os.O_RDWR)
            with open(f"/sys/class/hidraw/hidraw{i}/device/uevent", "r") as f:
                uevent_data = f.read().upper()
                if "0000048D:00006006" in uevent_data or "0000093A:00000255" in uevent_data:
                    print(f"[+] Found EC at /dev/hidraw{i}")
                    return fd
            os.close(fd)
        except Exception:
            pass
    print("[-] EC hidraw not found.")
    sys.exit(1)

def send(fd, pkt, label):
    padded = [0x00] + pkt + [0x00] * (65 - 1 - len(pkt))
    buf = array.array("B", padded)
    print(f"[*] {label:20} : {' '.join(f'{x:02X}' for x in pkt)}")
    fcntl.ioctl(fd, HIDIOCSFEATURE, buf, True)

def commit(fd):
    send(fd, [0x1A, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x01], "Commit")

def test_sequence(fd, name, seq):
    print(f"\n======================================")
    print(f"Executing: {name}")
    print(f"======================================")
    for label, pkt in seq:
        send(fd, pkt, label)
        time.sleep(0.1)
    
    print("\n>> Look at your keyboard. What did it do?")
    input("Press Enter to continue to the next test...\n")

def main():
    fd = open_ec()
    
    # Test 1: The hidpoke.py way
    seq1 = [
        ("Set Mode (0x16)", [0x16, 0x00, 0x06]), # Breathing
        ("Set Params (0x14)", [0x14, 0x01, 0x00, 0x00, 0x00, 0xFF, 0x00, 0x00]),
        ("Set Color (0x08)", [0x08, 0x00, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00]), # Red
        ("Commit (0x1A)", [0x1A, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x01])
    ]
    test_sequence(fd, "Test 1 -> Breathing RED (hidpoke method 0x08 color)", seq1)

    # Test 2: The kbctrl.py way
    seq2 = [
        ("Set Mode (0x16)", [0x16, 0x00, 0x06]), # Breathing
        ("Set Params (0x14)", [0x14, 0x01, 0x00, 0x0A, 0x01, 0xFF, 0x00, 0x00]),
        ("Set Color (0x14)", [0x14, 0x01, 0x01, 0x00, 0xFF, 0x00, 0x00, 0x00]), # Green
        ("Commit (0x1A)", [0x1A, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x01])
    ]
    test_sequence(fd, "Test 2 -> Breathing GREEN (kbctrl method 0x14 color)", seq2)

    # Test 3: Unmodified Effect 0x06 alone
    seq3 = [
        ("Set Mode (0x16)", [0x16, 0x00, 0x06]), # Breathing
        ("Commit (0x1A)", [0x1A, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x01])
    ]
    test_sequence(fd, "Test 3 -> Effect 0x06 Only", seq3)

    os.close(fd)
    print("Done testing.")

if __name__ == '__main__':
    main()
