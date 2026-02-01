#!/bin/bash

CLIENT="client_int"

declare -A EXPECTED_DNS=(
  [clustera.com]="10.0.200.1"
  [clusterb.com]="10.0.100.1"
)

echo "=== DNS Resolution Test (Exact Match) from $CLIENT ==="

FAIL=0

for HOST in "${!EXPECTED_DNS[@]}"; do
    EXPECTED_IP=${EXPECTED_DNS[$HOST]}
    echo "Resolving $HOST..."

    OUTPUT=$(kathara exec "$CLIENT" "dig +short $HOST" 2>&1)

    if [[ "$OUTPUT" == "$EXPECTED_IP" ]]; then
        echo "  ✅ $HOST resolves to $EXPECTED_IP"
    else
        echo "  ❌ $HOST resolution mismatch"
        echo "     Expected: $EXPECTED_IP"
        echo "     Got: ${OUTPUT:-<no result>}"
        FAIL=1
    fi
done

echo
if [[ $FAIL -eq 0 ]]; then
    echo "✔ DNS records match expected values"
else
    echo "✖ DNS resolution test failed"
    exit 1
fi
