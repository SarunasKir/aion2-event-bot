"""Watcher tests against HTML shaped like aion2hub's pages (text as quoted from the live site)."""

import pytest

from bot import watcher
from bot.schedule import EVENTS, Rule, WED, SAT, TUE, FRI

BOSS_PAGE = """
<html><body><script>var x = "Executor Argo Mon 01:00 server";</script>
<h2>Global data (Launch Scale Test client, 2026-09-19).</h2>
<h3>Chaotic Lower Reshanta</h3>
<div><h4>Watcher Kaira</h4><span>Interval</span><span>Every 3h from 01:00 server</span></div>
<div><h4>Executor Argo</h4><span>Mon &amp; Thu &amp; Sat · 21:30 server</span></div>
<div><h4>Executor Kaira</h4><span>Mon &amp; Thu &amp; Sat · 21:30 server</span></div>
<div><h4>Executor Tamasa</h4><span>Mon &amp; Thu &amp; Sat · 21:30 server</span></div>
<div><h4>Abyss Siege Boss</h4><span>Sun &amp; Fri · 21:00 server</span></div>
<h2>Korea/Taiwan data (client v110, 2026-09-09).</h2>
<h3>Chaotic Lower Reshanta</h3>
<div><h4>Watcher Kaira</h4><span>Interval</span><span>Every 4h from 01:00 server</span></div>
<div><h4>Executor Argo</h4><span>Wed &amp; Sat · 22:30 server</span></div>
<div><h4>Executor Kaira</h4><span>Wed &amp; Sat · 22:30 server</span></div>
<div><h4>Executor Tamasa</h4><span>Wed &amp; Sat · 22:30 server</span></div>
<div><h4>Abyss Siege Boss</h4><span>Sun &amp; Fri · 22:00 server</span></div>
<h3>Chaotic Middle Reshanta</h3>
<div><h4>Enraged Guardian Lord Nahma</h4><span>Sun &amp; Fri · 22:00 server</span></div>
<div><h4>Executioner Dramos</h4><span>Wed &amp; Sat · 22:30 server</span></div>
<div><h4>Ravager Marakha</h4><span>Wed &amp; Sat · 22:30 server</span></div>
<div><h4>Turncoat Ducal</h4><span>Wed &amp; Sat · 22:30 server</span></div>
</body></html>
"""

EVENT_PAGE = """
<html><body>
<p>Korea and Taiwan Spacetime Rifts open every 3 hours, 8 times a day, at 02:00, 05:00, 08:00, 11:00, 14:00, 17:00, 20:00 and 23:00 server time.</p>
<p>Confirmed Global rift hours: North America, Europe, South America and Japan (GMT+9): 00:00, 03:00, 06:00, 09:00, 12:00, 15:00, 18:00 and 21:00.</p>
<p>Abyss Rift Zone runs every Tuesday and Thursday at 22:00 server time. It lasts up to 35 minutes.</p>
<p>Spacetime Rift Domination runs every Monday, Thursday and Saturday at 20:00 and 23:00 server time.</p>
</body></html>
"""


@pytest.fixture(autouse=True)
def restore_events():
    saved = dict(EVENTS)
    yield
    EVENTS.clear()
    EVENTS.update(saved)


def test_current_pages_match_built_in_schedule():
    parsed, problems = watcher.read_pages(BOSS_PAGE, EVENT_PAGE)
    assert problems == []
    assert len(parsed) == 9 * 2 + 5 + 3 + 2 * 2  # KR+TW bosses, Global bosses, rifts, two KR/TW PvP events
    assert watcher.diff(parsed) == []


def test_changed_boss_time_is_detected_and_applied():
    page = BOSS_PAGE.replace(
        "<h4>Turncoat Ducal</h4><span>Wed &amp; Sat · 22:30 server", "<h4>Turncoat Ducal</h4><span>Tue &amp; Fri · 21:00 server"
    )
    parsed, _ = watcher.read_pages(page, EVENT_PAGE)
    changes = watcher.diff(parsed)
    assert [(c.key, c.region, c.old, c.new) for c in changes] == [
        ("turncoat_ducal", "KR", "Wed & Sat 22:30", "Tue & Fri 21:00"),
        ("turncoat_ducal", "TW", "Wed & Sat 22:30", "Tue & Fri 21:00"),
    ]
    watcher.apply(parsed)
    assert EVENTS["turncoat_ducal"].rules["KR"] == (Rule(((21, 0),), frozenset({TUE, FRI})),)
    assert watcher.diff(parsed) == []


def test_changed_interval_and_rift_hours():
    boss = BOSS_PAGE.replace("Every 4h from 01:00 server", "Every 6h from 02:00 server")
    events = EVENT_PAGE.replace("00:00, 03:00, 06:00, 09:00, 12:00, 15:00, 18:00 and 21:00", "01:00, 04:00, 07:00, 10:00, 13:00, 16:00, 19:00 and 22:00")
    parsed, _ = watcher.read_pages(boss, events)
    keys = {(c.key, c.region) for c in watcher.diff(parsed)}
    assert keys == {("watcher_kaira", "KR"), ("watcher_kaira", "TW"), ("spacetime_rift", "GLOBAL")}
    assert parsed[("watcher_kaira", "KR")].times == ((2, 0), (8, 0), (14, 0), (20, 0))


def test_unreadable_page_changes_nothing():
    parsed, problems = watcher.read_pages("<html><body>Maintenance</body></html>", None)
    assert parsed == {}
    assert len(problems) == 2
    assert watcher.diff(parsed) == []


def test_saved_schedule_round_trips():
    parsed, _ = watcher.read_pages(BOSS_PAGE, EVENT_PAGE)
    assert watcher.from_json(watcher.to_json(parsed)) == parsed


def test_source_versions():
    assert watcher.source_versions(watcher.page_text(BOSS_PAGE)) == [
        "(Launch Scale Test client, 2026-09-19)",
        "(client v110, 2026-09-09)",
    ]


def test_parse_rule_variants():
    assert watcher.parse_rule("Wednesdays and Saturdays at 22:30 server time") == Rule(((22, 30),), frozenset({WED, SAT}))
    assert watcher.parse_rule("no times here") is None


def test_bot_update_saves_and_reloads(monkeypatch, tmp_path):
    import asyncio
    import json

    from bot.main import EventBot
    from bot.storage import Storage

    pages = {
        watcher.BOSS_URL: BOSS_PAGE.replace("Sun &amp; Fri · 22:00 server</span></div>\n<h3>", "Sat · 19:00 server</span></div>\n<h3>"),
        watcher.EVENT_URL: EVENT_PAGE,
    }

    async def fake_fetch(session, url):
        return pages[url]

    monkeypatch.setattr(watcher, "fetch", fake_fetch)
    storage = Storage(str(tmp_path / "b.db"))

    async def run():
        bot = EventBot(storage)
        return await bot.update_schedule()

    changes, problems, _ = asyncio.run(run())
    assert problems == []
    assert {(c.key, c.new) for c in changes} == {("abyss_siege_boss", "Sat 19:00")}
    saved = watcher.from_json(json.loads(storage.get_meta("schedule")))
    assert saved[("abyss_siege_boss", "KR")].times == ((19, 0),)
