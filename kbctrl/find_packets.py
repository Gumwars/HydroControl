import sys

with open("Resources/117.ELUK", "rb") as f:
    data = f.read()

patterns = [b"\x14\x01", b"\x09\x02", b"\x1A\x00", b"\x16\x00", b"\x08\x02", b"\x08\x00"]
for p in patterns:
    idx = data.find(p)
    if idx != -1:
        print(f"Found {p.hex()} at 0x{idx:0X}")
        start = max(0, idx - 8)
        end = min(len(data), idx + 16)
        print(data[start:end].hex(" "))
    else:
        print(f"Not found: {p.hex()}")
