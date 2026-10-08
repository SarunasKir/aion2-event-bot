"""Aion 2 event and world boss schedule, mirrored from aion2hub.com.

Events come from https://aion2hub.com/tools/event-timer and bosses from
https://aion2hub.com/tools/world-bosses. The site has no public API: its
timers compute countdowns in the browser from a fixed weekly schedule. This
module holds that same schedule as data.
When the game changes its timetable, edit EVENTS below.

All times are *server* time for the region. Weekdays use Python's numbering
(Monday = 0 ... Sunday = 6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

REGIONS: dict[str, timezone] = {
    "KR": timezone(timedelta(hours=9), "KST"),
    "TW": timezone(timedelta(hours=8), "GMT+8"),
    # aion2hub lists Global (NA / EU / SA / JP) rift hours in GMT+9.
    "GLOBAL": timezone(timedelta(hours=9), "GMT+9"),
}

REGION_LABELS = {
    "KR": "Korea",
    "TW": "Taiwan",
    "GLOBAL": "Global (NA / EU / SA / JP)",
}

EVERY_DAY = frozenset(range(7))
MON, TUE, WED, THU, FRI, SAT, SUN = range(7)


@dataclass(frozen=True)
class Rule:
    times: tuple[tuple[int, int], ...]  # (hour, minute) in server time
    days: frozenset[int] = EVERY_DAY


@dataclass(frozen=True)
class Event:
    key: str
    name: str
    description: str
    rules: dict[str, tuple[Rule, ...]] = field(default_factory=dict)
    category: str = "event"  # "event" or "boss"

    def regions(self) -> list[str]:
        return list(self.rules)


def _hours(*hours: int, minute: int = 0) -> tuple[tuple[int, int], ...]:
    return tuple((h, minute) for h in hours)


_EVERY_HOUR_HALF_PAST = _hours(*range(24), minute=30)
_KR_TW = ("KR", "TW")


def _boss(key: str, name: str, location: str, **rules: tuple[Rule, ...]) -> Event:
    return Event(key=key, name=name, description=location, rules=rules, category="boss")


def _same(rule: Rule, regions=_KR_TW) -> dict[str, tuple[Rule, ...]]:
    return {r: (rule,) for r in regions}

EVENTS: dict[str, Event] = {
    e.key: e
    for e in [
        Event(
            key="spacetime_rift",
            name="Spacetime Rift",
            description="Rift opens every 3 hours.",
            rules={
                "KR": (Rule(_hours(2, 5, 8, 11, 14, 17, 20, 23)),),
                "TW": (Rule(_hours(2, 5, 8, 11, 14, 17, 20, 23)),),
                "GLOBAL": (Rule(_hours(0, 3, 6, 9, 12, 15, 18, 21)),),
            },
        ),
        Event(
            key="beritra_air_raid",
            name="Beritra Air Raid",
            description="Field boss event on the half hour.",
            rules={r: (Rule(_EVERY_HOUR_HALF_PAST),) for r in REGIONS},
        ),
        Event(
            key="abyss_rift_zone",
            name="Abyss Rift Zone",
            description="Chapter 1 PvP, level 45+. 5 min prep, then a 30 min match.",
            rules={
                "KR": (Rule(_hours(22), frozenset({TUE, THU})),),
                "TW": (Rule(_hours(22), frozenset({TUE, THU})),),
            },
        ),
        Event(
            key="rift_domination",
            name="Spacetime Rift Domination",
            description="Stronghold PvP, level 50 / iLvl 4,500 / 500k CP. "
            "5 min prep, 15 min match, entry closes 5 min after start.",
            rules={
                "KR": (Rule(_hours(20, 23), frozenset({MON, THU, SAT})),),
                "TW": (Rule(_hours(20, 23), frozenset({MON, THU, SAT})),),
            },
        ),
        Event(
            key="daily_reset",
            name="Daily Reset",
            description="Daily content resets.",
            rules={
                "KR": (Rule(_hours(5)),),
                "TW": (Rule(_hours(5)),),
                "GLOBAL": (Rule(_hours(16)),),
            },
        ),
        Event(
            key="weekly_reset",
            name="Weekly Reset",
            description="Weekly content resets (Wednesday, at the daily reset hour).",
            rules={
                "KR": (Rule(_hours(5), frozenset({WED})),),
                "TW": (Rule(_hours(5), frozenset({WED})),),
                "GLOBAL": (Rule(_hours(16), frozenset({WED})),),
            },
        ),
        # World bosses (https://aion2hub.com/tools/world-bosses).
        _boss(
            "watcher_kaira", "Watcher Kaira", "Chaotic Lower Reshanta",
            KR=(Rule(_hours(1, 5, 9, 13, 17, 21)),),
            TW=(Rule(_hours(1, 5, 9, 13, 17, 21)),),
            GLOBAL=(Rule(_hours(1, 4, 7, 10, 13, 16, 19, 22)),),
        ),
        *[
            _boss(
                key, name, "Chaotic Lower Reshanta",
                **_same(Rule(_hours(22, minute=30), frozenset({WED, SAT}))),
                GLOBAL=(Rule(_hours(21, minute=30), frozenset({MON, THU, SAT})),),
            )
            for key, name in [
                ("executor_argo", "Executor Argo"),
                ("executor_kaira", "Executor Kaira"),
                ("executor_tamasa", "Executor Tamasa"),
            ]
        ],
        _boss(
            "abyss_siege_boss", "Abyss Siege Boss", "Abyss",
            **_same(Rule(_hours(22), frozenset({FRI, SUN}))),
            GLOBAL=(Rule(_hours(21), frozenset({FRI, SUN})),),
        ),
        _boss(
            "lord_nahma", "Enraged Guardian Lord Nahma", "Chaotic Middle Reshanta",
            **_same(Rule(_hours(22), frozenset({FRI, SUN}))),
        ),
        *[
            _boss(key, name, "Chaotic Middle Reshanta", **_same(Rule(_hours(22, minute=30), frozenset({WED, SAT}))))
            for key, name in [
                ("executioner_dramos", "Executioner Dramos"),
                ("ravager_marakha", "Ravager Marakha"),
                ("turncoat_ducal", "Turncoat Ducal"),
            ]
        ],
    ]
}


def occurrences(event: Event, region: str, start: datetime, end: datetime) -> list[datetime]:
    """Start times (UTC) of `event` in `region` with start < t <= end."""
    rules = event.rules.get(region)
    if not rules:
        return []
    tz = REGIONS[region]
    local_start = start.astimezone(tz)
    local_end = end.astimezone(tz)
    out: set[datetime] = set()
    day = local_start.date()
    while day <= local_end.date():
        for rule in rules:
            if day.weekday() not in rule.days:
                continue
            for hour, minute in rule.times:
                t = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
                if start < t <= end:
                    out.add(t.astimezone(timezone.utc))
        day += timedelta(days=1)
    return sorted(out)


def next_occurrence(event: Event, region: str, after: datetime) -> datetime | None:
    """Next start time (UTC) strictly after `after`, looking up to 8 days ahead."""
    found = occurrences(event, region, after, after + timedelta(days=8))
    return found[0] if found else None
