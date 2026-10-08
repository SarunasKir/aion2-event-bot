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
