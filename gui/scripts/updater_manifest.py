"""Write latest.json, the manifest the app's updater reads, from a release's files.

    python3 gui/scripts/updater_manifest.py <folder> <version> <tag> <owner/repo>

The folder holds the release's files, each update with its .sig beside it (release_macos.sh and
build_linux.sh make them). Each platform the updater asks for ("{os}-{arch}", and with the kind
of install, "{os}-{arch}-{bundle}") gets the file's address on the release and its signature.
A bare "linux-{arch}" is the AppImage, which updates on any Linux.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

# (pattern on the file name, the updater's platform keys)
KINDS = [
    (r"_(aarch64|arm64)\.app\.tar\.gz$", ["darwin-aarch64", "darwin-aarch64-app"]),
    (r"_(amd64|x86_64)\.AppImage$", ["linux-x86_64", "linux-x86_64-appimage"]),
    (r"_(aarch64|arm64)\.AppImage$", ["linux-aarch64", "linux-aarch64-appimage"]),
    (r"_amd64\.deb$", ["linux-x86_64-deb"]),
    (r"_arm64\.deb$", ["linux-aarch64-deb"]),
    (r"\.x86_64\.rpm$", ["linux-x86_64-rpm"]),
    (r"\.aarch64\.rpm$", ["linux-aarch64-rpm"]),
]


def manifest(folder: Path, version: str, tag: str, repo: str, now: datetime | None = None) -> dict:
    platforms: dict[str, dict[str, str]] = {}
    for path in sorted(folder.iterdir()):
        keys = next((keys for pattern, keys in KINDS if re.search(pattern, path.name)), None)
        if keys is None:
            continue
        sig = path.with_name(path.name + ".sig")
        if not sig.is_file():
            raise SystemExit(f"{path.name} has no {sig.name}: an update must be signed")
        entry = {
            "signature": sig.read_text().strip(),
            "url": f"https://github.com/{repo}/releases/download/{tag}/{path.name}",
        }
        for key in keys:
            if key in platforms:
                raise SystemExit(f"two files for {key}: {platforms[key]['url']} and {path.name}")
            platforms[key] = entry
    if not platforms:
        raise SystemExit(f"no updates in {folder}")
    return {
        "version": version,
        "notes": f"See https://github.com/{repo}/releases/tag/{tag}",
        "pub_date": (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platforms": platforms,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__.strip().splitlines()[0])
        return 2
    folder, version, tag, repo = Path(argv[0]), *argv[1:]
    m = manifest(folder, version, tag, repo)
    (folder / "latest.json").write_text(json.dumps(m, indent=2) + "\n")
    print(f"latest.json: {', '.join(sorted(m['platforms']))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
