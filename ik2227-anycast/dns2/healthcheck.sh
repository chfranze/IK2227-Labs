#!/bin/bash

while true; do
    dig google.com @127.0.0.1 > /dev/null

    if [[ $? != 0 ]]; then
        vtysh -c "configure" \
              -c "router bgp 70" \
              -c "no network 8.8.8.8/32" > /dev/null
    else
        vtysh -c "configure" \
              -c "router bgp 70" \
              -c "network 8.8.8.8/32" > /dev/null
    fi

    sleep 2
done
