# Ombro as a server, in a container. The desktop app connects to it by address with the pairing
# code the server prints at start (`docker compose logs ombro`). See README, "Running Ombro on a
# server". The image holds the Python core only; the desktop app is released apart.

# Base images are pinned by digest (the tag is kept for reading), so a moved tag can't change the build.
# To update one, take the new digest of the tag's multi-arch index and change it here.
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 /uv /bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
# The wheel, and the exact versions uv.lock tested, with their hashes, so the image installs those
# files and nothing else.
RUN uv build --wheel --out-dir /dist \
 && uv export --frozen --no-dev --no-emit-project -o /dist/requirements.txt

FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
# bubblewrap is the sandbox for code a job runs (code.run).
RUN apt-get update \
 && apt-get install -y --no-install-recommends bubblewrap \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /dist /tmp/dist
# Every library is checked against uv.lock's hashes; Ombro's own wheel, built above, adds nothing.
RUN pip install --no-cache-dir --require-hashes -r /tmp/dist/requirements.txt \
 && pip install --no-cache-dir --no-deps /tmp/dist/*.whl && rm -rf /tmp/dist

# Ombro runs as an ordinary user. Its settings and its jobs and chats are the two volumes; a folder
# to share with jobs is mounted under /home/ombro/shared and then shared in Settings › Folders.
RUN useradd --create-home --uid 1000 ombro
USER ombro
WORKDIR /home/ombro
RUN mkdir -p /home/ombro/.config/ombro /home/ombro/Ombro /home/ombro/shared
VOLUME ["/home/ombro/.config/ombro", "/home/ombro/Ombro"]

EXPOSE 8765
# Every address inside the container, so Docker's published port reaches it. Publish the port to
# 127.0.0.1 only (compose.yaml does), and connect to it from elsewhere over SSH or Tailscale.
CMD ["ombro", "serve", "--host", "0.0.0.0", "--port", "8765", "--tick"]
