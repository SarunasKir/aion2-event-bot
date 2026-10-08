from datetime import datetime, timedelta, timezone

from bot.announcer import due
from bot.schedule import EVENTS, REGIONS, next_occurrence, occurrences
from bot.storage import GuildConfig, Storage

UTC = timezone.utc


def kst(*args):
    return datetime(*args, tzinfo=REGIONS["KR"])


def test_rift_kr_every_three_hours():
    start = kst(2026, 10, 8, 0, 0)
    got = occurrences(EVENTS["spacetime_rift"], "KR", start, start + timedelta(days=1))
    assert [t.astimezone(REGIONS["KR"]).hour for t in got] == [2, 5, 8, 11, 14, 17, 20, 23]


def test_rift_global_hours():
    start = kst(2026, 10, 8, 0, 0)
    got = occurrences(EVENTS["spacetime_rift"], "GLOBAL", start, start + timedelta(days=1))
    assert [t.astimezone(REGIONS["GLOBAL"]).hour for t in got] == [3, 6, 9, 12, 15, 18, 21, 0]


def test_domination_only_on_mon_thu_sat():
    # 2026-10-05 is a Monday.
    start = kst(2026, 10, 5, 0, 0)
    got = occurrences(EVENTS["rift_domination"], "KR", start, start + timedelta(days=7))
    local = [t.astimezone(REGIONS["KR"]) for t in got]
    assert {t.strftime("%a") for t in local} == {"Mon", "Thu", "Sat"}
    assert len(local) == 6


def test_kr_only_event_absent_on_global():
    assert next_occurrence(EVENTS["abyss_rift_zone"], "GLOBAL", datetime.now(UTC)) is None


def test_taiwan_uses_its_own_timezone():
    nxt = next_occurrence(EVENTS["daily_reset"], "TW", datetime(2026, 10, 8, 0, 0, tzinfo=UTC))
    assert nxt == datetime(2026, 10, 8, 21, 0, tzinfo=UTC)  # 05:00 GMT+8 on the 9th


def test_due_ten_minutes_before(tmp_path):
    cfg = GuildConfig(1, 10, 20, "KR", 10)
    rift = kst(2026, 10, 8, 14, 0)
    assert due(cfg, ["spacetime_rift"], rift - timedelta(minutes=11)) == []
    hits = due(cfg, ["spacetime_rift"], rift - timedelta(minutes=10))
    assert [(e.key, t) for e, t in hits] == [("spacetime_rift", rift)]


def test_due_at_start_with_zero_lead():
    cfg = GuildConfig(1, 10, 20, "KR", 0)
    rift = kst(2026, 10, 8, 14, 0)
    assert due(cfg, ["spacetime_rift"], rift + timedelta(seconds=10))


def test_storage_dedupes_pings(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    assert s.mark_sent(1, "spacetime_rift", 100)
    assert not s.mark_sent(1, "spacetime_rift", 100)
    s.follow(1, "beritra_air_raid")
    s.follow(1, "beritra_air_raid")
    assert s.followed(1) == ["beritra_air_raid"]


def test_executors_share_one_ping_on_kr():
    from bot.announcer import format_ping

    cfg = GuildConfig(1, 10, 20, "KR", 10)
    # 2026-10-07 is a Wednesday; Executors spawn 22:30 KST.
    spawn = kst(2026, 10, 7, 22, 30)
    hits = due(cfg, ["executor_argo", "executor_kaira", "spacetime_rift"], spawn - timedelta(minutes=10))
    assert {e.key for e, _ in hits} == {"executor_argo", "executor_kaira"}
    msg = format_ping([e for e, _ in hits], spawn, 20)
    assert msg.startswith("<@&20> **Executor Argo**, **Executor Kaira** spawn <t:")


def test_watcher_kaira_interval_differs_by_region():
    start = kst(2026, 10, 8, 0, 0)
    kr = occurrences(EVENTS["watcher_kaira"], "KR", start, start + timedelta(days=1))
    gl = occurrences(EVENTS["watcher_kaira"], "GLOBAL", start, start + timedelta(days=1))
    assert len(kr) == 6 and len(gl) == 8


def test_middle_reshanta_bosses_not_on_global():
    assert next_occurrence(EVENTS["turncoat_ducal"], "GLOBAL", datetime.now(UTC)) is None
    assert next_occurrence(EVENTS["turncoat_ducal"], "TW", datetime.now(UTC)) is not None


def test_unfollowed_bosses_are_not_pinged():
    cfg = GuildConfig(1, 10, 20, "GLOBAL", 10)
    siege = datetime(2026, 10, 9, 21, 0, tzinfo=REGIONS["GLOBAL"])  # Friday
    assert due(cfg, ["daily_reset", "executor_argo"], siege - timedelta(minutes=10)) == []
    assert [e.key for e, _ in due(cfg, ["abyss_siege_boss"], siege - timedelta(minutes=10))] == ["abyss_siege_boss"]
