#!/bin/bash

NODES=(
  leaf_1_1
  leaf_1_2
  leaf_2_1
  leaf_2_2
  spine_1_1
  spine_1_2
  spine_2_1
  spine_2_2
  core_1_1
  core_1_2
)

echo "=== BGP Router ID Uniqueness Check (All Nodes) ==="

declare -A ROUTER_IDS
FAIL=0

for NODE in "${NODES[@]}"; do
    RID=$(kathara exec "$NODE" \
        "vtysh -c 'show ip bgp'" 2>&1 \
        | grep "local router ID" \
        | sed -E 's/.*router ID is ([0-9.]+),.*/\1/')

    if [[ -z "$RID" ]]; then
        echo "  ❌ $NODE: router ID not found"
        FAIL=1
        continue
    fi

    if [[ -n "${ROUTER_IDS[$RID]}" ]]; then
        echo "  ❌ Duplicate router ID $RID on $NODE and ${ROUTER_IDS[$RID]}"
        FAIL=1
    else
        ROUTER_IDS[$RID]=$NODE
        echo "  ✅ $NODE router ID = $RID"
    fi
done

echo
if [[ $FAIL -eq 0 ]]; then
    echo "✔ All nodes have unique BGP router IDs"
else
    echo "✖ Router ID uniqueness check failed"
    exit 1
fi
