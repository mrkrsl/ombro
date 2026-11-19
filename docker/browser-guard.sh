#!/bin/sh
# Lets the browser reach the public internet only (and Ombro's own proxy, below). Replies to Ombro's own connections to the browser
# (port 9222) still pass, but the browser can't start a connection to Ombro, the host, the local
# network or a cloud metadata address, whatever a page's name resolves to (DNS rebinding).
set -eu
iptables -F OUTPUT
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
# Ombro's own proxy: every page's connection goes through it, on Ombro's container, on these
# ports only. The proxy itself refuses private addresses, so this opens nothing further.
if [ -n "${OMBRO_SERVICE:-}" ]; then
  ombro_ip=""
  for _ in $(seq 1 30); do
    ombro_ip=$(getent ahostsv4 "$OMBRO_SERVICE" | awk 'NR==1 {print $1}')
    [ -n "$ombro_ip" ] && break
    sleep 1
  done
  if [ -n "$ombro_ip" ]; then
    iptables -A OUTPUT -p tcp -d "$ombro_ip" --dport "${OMBRO_PROXY_PORTS:-9300:9399}" -j ACCEPT
    echo "browser network guard: Ombro's proxy at $ombro_ip:${OMBRO_PROXY_PORTS:-9300:9399}"
  else
    echo "browser network guard: $OMBRO_SERVICE not found; pages can't be opened" >&2
  fi
fi
for net in 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 \
           192.0.0.0/24 192.168.0.0/16 198.18.0.0/15 224.0.0.0/4 240.0.0.0/4; do
  iptables -A OUTPUT -d "$net" -j REJECT
done
# IPv6: global unicast only.
ip6tables -F OUTPUT
ip6tables -A OUTPUT -o lo -j ACCEPT
ip6tables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
ip6tables -A OUTPUT -d fc00::/7 -j REJECT
ip6tables -A OUTPUT -d 2000::/3 -j ACCEPT
ip6tables -A OUTPUT -j REJECT
touch /tmp/ready
echo "browser network guard: private ranges blocked"
exec sleep infinity
