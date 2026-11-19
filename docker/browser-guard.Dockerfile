# The browser service's network guard: it owns the network the browser uses, and refuses any new
# connection from it to private, link-local and loopback-like ranges (the local network, this
# machine, a cloud's metadata service). The browser container has no rights to change this.
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
RUN apt-get update \
 && apt-get install -y --no-install-recommends iptables \
 && rm -rf /var/lib/apt/lists/*
COPY browser-guard.sh /usr/local/bin/browser-guard
CMD ["/usr/local/bin/browser-guard"]
