#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_capture.py — Parse usbmon capture file or live-capture from usbmon interface.

Usage:
  # Live capture (run before starting the Windows VM with USB passthrough):
  sudo python3 kb_capture.py --live

  # Parse a .pcap file captured with tcpdump/wireshark:
  sudo python3 kb_capture.py --pcap /tmp/kb.pcap

Filters for USB address 048D:600B (hidraw0) and prints all
SET_REPORT (feature) and Interrupt OUT packets in readable form.
"""

import sys, os, struct, argparse, time

TARGET_VID = 0x048D
TARGET_PID = 0x600B

def find_usbmon_iface():
    """Find the usbmon interface for the target device."""
    import subprocess
    try:
        out = subprocess.check_output(["lsusb", "-d", f"{TARGET_VID:04x}:{TARGET_PID:04x}"],
                                       text=True).strip()
        # e.g. "Bus 001 Device 003: ID 048d:600b ..."
        parts = out.split()
        bus = int(parts[1])
        print(f"[+] Found device on USB Bus {bus} → use usbmon{bus}")
        return f"usbmon{bus}", bus
    except Exception as e:
        print(f"[!] lsusb failed: {e}")
        print("    Try: lsusb -d 048d:600b")
        return None, None

def live_capture(iface):
    """Read from /dev/usbmonX and print HID SET_REPORT packets."""
    dev = f"/dev/{iface}"
    print(f"[*] Opening {dev} for live capture...")
    print("    Now start Windows VM with USB passthrough and launch Control Center.")
    print("    Press Ctrl+C to stop.\n")

    try:
        fd = os.open(dev, os.O_RDONLY)
    except OSError as e:
        print(f"[!] Cannot open {dev}: {e}")
        print("    Make sure usbmon is loaded: sudo modprobe usbmon")
        print("    And that you have read permission: sudo chmod a+r /dev/usbmon*")
        sys.exit(1)

    count = 0
    while True:
        try:
            # usbmon binary records are variable length; read 48-byte header first
            hdr = os.read(fd, 48)
            if len(hdr) < 48:
                continue

            # Parse usbmon binary header (struct usbmon_packet)
            # id(8) type(1) xfer_type(1) epnum(1) devnum(1) busnum(2) flag_setup(1) flag_data(1)
            # ts_sec(8) ts_usec(4) status(4) length(4) len_cap(4) ... setup(8)
            id_val, pkt_type, xfer_type, epnum, devnum, busnum = struct.unpack_from('<QBBBBH', hdr, 0)
            flag_data = hdr[15]
            ts_sec, ts_usec, status, length, len_cap = struct.unpack_from('<QIIIII', hdr, 16)

            # Read the data payload
            if len_cap > 0:
                data = os.read(fd, len_cap)
            else:
                data = b''

            # Filter: only our device's Interrupt OUT (epnum 0x01 OUT) and Control OUT (ep 0x00)
            # pkt_type: 'S'=submit, 'C'=complete, 'E'=error
            # xfer_type: 0=isochronous, 1=interrupt, 2=control, 3=bulk
            if pkt_type == ord('S') and xfer_type in (1, 2) and (epnum & 0x80) == 0:
                hex_data = ' '.join(f'{b:02X}' for b in data)
                ep = epnum & 0x0F
                t = {1: 'INT-OUT', 2: 'CTRL'}.get(xfer_type, '?')
                print(f"[{t}] ep={ep} dev={devnum} len={length}: {hex_data}")
                count += 1

        except KeyboardInterrupt:
            break
        except OSError:
            break

    os.close(fd)
    print(f"\n[+] Captured {count} packets.")

def parse_pcap(path):
    """Parse a pcap file and print HID-relevant packets."""
    try:
        import dpkt
        with open(path, 'rb') as f:
            pcap = dpkt.pcap.Reader(f)
            count = 0
            for ts, buf in pcap:
                # USB packets in pcap: dpkt.usb.USB
                try:
                    usb = dpkt.usb.USB(buf)
                    if usb.transfer_type in (1, 2) and (usb.endpoint & 0x80) == 0:
                        hex_data = ' '.join(f'{b:02X}' for b in bytes(usb.data))
                        t = {1: 'INT-OUT', 2: 'CTRL'}.get(usb.transfer_type, '?')
                        print(f"[{t}] ep={usb.endpoint} len={usb.urb_len}: {hex_data}")
                        count += 1
                except Exception:
                    pass
        print(f"\n[+] Found {count} relevant packets.")
    except ImportError:
        print("[!] dpkt not installed. Install with: pip install dpkt")
        print("    Or use Wireshark to open the pcap and filter manually.")

def print_setup_instructions():
    iface, bus = find_usbmon_iface()
    print()
    print("=" * 60)
    print("  USB Capture Setup Instructions")
    print("=" * 60)
    print()
    print("Step 1 — Load usbmon kernel module:")
    print("  sudo modprobe usbmon")
    print()
    if bus:
        print(f"Step 2 — Start capture (in a separate terminal):")
        print(f"  sudo tcpdump -i usbmon{bus} -w /tmp/kb_capture.pcap")
        print(f"  OR: sudo wireshark -i usbmon{bus}   (GUI)")
        print()
        print(f"Step 3 — In your VM software, add USB passthrough for:")
        print(f"  VendorID=048D  ProductID=600B")
        print(f"  (VirtualBox: Devices > USB > ITE Device)")
        print(f"  (VMware: VM > Removable Devices > ITE Device)")
        print()
        print(f"Step 4 — In Windows VM, launch Eluktronics Control Center.")
        print(f"  Wait for the keyboard to light up.")
        print()
        print(f"Step 5 — Stop the capture, then run:")
        print(f"  sudo python3 kb_capture.py --pcap /tmp/kb_capture.pcap")
        print()
        print(f"  OR run this live (before Step 3):")
        print(f"  sudo python3 kb_capture.py --live")
    print()

def main():
    p = argparse.ArgumentParser(description="USB HID capture for keyboard RE")
    p.add_argument("--live", action="store_true", help="Live capture from usbmon")
    p.add_argument("--pcap", help="Parse existing .pcap file")
    p.add_argument("--setup", action="store_true", help="Print setup instructions")
    args = p.parse_args()

    if args.pcap:
        parse_pcap(args.pcap)
    elif args.live:
        iface, bus = find_usbmon_iface()
        if iface:
            live_capture(iface)
        else:
            print("[!] Could not find device. Is it connected?")
    else:
        print_setup_instructions()

if __name__ == "__main__":
    main()
