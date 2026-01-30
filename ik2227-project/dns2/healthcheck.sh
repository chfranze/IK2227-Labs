#!/bin/bash

while true; do
    dig google.com @127.0.0.1 > /dev/null

    if [[ $? != 0 ]]; then
        vtysh -c "configure" \
              -c "router bgp 40" \
              -c "no network 10.30.40.2/24" > /dev/null
    else
        vtysh -c "configure" \
              -c "router bgp 40" \
              -c "network 10.30.40.2/24" > /dev/null
    fi

    sleep 2
done
