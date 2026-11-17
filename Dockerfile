# Ombro as a server, in a container. The desktop app connects to it by address with the pairing
# code the server prints at start (`docker compose logs ombro`). See README, "Running Ombro on a
# server". The image holds the Python core only; the desktop app is released apart.

FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
# The wheel, and the exact versions uv.lock tested, so the image installs those and nothing newer.
RUN uv build --wheel --out-dir /dist \
 && uv export --frozen --no-dev --no-hashes --no-emit-project -o /dist/requirements.txt

FROM python:3.12-slim-bookworm
# bubblewrap is the sandbox for code a job runs (code.run).
RUN apt-get update \
 && apt-get install -y --no-install-recommends bubblewrap \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /dist /tmp/dist
RUN pip install --no-cache-dir -r /tmp/dist/requirements.txt /tmp/dist/*.whl && rm -rf /tmp/dist

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
