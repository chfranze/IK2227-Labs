#!/bin/bash

IP="10.1.1.1"

echo "=== Ping Test from client_basic ==="
echo "Pinging $IP from client_basic..."

# Run ping inside container
OUTPUT=$(kathara exec client_basic "ping -c 2 -W 1 $IP" 2>&1)

# Check for successful ping
if echo "$OUTPUT" | grep -q "bytes from"; then
    echo "  ✅ Success: $IP is reachable"
else
    echo "  ❌ Failed: $IP is unreachable"
    echo "Ping output:"
    echo "$OUTPUT"
fi
