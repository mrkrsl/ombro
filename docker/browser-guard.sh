#!/bin/sh
# Lets the browser reach the public internet only. Replies to Ombro's own connections to the browser
# (port 9222) still pass, but the browser can't start a connection to Ombro, the host, the local
# network or a cloud metadata address, whatever a page's name resolves to (H2, DNS rebinding).
set -eu
iptables -F OUTPUT
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
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
