# The toolchain for building Ombro's Linux app (.deb) from any machine with Docker:
# scripts/build_linux.sh builds this image, then runs the build in it.
#
# Ubuntu 24.04 is the oldest Ubuntu the package is for: a binary built against its glibc runs on
# later releases (25.10, 26.04), not the other way round.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential curl wget file git ca-certificates pkg-config xz-utils \
      libwebkit2gtk-4.1-dev libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev libdbus-1-dev \
    && rm -rf /var/lib/apt/lists/*

# Node 22 (the GUI's npm build).
ARG NODE_VERSION=22.21.1
RUN arch=$(dpkg --print-architecture | sed 's/amd64/x64/') \
    && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${arch}.tar.xz" \
       | tar -xJ -C /usr/local --strip-components=1

# Rust (Tauri) and uv (the sidecar's Python, a standalone build the app carries).
ENV RUSTUP_HOME=/opt/rustup CARGO_HOME=/opt/cargo PATH=/opt/cargo/bin:/root/.local/bin:$PATH
RUN curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable \
    && curl -LsSf https://astral.sh/uv/install.sh | sh

ENV UV_PYTHON_PREFERENCE=only-managed UV_LINK_MODE=copy
WORKDIR /src
