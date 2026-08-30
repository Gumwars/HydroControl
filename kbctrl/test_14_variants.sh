#!/bin/bash
echo "Testing Effect Array 14 01 [EffectID] FF 00 00"
echo "We are sending RED to various effect IDs to see what happens."

pause() {
    read -n 1 -s -r -p "Press any key to test next ID..."
    echo
}

# 01 is static. Let's test 02 through 09.
for id in 02 03 04 05 06 07 08 09 0A 0B; do
    echo "-> Testing ID $id"
    python3 kbctrl.py raw --packet "14 01 $id FF 00 00 00 00" --commit
    pause
    echo ""
done

# Reset back to static green so the user knows the test finished cleanly
echo "-> Resetting to Static Green"
python3 kbctrl.py raw --packet "14 01 01 00 FF 00 00 00" --commit
echo "=== Test complete ==="
