#!/bin/bash

ROUTER="as100r1"
CLIENT="client_int"
TEST_IP="10.30.40.2"
HOSTS=("clustera.com" "clusterb.com")

echo "=== Testing backup DNS / routing when $ROUTER eth0 is down ==="

# Step 1: Bring down eth0 on the router
echo "Bringing down $ROUTER eth0..."
kathara exec "$ROUTER" "ip link set eth0 down"

# Wait 3 seconds for BGP converge
sleep 3

# Step 2: Verify connectivity via traceroute
echo
echo "--- Traceroute from $CLIENT to $TEST_IP ---"
TR_OUTPUT=$(kathara exec "$CLIENT" "traceroute -n -w 1 -q 1 $TEST_IP" 2>&1)

if echo "$TR_OUTPUT" | grep -q "$TEST_IP"; then
    echo "  ✅ Traceroute reached $TEST_IP via backup path"
    echo
    echo "Traceroute hops:"
    echo "$TR_OUTPUT" | sed 's/^/    /'
else
    echo "  ❌ Traceroute failed to reach $TEST_IP"
    echo "$TR_OUTPUT"
    exit 1
fi

# Step 3: Verify DNS resolution
echo
echo "--- DNS Resolution from $CLIENT ---"
DNS_FAIL=0

for HOST in "${HOSTS[@]}"; do
    OUTPUT=$(kathara exec "$CLIENT" "dig +short $HOST")
    if [[ -z "$OUTPUT" ]]; then
        echo "  ❌ $HOST could not be resolved via backup DNS"
        DNS_FAIL=1
    else
        echo "  ✅ $HOST resolves to $OUTPUT"
    fi
done

if [[ $DNS_FAIL -eq 1 ]]; then
    echo "✖ DNS resolution failed"
    exit 1
else
    echo "✔ DNS works via backup"
fi

# Step 4: Optional: Bring eth0 back up
echo
echo "Restoring $ROUTER eth0..."
kathara exec "$ROUTER" "ip link set eth0 up"
