---
name: rename-photos
crews: [default]
folders: [photos]
limits:
  max_rounds: 2
  run_timeout: 10m
---

# Rename the photos in the shared photos folder by date

Done looks like: every .jpg in shared/photos renamed to YYYY-MM-DD-NN.jpg,
from the date in its current name or, failing that, its modified date, and a
list of the renames in workspace/renames.md.

Who uses it: me, browsing the folder by date afterwards.

Binding constraint: no file is lost or overwritten; every photo is still
there afterwards under exactly one name.

A waste would be: renaming anything that is not a .jpg, or moving files into
other folders.

Not wanted: editing the photos themselves; deleting duplicates.
