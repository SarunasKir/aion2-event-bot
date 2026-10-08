# Aion 2 Event Bot

A Discord bot that pings a role before Aion 2 events start. It uses the schedule from
[aion2hub.com/tools/event-timer](https://aion2hub.com/tools/event-timer).

Server admins pick:
- the **channel** for announcements
- the **role** to ping
- the **region** (Korea, Taiwan or Global) so times match their servers
- which **events** to follow
- how early to ping (default **10 minutes**; 0 pings at start)

Example announcement:

> @Raiders **Spacetime Rift** starts in 10 minutes (14:00).

## Events

| Event | Korea / Taiwan | Global |
|---|---|---|
| Spacetime Rift | 02, 05, 08, 11, 14, 17, 20, 23:00 | 00, 03, 06 … 21:00 (GMT+9) |
| Beritra Air Raid | every hour at :30 | every hour at :30 |
| Abyss Rift Zone | Tue and Thu 22:00 | not in Global yet |
| Spacetime Rift Domination | Mon, Thu and Sat at 20:00 and 23:00 | not in Global yet |
| Daily Reset | 05:00 | 16:00 |
| Weekly Reset | Wed 05:00 | Wed 16:00 |

All times are server time: Korea is GMT+9, Taiwan is GMT+8, and Global is GMT+9 (as aion2hub lists it).
Discord shows each ping time in every reader's own timezone.

aion2hub has no public API. Its timer is worked out in the browser from a fixed schedule,
so this bot keeps the same schedule in [`bot/schedule.py`](bot/schedule.py). When the game changes
its times, edit that file. The hourly minigames (Nyerk Shooter, Mysterious Track, Hidden Lugi and others)
aren't included, because aion2hub doesn't publish their start minutes. You can add them there in the same format.

## Commands

| Command | Who | What it does |
|---|---|---|
| `/setup channel role [region] [lead_minutes]` | Manage Server | Sets where and whom to ping |
| `/follow event` | Manage Server | Starts announcing an event, or "All events" |
| `/unfollow event` | Manage Server | Stops announcing an event |
| `/events` | Everyone | Shows settings and when each event is next |
| `/test-ping` | Manage Server | Posts a sample ping in the configured channel |

## Setup

### 1. Create the bot in Discord
1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and select **New Application**.
2. On the **Bot** tab, select **Reset Token** and copy the token. This is your `DISCORD_TOKEN`. Keep it secret.
3. Under **OAuth2 → URL Generator**, tick the **bot** and **applications.commands** scopes.
   Then tick the **View Channels**, **Send Messages** and **Mention @everyone, @here and All Roles** permissions.
4. Open the generated URL and invite the bot to your server.

The bot needs no privileged intents.

### 2. Run it

```bash
git clone <this repo> && cd aion2-event-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # paste your token into .env
export $(grep -v '^#' .env | xargs)
python -m bot.main
```

You need Python 3.10 or newer.

In Discord, run `/setup`, then `/follow`, then `/test-ping`. Slash commands can take a minute to appear the first time.

### 3. Host it 24/7

The bot only pings while it's running, so it needs a machine that stays on.

**Docker (any VPS, a home server or a Raspberry Pi):**
```bash
docker build -t aion2-event-bot .
docker run -d --name aion2-bot --restart unless-stopped \
  -e DISCORD_TOKEN=your-token -v aion2-bot-data:/data aion2-event-bot
```

**Railway or Render:** create a project from this GitHub repo and set `DISCORD_TOKEN` as a variable.
Use start command `python -m bot.main`. Run it as a *worker* or *background service*, not a web service, since it serves no HTTP.
Mount a volume and set `DATABASE_PATH` to a file on it, so settings survive redeploys.

**Linux with systemd:** run it as a service with `ExecStart=/path/.venv/bin/python -m bot.main`,
`WorkingDirectory=` set to the repo, and `EnvironmentFile=` pointing to your `.env`.

Settings are stored in a small SQLite file (`DATABASE_PATH`, default `bot.db`).

## Development

```bash
pip install pytest
python -m pytest
```
