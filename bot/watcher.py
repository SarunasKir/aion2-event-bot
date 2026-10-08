"""Daily check of aion2hub for changed event and boss times.

aion2hub has no API, so this reads the same text a visitor sees on
https://aion2hub.com/tools/world-bosses and https://aion2hub.com/tools/event-timer
and looks for schedule phrases such as "Wed & Sat · 22:30 server" or
"Every 4h from 01:00 server" next to each known event or boss name.

Only times for events and bosses the bot already knows are updated. If the
page can't be read, or too little of it matches, nothing changes and the
built-in schedule in schedule.py stays in use.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from html.parser import HTMLParser

from .schedule import EVENTS, EVERY_DAY, REGION_LABELS, Rule

log = logging.getLogger("aion2bot.watcher")

BOSS_URL = "https://aion2hub.com/tools/world-bosses"
EVENT_URL = "https://aion2hub.com/tools/event-timer"

# Below this many readable boss schedules, assume the page layout changed and change nothing.
MIN_BOSS_MATCHES = 5

_DAY_NAMES = {
    "mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5, "sun": 6, "sunday": 6,
}
_DAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_DAY_RE = re.compile(r"\b(" + "|".join(sorted(_DAY_NAMES, key=len, reverse=True)) + r")s?\b", re.I)
_TIME_RE = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
_EVERY_RE = re.compile(r"every\s+(\d{1,2})\s*h(?:ours?)?\s+(?:from|starting at)\s+([01]?\d|2[0-3]):([0-5]\d)", re.I)
_VERSION_RE = re.compile(r"\((?:[^()]*client[^()]*\d{4}-\d{2}-\d{2})\)", re.I)

KR_TW = ("KR", "TW")


# ----------------------------------------------------------------- page text

class _TextExtractor(HTMLParser):
    _BLOCK = {"p", "div", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "br", "section", "article", "span"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "template"):
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "template") and self._skip:
            self._skip -= 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def page_text(html: str) -> str:
    """Visible text of a page, one block per line."""
    parser = _TextExtractor()
    parser.feed(html)
    lines = (re.sub(r"\s+", " ", line).strip() for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line)


# ----------------------------------------------------------------- parsing

def parse_rule(snippet: str) -> Rule | None:
    """Turn 'Wed & Sat · 22:30 server' or 'Every 4h from 01:00 server' into a Rule."""
    every = _EVERY_RE.search(snippet)
    if every:
        step, hour, minute = int(every.group(1)), int(every.group(2)), int(every.group(3))
        if not 1 <= step <= 24 or 24 % step:
            return None
        hours = sorted((hour + i * step) % 24 for i in range(24 // step))
        return Rule(tuple((h, minute) for h in hours))
    times = [(int(h), int(m)) for h, m in _TIME_RE.findall(snippet)]
    if not times:
        return None
    days = frozenset(_DAY_NAMES[d.lower()] for d in _DAY_RE.findall(snippet))
    return Rule(tuple(sorted(set(times))), days or EVERY_DAY)


def _sections(text: str) -> list[tuple[int, tuple[str, ...]]]:
    """Positions where the Global or Korea/Taiwan part of the boss page begins."""
    found = []
    for line_match in re.finditer(r"^.*$", text, re.M):
        line = line_match.group(0)
        if len(line) > 120 or "client" not in line.lower():
            continue
        if re.search(r"korea\s*/\s*taiwan|kr\s*/\s*tw", line, re.I):
            found.append((line_match.start(), KR_TW))
        elif re.search(r"\bglobal\b", line, re.I):
            found.append((line_match.start(), ("GLOBAL",)))
    return sorted(found)


def _region_at(sections, pos: int) -> tuple[str, ...] | None:
    region = None
    for start, regions in sections:
        if start <= pos:
            region = regions
    return region


def _name_positions(text: str, names: dict[str, str]) -> list[tuple[int, int, str]]:
    """(start, end, key) of each event or boss name in the text, longest names first."""
    hits: list[tuple[int, int, str]] = []
    taken: list[range] = []
    for name in sorted(names, key=len, reverse=True):
        for m in re.finditer(re.escape(name), text, re.I):
            if any(m.start() in r for r in taken):
                continue
            hits.append((m.start(), m.end(), names[name]))
            taken.append(range(m.start(), m.end()))
    return sorted(hits)


def parse_bosses(text: str) -> dict[tuple[str, str], Rule]:
    """{(boss key, region): Rule} read from the world boss page text."""
    sections = _sections(text)
    names = {e.name: e.key for e in EVENTS.values() if e.category == "boss"}
    hits = _name_positions(text, names)
    found: dict[tuple[str, str], Rule] = {}
    for i, (start, end, key) in enumerate(hits):
        region = _region_at(sections, start)
        if region is None:
            continue
        stop = hits[i + 1][0] if i + 1 < len(hits) else len(text)
        snippet = text[end:min(stop, end + 200)]
        snippet = snippet.split("\n\n")[0]
        if "server" not in snippet.lower():
            continue
        rule = parse_rule(snippet)
        if rule:
            for r in region:
                found.setdefault((key, r), rule)
    return found


def parse_events(text: str) -> dict[tuple[str, str], Rule]:
    """{(event key, region): Rule} read from the event timer page text.

    Reads Spacetime Rift hours, and the Korea/Taiwan-only Abyss Rift Zone and
    Spacetime Rift Domination days and times.
    """
    found: dict[tuple[str, str], Rule] = {}
    for line in text.splitlines():
        lower = line.lower()
        times = _TIME_RE.findall(line)
        # Rift hours, e.g. "... every 3 hours at 02:00, 05:00, ... and 23:00 server time."
        if "rift" in lower and "domination" not in lower and "abyss" not in lower and len(times) >= 6:
            rule = Rule(tuple(sorted({(int(h), int(m)) for h, m in times})))
            is_global = bool(re.search(r"global|north america", lower))
            is_kr_tw = bool(re.search(r"korea|taiwan|\bkr\b|\btw\b", lower))
            if is_global and not is_kr_tw:
                found.setdefault(("spacetime_rift", "GLOBAL"), rule)
            elif is_kr_tw and not is_global:
                for r in KR_TW:
                    found.setdefault(("spacetime_rift", r), rule)
        for key, name in (("abyss_rift_zone", "abyss rift zone"), ("rift_domination", "rift domination")):
            idx = lower.find(name)
            if idx < 0 or not times:
                continue
            rule = parse_rule(line[idx:idx + 250])
            if rule and rule.days != EVERY_DAY:
                for r in KR_TW:
                    found.setdefault((key, r), rule)
    return found


def source_versions(text: str) -> list[str]:
    """Data version notes such as '(client v110, 2026-09-09)'."""
    return sorted(set(_VERSION_RE.findall(text)))


# ----------------------------------------------------------------- saving

def to_json(parsed: dict[tuple[str, str], Rule]) -> list[dict]:
    return [
        {"key": k, "region": r, "days": sorted(rule.days), "times": [list(t) for t in rule.times]}
        for (k, r), rule in sorted(parsed.items())
    ]


def from_json(items: list[dict]) -> dict[tuple[str, str], Rule]:
    return {
        (i["key"], i["region"]): Rule(tuple(tuple(t) for t in i["times"]), frozenset(i["days"]))
        for i in items
    }


# ----------------------------------------------------------------- applying

def describe(rule: Rule) -> str:
    days = "daily" if rule.days == EVERY_DAY else " & ".join(_DAY_SHORT[d] for d in sorted(rule.days))
    times = ", ".join(f"{h:02d}:{m:02d}" for h, m in rule.times)
    return f"{days} {times}"


def _same(a: tuple[Rule, ...], b: Rule) -> bool:
    return len(a) == 1 and a[0].days == b.days and sorted(a[0].times) == sorted(b.times)


@dataclass
class Change:
    key: str
    region: str
    old: str
    new: str

    def line(self) -> str:
        return f"**{EVENTS[self.key].name}** ({REGION_LABELS[self.region]}): {self.old} → {self.new}"


def diff(parsed: dict[tuple[str, str], Rule]) -> list[Change]:
    changes = []
    for (key, region), rule in sorted(parsed.items()):
        event = EVENTS.get(key)
        if event is None:
            continue
        current = event.rules.get(region, ())
        if not _same(current, rule):
            old = ", ".join(describe(r) for r in current) or "not scheduled"
            changes.append(Change(key, region, old, describe(rule)))
    return changes


def apply(parsed: dict[tuple[str, str], Rule]) -> None:
    """Replace the matching rules in EVENTS in place."""
    for (key, region), rule in parsed.items():
        event = EVENTS.get(key)
        if event is None:
            continue
        rules = dict(event.rules)
        rules[region] = (rule,)
        EVENTS[key] = replace(event, rules=rules)


def read_pages(boss_html: str | None, event_html: str | None) -> tuple[dict[tuple[str, str], Rule], list[str]]:
    """Parse both pages. Returns (schedule found, problems)."""
    parsed: dict[tuple[str, str], Rule] = {}
    problems: list[str] = []
    if boss_html:
        bosses = parse_bosses(page_text(boss_html))
        if len(bosses) >= MIN_BOSS_MATCHES:
            parsed.update(bosses)
        else:
            problems.append(f"Read only {len(bosses)} boss times from the boss page, so boss times were left as they are.")
    else:
        problems.append("Couldn't download the boss page.")
    if event_html:
        parsed.update(parse_events(page_text(event_html)))
    else:
        problems.append("Couldn't download the event timer page.")
    return parsed, problems


async def fetch(session, url: str) -> str | None:
    import aiohttp

    try:
        async with session.get(
            url, headers={"User-Agent": "Mozilla/5.0 (aion2-event-bot)"}, timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            if resp.status != 200:
                log.warning("GET %s returned %s", url, resp.status)
                return None
            return await resp.text()
    except Exception:
        log.exception("GET %s failed", url)
        return None
