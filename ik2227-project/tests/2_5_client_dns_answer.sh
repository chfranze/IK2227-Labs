#!/bin/bash

CLIENT="client_int"
DEST="10.30.40.2"

echo "=== Traceroute Test from $CLIENT to $DEST ==="

OUTPUT=$(kathara exec "$CLIENT" "traceroute -n -w 1 -q 1 $DEST" 2>&1)

# Check if destination appears in the output
if echo "$OUTPUT" | grep -q "$DEST"; then
    echo "  ✅ Traceroute reached $DEST"
    echo "$OUTPUT"
else
    echo "  ❌ Traceroute failed to reach $DEST"
    echo "$OUTPUT"
    exit 1
fi

echo
echo "✔ Traceroute verification completed"
