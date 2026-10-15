"""Boss ping images: drawing, styles, storage and attaching them to pings."""

import asyncio
import io
import json
import types
from datetime import datetime, timedelta

import pytest
from PIL import Image

from bot import cards
from bot.announcer import card_time
from bot.main import EventBot
from bot.schedule import REGIONS
from bot.storage import GuildConfig, Storage

KR = REGIONS["KR"]
ARGO = datetime(2026, 10, 7, 22, 30, tzinfo=KR)  # Executor Argo, Wednesday


def _size(png: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(png)) as img:
        assert img.format == "PNG"
        return img.size


@pytest.mark.parametrize("layout", cards.CHOICES["layout"])
@pytest.mark.parametrize("border", cards.CHOICES["border"])
@pytest.mark.parametrize("pattern", cards.CHOICES["pattern"])
def test_every_style_option_draws(layout, border, pattern):
    style = cards.CardStyle(layout=layout, border=border, pattern=pattern, tagline="Legion, assemble!")
    png = cards.render(style, "World boss", "Executor Argo", "Spawns in 10 min · 22:30 server time", "Reshanta")
    assert _size(png) == (cards.W, cards.H)


def test_long_names_and_light_colors_draw():
    style = cards.CardStyle(bg_top="#ffffff", bg_bottom="#eeeeee", text="#111111", accent="#ffee00",
                            font="sans", uppercase=False)
    names = " · ".join(["Third Unit Commander Minasara"] * 4)
    assert _size(cards.render(style, "Field boss", names, "Spawning now")) == (cards.W, cards.H)


def test_example_image_becomes_background():
    src = io.BytesIO()
    Image.new("RGB", (640, 640), (200, 30, 30)).save(src, "PNG")
    background = cards.prepare_background(src.getvalue())
    with Image.open(io.BytesIO(background)) as img:
        assert img.size == (cards.W, cards.H)
    assert _size(cards.sample(cards.CardStyle(), background)) == (cards.W, cards.H)
    with pytest.raises(cards.StyleError):
        cards.prepare_background(b"not an image")


def test_style_keeps_defaults_for_bad_values():
    style = cards.CardStyle.from_dict({
        "bg_top": "#ABCDEF", "accent": "red", "font": "comic", "layout": "center",
        "uppercase": "yes", "tagline": "  a   very long tagline that goes on and on  ",
    })
    default = cards.CardStyle()
    assert style.bg_top == "#abcdef" and style.layout == "center"
    assert (style.accent, style.font, style.uppercase) == (default.accent, default.font, default.uppercase)
    assert style.tagline == "a very long tagline that goes"
    assert cards.CardStyle.from_dict(json.loads(style.to_json())) == style


def test_card_time():
    now = ARGO - timedelta(minutes=10)
    assert card_time(ARGO, "KR", now) == "Spawns in 10 min · 22:30 server time (KST)"
    assert card_time(ARGO, "KR", ARGO - timedelta(minutes=90)).startswith("Spawns in 1h 30m")
    assert card_time(ARGO, "KR", ARGO - timedelta(minutes=120)).startswith("Spawns in 2h ·")
    assert card_time(ARGO, "KR", ARGO).startswith("Spawning now")


def test_storage_keeps_style_when_turned_off(tmp_path):
    s = Storage(str(tmp_path / "c.db"))
    assert s.card_style(1) is None
    s.set_card_enabled(1, True)
    assert s.card_style(1) == (True, "{}", None)
    s.set_card_style(1, '{"layout": "center"}', b"jpeg")
    s.set_card_enabled(1, False)
    assert s.card_style(1) == (False, '{"layout": "center"}', b"jpeg")
    s.set_card_enabled(1, True)
    assert s.card_style(1)[0] is True


class Chan:
    def __init__(self, attach=True):
        self.id, self.mention, self.sent, self.attach = 10, "<#10>", [], attach

    def permissions_for(self, member):
        return types.SimpleNamespace(send_messages=True, attach_files=self.attach)

    async def send(self, text, **kw):
        self.sent.append((text, kw))
        return types.SimpleNamespace(id=1000 + len(self.sent))


def _bot(tmp_path, chan, follow=("executor_argo", "spacetime_rift")):
    s = Storage(str(tmp_path / "b.db"))
    s.save_config(GuildConfig(1, 10, 3, "KR", 10))
    for key in follow:
        s.follow(1, key)
    system = Chan()
    system.id = 99
    guild = types.SimpleNamespace(id=1, name="G", me=object(), system_channel=system, owner=None, owner_id=1,
                                  get_channel=lambda c: chan)
    bot = EventBot(s)
    bot.get_guild = lambda gid: guild
    return bot, system


def _tick(bot, now):
    asyncio.run(bot._check_guild(bot.storage.get_config(1), now))


def test_boss_pings_get_an_image_and_events_dont(tmp_path):
    chan = Chan()
    bot, _ = _bot(tmp_path, chan)
    bot.storage.set_card_style(1, cards.CardStyle(pattern="sparks").to_json(), None)
    _tick(bot, ARGO - timedelta(minutes=10))
    text, kw = chan.sent[0]
    assert "Executor Argo" in text
    assert kw["file"].filename == cards.FILENAME
    assert _size(kw["file"].fp.read()) == (cards.W, cards.H)
    rift = datetime(2026, 10, 7, 23, 0, tzinfo=KR)
    _tick(bot, rift - timedelta(minutes=10))
    assert "Spacetime Rift" in chan.sent[1][0] and "file" not in chan.sent[1][1]


def test_field_boss_pings_get_an_image(tmp_path):
    chan = Chan()
    bot, _ = _bot(tmp_path, chan, follow=())
    bot.storage.set_card_enabled(1, True)
    now = ARGO
    bot.storage.set_field_timer(1, "Silent Dartan", "Altgard", int((now + timedelta(minutes=5)).timestamp()))
    _tick(bot, now)
    assert "file" in chan.sent[0][1] and "view" in chan.sent[0][1]


def test_no_image_when_off(tmp_path):
    chan = Chan()
    bot, _ = _bot(tmp_path, chan)
    bot.storage.set_card_style(1, "{}", None)
    bot.storage.set_card_enabled(1, False)
    _tick(bot, ARGO - timedelta(minutes=10))
    assert "file" not in chan.sent[0][1]


def test_ping_still_posts_without_attach_permission(tmp_path):
    chan = Chan(attach=False)
    bot, system = _bot(tmp_path, chan)
    bot.storage.set_card_enabled(1, True)
    _tick(bot, ARGO - timedelta(minutes=10))
    assert "Executor Argo" in chan.sent[0][0] and "file" not in chan.sent[0][1]
    assert "Attach Files" in system.sent[0][0]


def test_broken_style_still_posts_the_ping(tmp_path):
    chan = Chan()
    bot, _ = _bot(tmp_path, chan)
    bot.storage.set_card_style(1, "not json", None)
    _tick(bot, ARGO - timedelta(minutes=10))
    assert "Executor Argo" in chan.sent[0][0] and "file" not in chan.sent[0][1]


def test_design_style_reads_claudes_answer(monkeypatch):
    import anthropic

    answer = {"bg_top": "#330000", "bg_bottom": "#110000", "accent": "#ffcc00", "text": "#ffffff",
              "subtext": "#ddbbbb", "font": "serif", "layout": "center", "border": "double",
              "pattern": "vignette", "uppercase": True, "tagline": "For the Legion"}
    seen = {}

    class FakeMessages:
        async def create(self, **kw):
            seen.update(kw)
            return types.SimpleNamespace(stop_reason="end_turn",
                                         content=[types.SimpleNamespace(type="text", text=json.dumps(answer))])

    class FakeClient:
        def __init__(self, *a, **kw):
            self.beta = types.SimpleNamespace(messages=FakeMessages())

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeClient)
    style = asyncio.run(cards.design_style("dark red and gold", b"\x89PNG", "image/png"))
    assert style == cards.CardStyle.from_dict(answer)
    content = seen["messages"][0]["content"]
    assert content[0]["type"] == "image" and "dark red and gold" in content[1]["text"]


def test_design_style_needs_input_and_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(cards.StyleError, match="Describe"):
        asyncio.run(cards.design_style(""))
    with pytest.raises(cards.StyleError, match="ANTHROPIC_API_KEY"):
        asyncio.run(cards.design_style("gold"))
    with pytest.raises(cards.StyleError, match="PNG"):
        asyncio.run(cards.design_style("", b"x", "application/pdf"))
