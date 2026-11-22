# The browser service's network guard: it owns the network the browser uses, and refuses any new
# connection from it to private, link-local and loopback-like ranges (the local network, this
# machine, a cloud's metadata service). The browser container has no rights to change this.
FROM python:3.14-slim-bookworm@sha256:3fe1a0b6c2d6b2ebb572906cdabeca75aeb558c0c17db5ffefbcf8094871c32b
RUN apt-get update \
 && apt-get install -y --no-install-recommends iptables \
 && rm -rf /var/lib/apt/lists/*
COPY browser-guard.sh /usr/local/bin/browser-guard
CMD ["/usr/local/bin/browser-guard"]
