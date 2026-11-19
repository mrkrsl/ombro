#!/bin/sh
# Lets the browser reach the public internet only (and Ombro's own proxy, below). Replies to Ombro's own connections to the browser
# (port 9222) still pass, but the browser can't start a connection to Ombro, the host, the local
# network or a cloud metadata address, whatever a page's name resolves to (DNS rebinding).
set -eu
iptables -F OUTPUT
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
# Ombro's own proxy: every page's connection goes through it, on Ombro's container, on these
# ports only. The proxy itself refuses private addresses, so this opens nothing further. The rule
# names this compose network, not Ombro's address, so it still holds when Ombro's container is
# recreated with a new address. Only Ombro listens on these ports on this network.
# The network's gateway is the host, so it is refused first.
read -r compose_net gateway <<EOF2
$(python3 - <<'PY'
import socket, struct
net = gw = ""
for line in open("/proc/net/route").read().splitlines()[1:]:
    f = line.split()
    ip = lambda h: socket.inet_ntoa(struct.pack("<I", int(h, 16)))
    if f[1] == "00000000" and f[2] != "00000000":  # the default route: its gateway is the host
        gw = ip(f[2])
    elif f[2] == "00000000" and not net:  # the directly connected route: this network
        net = f"{ip(f[1])}/{bin(int(f[7], 16)).count('1')}"
print(net, gw)
PY
)
EOF2
if [ -n "$compose_net" ] && [ -n "$gateway" ]; then
  iptables -A OUTPUT -d "$gateway" -j REJECT
  iptables -A OUTPUT -p tcp -d "$compose_net" --dport "${OMBRO_PROXY_PORTS:-9300:9399}" -j ACCEPT
  echo "browser network guard: Ombro's proxy on $compose_net (not the host, $gateway), ports ${OMBRO_PROXY_PORTS:-9300:9399}"
else
  echo "browser network guard: this network's address range not found; pages can't be opened" >&2
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
