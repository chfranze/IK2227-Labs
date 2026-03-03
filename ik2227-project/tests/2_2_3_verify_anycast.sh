#!/bin/bash

ROUTER="as100r1"
ROUTER2="as30r1"
CLIENT="client_int"
TEST_IP="10.30.40.2"
HOSTS=("clustera.com" "clusterb.com")

echo "=== Testing primary and backup DNS when $ROUTER eth0 is down ==="

# Step 1: Verify connectivity via traceroute
echo
echo "--- Traceroute from $CLIENT to $TEST_IP ---"
TR_OUTPUT=$(kathara exec "$CLIENT" "traceroute -n -w 1 -q 1 $TEST_IP" 2>&1)

if echo "$TR_OUTPUT" | grep -q "$TEST_IP"; then
    echo "  ✅ Traceroute reached $TEST_IP via primary path"
    echo
    echo "Traceroute hops:"
    echo "$TR_OUTPUT" | sed 's/^/    /'
else
    echo "  ❌ Traceroute failed to reach $TEST_IP"
    echo "$TR_OUTPUT"
    exit 1
fi

# Step 2: Verify DNS resolution
echo
echo "--- DNS Resolution from $CLIENT ---"
DNS_FAIL=0

for HOST in "${HOSTS[@]}"; do
    OUTPUT=$(kathara exec "$CLIENT" "dig +short $HOST")
    if [[ -z "$OUTPUT" ]]; then
        echo "  ❌ $HOST could not be resolved via primary DNS"
        DNS_FAIL=1
    else
        echo "  ✅ $HOST resolves to $OUTPUT"
    fi
done

if [[ $DNS_FAIL -eq 1 ]]; then
    echo "✖ DNS resolution failed"
    exit 1
else
    echo "✔ DNS works via primary"
fi

# Step 3: Bring down eth0 on the as100r1 and as30r1 router
# Note: To break the connection, you need to run ip link set <iface> down on both sides of the link!
echo
echo "Bringing down $ROUTER eth0..."
kathara exec "$ROUTER" "ip link set eth0 down"

echo
echo "Bringing down $ROUTER2 eth0..."
kathara exec "$ROUTER2" "ip link set eth0 down"

# Wait 5 seconds for BGP converge
sleep 5

# Step 4: Verify connectivity via traceroute
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

# Step 5: Verify DNS resolution
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

# Step 6: Optional: Bring eth0 back up
echo
echo "Restoring $ROUTER eth0..."
kathara exec "$ROUTER" "ip link set eth0 up"

echo
echo "Restoring $ROUTER2 eth0..."
kathara exec "$ROUTER2" "ip link set eth0 up"

# Wait 5 seconds for BGP converge
sleep 5