#!/bin/sh
# After the .deb or .rpm installs: /usr/bin/ombro runs the app's own command line (ombro-serve,
# the whole of `ombro`), so the terminal and the app are one Ombro. One already there that isn't
# ours (a system-wide pip install) is left alone.
if [ ! -e /usr/bin/ombro ] && [ ! -L /usr/bin/ombro ] || [ "$(readlink /usr/bin/ombro)" = ombro-serve ]; then
  ln -sfn ombro-serve /usr/bin/ombro
fi
exit 0
