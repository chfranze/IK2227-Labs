#!/bin/bash

CLIENTS=(
  client_int
  client_basic
)

QUERY='{"query":"An orc wanted to destroy everything"}'
URLS=(
  "http://clusterb.com/completion"
  "http://clustera.com/completion"
)

echo "=== LLM Completion Service Test (All Clients) ==="

FAIL=0

for CLIENT in "${CLIENTS[@]}"; do
    echo
    echo "--- Testing from $CLIENT ---"

    for URL in "${URLS[@]}"; do
        echo "Requesting $URL..."

        RESPONSE=$(kathara exec "$CLIENT" \
            "curl -s -X POST -H 'Content-Type: application/json' -d '$QUERY' $URL")

        if echo "$RESPONSE" | grep -q '"result"'; then
            echo "  ✅ Response received"
            echo "     $(echo "$RESPONSE" | cut -c1-80)..."
        else
            echo "  ❌ No valid result"
            echo "     Response: ${RESPONSE:-<empty>}"
            FAIL=1
        fi
    done
done

echo
if [[ $FAIL -eq 0 ]]; then
    echo "✔ Completion service reachable from all clients"
else
    echo "✖ Completion service test failed"
    exit 1
fi
