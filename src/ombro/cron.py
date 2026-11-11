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


def parse_slot(name: str) -> datetime:
    return datetime.strptime(name, SLOT_FORMAT)


def to_local(ts: float, tz: tzinfo | None = None) -> datetime:
    """A timestamp as naive local wall-clock time (the system's zone unless tz is given)."""
    if tz is None:
        return datetime.fromtimestamp(ts)
    return datetime.fromtimestamp(ts, tz).replace(tzinfo=None)


def to_timestamp(when: datetime, tz: tzinfo | None = None) -> float:
    if tz is None:
        return when.timestamp()
    return when.replace(tzinfo=tz).timestamp()


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


def describe_next(cron: Cron, now: datetime, n: int = 3) -> list[str]:
    return [w.strftime("%a %d %b %Y %H:%M") for w in next_slots(cron, now, n)]
