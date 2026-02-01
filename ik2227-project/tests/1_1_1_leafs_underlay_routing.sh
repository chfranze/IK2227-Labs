#!/bin/bash

declare -A LEAF_PINGS=(
  [leaf_1_1]="192.168.0.2 192.168.0.3 192.168.0.4"
  [leaf_1_2]="192.168.0.1 192.168.0.3 192.168.0.4"
  [leaf_2_1]="192.168.0.1 192.168.0.2 192.168.0.4"
  [leaf_2_2]="192.168.0.1 192.168.0.2 192.168.0.3"
)

for LEAF in "${!LEAF_PINGS[@]}"; do
    echo "=== Ping Test from $LEAF ==="
    for IP in ${LEAF_PINGS[$LEAF]}; do
        echo "Pinging $IP from $LEAF..."
        OUTPUT=$(kathara exec "$LEAF" "ping -c 2 -W 1 $IP" 2>&1)
        if echo "$OUTPUT" | grep -q "bytes from"; then
            echo "  ✅ Success: $IP reachable"
        else
            echo "  ❌ Failed: $IP unreachable"
        fi
    done
    echo
done
