#!/bin/bash
# Run as the ordinary user inside Whonix-Workstation, before and after
# stopping Whonix-Gateway. This deliberately bypasses application wrappers.
set -u
date -u
uname -m
ip -br address
ip route
env -u LD_PRELOAD /usr/bin/curl.anondist-orig --noproxy '*' --proxy '' \
  --max-time 30 https://check.torproject.org/api/ip
printf '\nTOR_HTTP_EXIT=%s\n' "$?"
env -u LD_PRELOAD python3 - <<'PY'
import socket
for family, address in [(socket.AF_INET, '1.1.1.1'),
                        (socket.AF_INET6, '2606:4700:4700::1111')]:
    with socket.socket(family, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        print('RAW_TCP', address, 'RESULT', connection.connect_ex((address, 443)))
PY
env -u LD_PRELOAD dig +time=3 +tries=1 @10.152.152.10 example.com A +short
printf 'DNS_EXIT=%s\n' "$?"
