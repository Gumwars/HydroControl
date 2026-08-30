import os, fcntl, array, sys, time

HIDIOCSFEATURE = 0xC0094806

def open_ec():
    for i in range(10):
        try:
            fd = os.open(f"/dev/hidraw{i}", os.O_RDWR)
            with open(f"/sys/class/hidraw/hidraw{i}/device/uevent", "r") as f:
                uevent_data = f.read().upper()
                if "0000048D:0000600B" in uevent_data or "048D:600B" in uevent_data or "0000048D:00006006" in uevent_data:
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

def main():
    fd = open_ec()
    print("Attempting to flush the EC chunked buffer state...")
    
    # Dump 16 empty segments in case it is waiting for 7 zones or per-key frames
    for i in range(16):
        send(fd, [0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00], f"Flush Frame {i}")
        time.sleep(0.05)
    
    send(fd, [0x1A, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x01], "Commit Flush")
    print("Done. Check if the TUI Mono Color works again.")
    os.close(fd)

if __name__ == '__main__':
    main()
