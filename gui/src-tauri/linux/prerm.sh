#!/bin/sh
# Before the .deb or .rpm is removed (not upgraded): take away the /usr/bin/ombro link postinst
# made. A .deb passes "remove" (or "upgrade"); an .rpm passes 0 on removal and 1 on upgrade.
case "$1" in
  remove | 0)
    if [ "$(readlink /usr/bin/ombro)" = ombro-serve ]; then
      rm -f /usr/bin/ombro
    fi
    ;;
esac
exit 0
