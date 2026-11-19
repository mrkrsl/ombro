"""Five-field cron expressions, matched against local wall-clock time.

Fields: minute (0-59), hour (0-23), day of month (1-31), month (1-12 or
jan-dec) and day of week (0-7 or sun-sat; 0 and 7 are both Sunday). Each
field takes `*`, numbers, ranges (`1-5`), lists (`1,15`) and steps (`*/15`,
`9-17/2`). `@hourly`, `@daily`, `@weekly`, `@monthly` and `@yearly` are
accepted as shorthands.

When both the day of month and the day of week are restricted (neither
starts with `*`), a day matches if either does, as in standard cron.

Slots are named by their local wall-clock minute. Clock changes:
- When clocks go back, the repeated hour's slots occur twice; the caller
  starts a slot once, because it looks up the slot by its wall-clock name.
- When clocks go forward, slots in the skipped hour happen once, at the
  first minute after the change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DAYS = {d: i for i, d in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}
ALIASES = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}
_FIELDS = (("minute", 0, 59, None), ("hour", 0, 23, None), ("day of month", 1, 31, None),
           ("month", 1, 12, MONTHS), ("day of week", 0, 7, DAYS))
SLOT_FORMAT = "%Y-%m-%dT%H:%M"


class CronError(ValueError):
    pass


def _value(text: str, name: str, lo: int, hi: int, names: dict | None) -> int:
    t = text.strip().lower()
    if names and t in names:
        return names[t]
    if not t.isdigit():
        raise CronError(f"{name}: {text!r} is not a number" + (" or a name" if names else ""))
    v = int(t)
    if not lo <= v <= hi:
        raise CronError(f"{name}: {v} is outside {lo}-{hi}")
    return v


def _field(text: str, name: str, lo: int, hi: int, names: dict | None) -> frozenset[int]:
    out: set[int] = set()
    for item in text.split(","):
        if not item:
            raise CronError(f"{name}: empty item in {text!r}")
        base, _, step_text = item.partition("/")
        step = 1
        if step_text:
            if not step_text.isdigit() or int(step_text) == 0:
                raise CronError(f"{name}: step {step_text!r} must be a whole number above 0")
            step = int(step_text)
        if base == "*":
            a, b = lo, hi
        elif "-" in base:
            left, _, right = base.partition("-")
            a, b = _value(left, name, lo, hi, names), _value(right, name, lo, hi, names)
            if a > b:
                raise CronError(f"{name}: range {base!r} runs backwards")
        else:
            a = _value(base, name, lo, hi, names)
            b = hi if step_text else a  # `5/10` means from 5 to the end, every 10
        out.update(range(a, b + 1, step))
    return frozenset(out)


@dataclass(frozen=True)
class Cron:
    text: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 = Sunday
    day_star: bool
    weekday_star: bool

    def matches(self, when: datetime) -> bool:
        """Does this naive local wall-clock minute match?"""
        if when.minute not in self.minutes or when.hour not in self.hours or when.month not in self.months:
            return False
        return self.day_matches(when)

    def day_matches(self, when: datetime) -> bool:
        if when.month not in self.months:
            return False
        dom = when.day in self.days
        dow = (when.isoweekday() % 7) in self.weekdays
        if self.day_star or self.weekday_star:
            return dom and dow
        return dom or dow


def parse(text: str) -> Cron:
    if not isinstance(text, str) or not text.strip():
        raise CronError("the cron expression is empty")
    raw = " ".join(text.split())
    expanded = ALIASES.get(raw.lower(), raw)
    if expanded.startswith("@"):
        raise CronError(f"unknown shorthand {raw!r}; use one of {', '.join(sorted(ALIASES))}")
    parts = expanded.split(" ")
    if len(parts) != 5:
        raise CronError(f"a cron expression has five fields (minute hour day month weekday), not {len(parts)}")
    sets = [_field(p, *spec) for p, spec in zip(parts, _FIELDS)]
    weekdays = frozenset(0 if d == 7 else d for d in sets[4])
    cron = Cron(raw, sets[0], sets[1], sets[2], sets[3], weekdays,
                parts[2].startswith("*"), parts[4].startswith("*"))
    if not next_slots(cron, datetime(2000, 1, 1), 1, horizon_days=366 * 8):
        raise CronError(f"{raw!r} never matches a real date")
    return cron


def slot_name(when: datetime) -> str:
    return when.strftime(SLOT_FORMAT)


def to_local(ts: float, tz: tzinfo | None = None) -> datetime:
    """A timestamp as naive local wall-clock time (the system's zone unless tz is given)."""
    if tz is None:
        return datetime.fromtimestamp(ts)
    return datetime.fromtimestamp(ts, tz).replace(tzinfo=None)


def slots_between(cron: Cron, start: float, end: float, tz: tzinfo | None = None) -> list[tuple[datetime, float]]:
    """Slots that fell due in (start, end], as (wall-clock minute, the moment it fell due).

    Walks real minutes, so a slot is due when its minute actually happens. A slot
    in an hour skipped by clocks going forward falls due at the first minute after
    the change. A wall-clock minute repeated by clocks going back is listed once.
    """
    out: list[tuple[datetime, float]] = []
    seen: set[datetime] = set()
    t = (int(start) // 60 + 1) * 60
    prev = to_local(t - 60, tz).replace(second=0, microsecond=0)
    while t <= end:
        local = to_local(t, tz).replace(second=0, microsecond=0)
        if local - prev > timedelta(minutes=1):
            gap = prev + timedelta(minutes=1)
            while gap < local:
                if gap not in seen and cron.matches(gap):
                    seen.add(gap)
                    out.append((gap, float(t)))
                gap += timedelta(minutes=1)
        if local not in seen and cron.matches(local):
            seen.add(local)
            out.append((local, float(t)))
        prev = local
        t += 60
    return out


def next_slots(cron: Cron, after: datetime, n: int, horizon_days: int = 366 * 5) -> list[datetime]:
    """The next n wall-clock slots strictly after `after` (naive local). For display."""
    out: list[datetime] = []
    day = after.replace(hour=0, minute=0, second=0, microsecond=0)
    hours, minutes = sorted(cron.hours), sorted(cron.minutes)
    for _ in range(horizon_days):
        if cron.day_matches(day):
            for h in hours:
                for m in minutes:
                    when = day.replace(hour=h, minute=m)
                    if when > after:
                        out.append(when)
                        if len(out) >= n:
                            return out
        day += timedelta(days=1)
    return out


DAY_NAMES = ["Sundays", "Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays"]


def _clock(hour: int, minute: int) -> str:
    """6am, 6:30pm, noon, midnight."""
    if (hour, minute) == (12, 0):
        return "noon"
    if (hour, minute) == (0, 0):
        return "midnight"
    h = hour % 12 or 12
    return f"{h}{':%02d' % minute if minute else ''}{'am' if hour < 12 else 'pm'}"


def describe(cron: Cron) -> str:
    """When a schedule runs, in words where it is simple ("Runs weekdays at 6am"); otherwise its
    cron expression. Only a single time a day is put in words."""
    full_month = cron.months == frozenset(range(1, 13))
    if len(cron.minutes) != 1 or len(cron.hours) != 1:
        return f"Runs at {cron.text}"
    at = _clock(next(iter(cron.hours)), next(iter(cron.minutes)))
    if not full_month:
        if len(cron.months) == 1 and len(cron.days) == 1 and cron.weekday_star:
            month = datetime(2000, next(iter(cron.months)), 1).strftime("%B")
            return f"Runs on {next(iter(cron.days))} {month} at {at}"
        return f"Runs at {cron.text}"
    if cron.day_star and cron.weekday_star:
        return f"Runs every day at {at}"
    if cron.day_star:
        days = sorted(cron.weekdays)
        if days == [1, 2, 3, 4, 5]:
            return f"Runs weekdays at {at}"
        if days == [0, 6]:
            return f"Runs weekends at {at}"
        names = [DAY_NAMES[d] for d in days]
        joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        return f"Runs {joined} at {at}"
    if cron.weekday_star and len(cron.days) == 1:
        d = next(iter(cron.days))
        suffix = "th" if 10 <= d % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")
        return f"Runs on the {d}{suffix} of each month at {at}"
    return f"Runs at {cron.text}"


def describe_next(cron: Cron, now: datetime, n: int = 3) -> list[str]:
    return [w.strftime("%a %d %b %Y %H:%M") for w in next_slots(cron, now, n)]


def zone_name() -> str:
    """The machine's time zone by its IANA name (Europe/London), or "" when it can't be told."""
    import os

    tz = os.environ.get("TZ", "").lstrip(":")
    if "/" in tz and not tz.startswith("/"):
        return tz
    import re

    for link in (tz if tz.startswith("/") else "", "/etc/localtime"):
        if not link:
            continue
        # The link itself first (macOS: /var/db/timezone/zoneinfo/Europe/London), then where it ends up
        # (/usr/share/zoneinfo.default/Europe/London).
        for path in ((os.readlink(link) if os.path.islink(link) else ""), os.path.realpath(link)):
            m = re.search(r"zoneinfo[^/]*/(.+)$", path)
            if m and "/" in m.group(1):
                return m.group(1)
    try:
        with open("/etc/timezone", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def local_zone(now: datetime | None = None) -> str:
    """The zone schedules run in (this machine's local time), in words: "Europe/London (GMT,
    UTC+00:00)", or "GMT (UTC+00:00)" when the zone's name can't be told."""
    aware = (now or datetime.now()).astimezone()
    off = aware.strftime("%z") or "+0000"
    offset = f"UTC{off[0]}{off[1:3]}:{off[3:5]}"
    abbr = aware.tzname() or ""
    name = zone_name()
    if name:
        return f"{name} ({abbr + ', ' if abbr and abbr != name else ''}{offset})"
    return f"{abbr} ({offset})" if abbr else offset


def in_words(cron: Cron) -> str:
    """When a schedule runs, as words that follow "runs": "every weekday at 9am", "every Monday at
    7:30am", "on the 1st of each month at noon". "" when it isn't one time a day (the caller then uses
    its own words)."""
    text = describe(cron)
    if text == f"Runs at {cron.text}":
        return ""
    words = text.removeprefix("Runs ")
    for plural, single in (("weekdays", "every weekday"), ("weekends", "every weekend"),
                           *((d, "every " + d[:-1]) for d in DAY_NAMES)):
        if words.startswith(plural + " ") or words.startswith(plural + ","):
            words = single + words[len(plural):]
            break
    for d in DAY_NAMES:  # "every Monday, Wednesdays and Fridays" -> "every Monday, Wednesday and Friday"
        words = words.replace(d, d[:-1]) if words.startswith("every ") else words
    return words


def next_in_words(cron: Cron, now: datetime, n: int = 3) -> list[str]:
    """The next runs, as "Thu 19 Nov at 9am"."""
    return [f"{w.strftime('%a')} {w.day} {w.strftime('%b')} at {_clock(w.hour, w.minute)}"
            for w in next_slots(cron, now, n)]
