#!/bin/bash
echo "Testing Effect Array 08 02 [EffectID] 0A 64 00 00 01"

pause() {
    read -n 1 -s -r -p "Press any key to test next ID..."
    echo
}

# Testing effect indices 0 through 8
for id in 00 01 02 03 04 05 06 07 08; do
    echo "-> Testing Effect ID 0x$id"
    # Send the kickoff array
    python3 kbctrl.py raw --packet "08 02 $id 0A 64 00 00 01" --commit
    pause
    echo ""
done

# We will not reset it this time so we don't accidentally override anything if it DOES work.
echo "=== Test complete ==="
