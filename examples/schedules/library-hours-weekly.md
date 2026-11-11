---
name: library-hours-weekly
cron: "0 8 * * mon"
spec: specs/library-hours.md
enabled: false
limits:
  allowance: conservative
  run_timeout: 10m
  tool_max_calls: {web.search: 4, web.fetch: 6}
grace: 15m
---

Runs the library-hours spec every Monday at 08:00, local time, with a small
allowance and tight tool caps, since nobody is watching. Copy the spec into
the crew's specs/ folder and this file into its schedules/ folder, then
`ombro schedule enable library-hours-weekly`.
