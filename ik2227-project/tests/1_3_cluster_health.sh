#!/bin/bash

CONTROLLERS=(
  controller1
  controller2
)

echo "=== Kubernetes Cluster Health Check ==="

FAIL=0

for CTRL in "${CONTROLLERS[@]}"; do
    echo
    echo "--- Checking cluster on $CTRL ---"

    # Check nodes
    NODES=$(kathara exec "$CTRL" "kubectl get nodes --no-headers" 2>&1)

    if echo "$NODES" | awk '{print $2}' | grep -vq "Ready"; then
        echo "  ❌ Not all nodes are Ready on $CTRL"
        echo "$NODES"
        FAIL=1
    else
        echo "  ✅ All nodes are Ready"
    fi

    # Check pods
    PODS=$(kathara exec "$CTRL" "kubectl get pods -A --no-headers" 2>&1)

    if echo "$PODS" | awk '{print $4}' \
        | grep -Ev "Running|Completed" >/dev/null; then
        echo "  ❌ Some pods are not healthy on $CTRL"
        echo "$PODS" | grep -Ev "Running|Completed"
        FAIL=1
    else
        echo "  ✅ All pods are Running or Completed"
    fi
done

echo
if [[ $FAIL -eq 0 ]]; then
    echo "✔ Both Kubernetes clusters are healthy"
else
    echo "✖ Kubernetes cluster health check failed"
    exit 1
fi
