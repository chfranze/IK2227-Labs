#!/bin/bash

declare -A EXPECTED_PREFIXES=(
  [leaf_1_1]="192.168.0.2/32 192.168.0.3/32 192.168.0.4/32"
  [leaf_1_2]="192.168.0.1/32 192.168.0.3/32 192.168.0.4/32"
  [leaf_2_1]="192.168.0.1/32 192.168.0.2/32 192.168.0.4/32"
  [leaf_2_2]="192.168.0.1/32 192.168.0.2/32 192.168.0.3/32"
)

echo "=== BGP Multipath Verification (All Leafs) ==="

GLOBAL_FAIL=0

for LEAF in "${!EXPECTED_PREFIXES[@]}"; do
    echo
    echo "--- $LEAF ---"

    OUTPUT=$(kathara exec "$LEAF" "vtysh -c 'show ip bgp'" 2>&1)

    CURRENT=""
    declare -A PATHS
    declare -A ECMP

    while read -r LINE; do
        if echo "$LINE" | grep -q "/32"; then
            CURRENT=$(echo "$LINE" | awk '{print $2}')
            PATHS["$CURRENT"]=1
            [[ "$LINE" =~ "=" ]] && ECMP["$CURRENT"]=1
        elif [[ "$LINE" =~ ^[\ \*=\>]+ ]] && [[ -n "$CURRENT" ]]; then
            PATHS["$CURRENT"]=$((PATHS["$CURRENT"] + 1))
            [[ "$LINE" =~ "=" ]] && ECMP["$CURRENT"]=1
        fi
    done <<< "$OUTPUT"

    LEAF_FAIL=0

    for PREFIX in ${EXPECTED_PREFIXES[$LEAF]}; do
        if [[ ${PATHS[$PREFIX]:-0} -gt 1 && ${ECMP[$PREFIX]:-0} -eq 1 ]]; then
            echo "  ✅ $PREFIX has ECMP paths (${PATHS[$PREFIX]})"
        else
            echo "  ❌ $PREFIX missing ECMP"
            LEAF_FAIL=1
            GLOBAL_FAIL=1
        fi
    done

    if [[ $LEAF_FAIL -eq 0 ]]; then
        echo "  ✔ All expected ECMP routes present on $LEAF"
    else
        echo "  ✖ ECMP verification failed on $LEAF"
    fi
done

echo
if [[ $GLOBAL_FAIL -eq 0 ]]; then
    echo "✔ ECMP verified on all leafs"
else
    echo "✖ ECMP verification failed"
    exit 1
fi
