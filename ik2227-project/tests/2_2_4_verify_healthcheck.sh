#!/bin/bash

DNS_NODE="dns1"
CLIENT="client_int"
TARGET_IP="10.30.40.2"

echo "=== DNS Failover Test ==="
echo

FAIL=0

echo "Checking bind9 status on $DNS_NODE..."
STATUS_BEFORE=$(kathara exec "$DNS_NODE" "systemctl is-active bind9" 2>&1)

if [[ "$STATUS_BEFORE" != "active" ]]; then
    echo "❌ bind9 is not running before test (status: $STATUS_BEFORE)"
    exit 1
fi
echo "✅ bind9 is running"
echo

echo "=== Traceroute BEFORE stopping bind9 ==="
TRACE_BEFORE=$(kathara exec "$CLIENT" "traceroute -n $TARGET_IP" 2>&1)
echo "$TRACE_BEFORE"
echo

if ! echo "$TRACE_BEFORE" | grep -q "$TARGET_IP"; then
    echo "❌ Target not reachable before shutdown"
    exit 1
fi

echo "Stopping bind9 on $DNS_NODE..."
kathara exec "$DNS_NODE" "systemctl stop bind9" >/dev/null 2>&1
sleep 2

STATUS_AFTER=$(kathara exec "$DNS_NODE" "systemctl is-active bind9" 2>&1)
if [[ "$STATUS_AFTER" != "inactive" ]]; then
    echo "❌ Failed to stop bind9 (status: $STATUS_AFTER)"
    exit 1
fi
echo "✅ bind9 successfully stopped"
echo

echo "=== Traceroute AFTER stopping bind9 ==="
TRACE_AFTER=$(kathara exec "$CLIENT" "traceroute -n $TARGET_IP" 2>&1)
echo "$TRACE_AFTER"
echo

if ! echo "$TRACE_AFTER" | grep -q "$TARGET_IP"; then
    echo "❌ Target not reachable after shutdown"
    FAIL=1
fi

echo
if [[ $FAIL -eq 0 ]]; then
    echo "✔ Failover verified: target reachable before and after bind9 shutdown"
else
    echo "✖ Failover test failed"
    exit 1
fi

echo
echo "Starting bind9 on $DNS_NODE..."
kathara exec "$DNS_NODE" "systemctl start bind9" >/dev/null 2>&1
sleep 2
echo "✅ bind9 restarted"