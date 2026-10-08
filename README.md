# Aion 2 Event Bot

A Discord bot that pings a role before the Aion 2 events and world bosses you pick. It uses the schedules from
[aion2hub's event timer](https://aion2hub.com/tools/event-timer) and [world boss timers](https://aion2hub.com/tools/world-bosses).
Only followed events and bosses are pinged.

Server admins pick:
- the **channel** for announcements
- the **role** to ping
- the **region** (Korea, Taiwan or Global) so times match their servers
- which **events and bosses** to follow
- how early to ping: a server default (**10 minutes** unless changed) and, if wanted, a different time per event or boss

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

## World bosses

| Boss | Where | Korea / Taiwan | Global |
|---|---|---|---|
| Watcher Kaira | Chaotic Lower Reshanta | every 4h from 01:00 | every 3h from 01:00 |
| Executor Argo, Kaira, Tamasa | Chaotic Lower Reshanta | Wed and Sat 22:30 | Mon, Thu and Sat 21:30 |
| Abyss Siege Boss | Abyss | Fri and Sun 22:00 | Fri and Sun 21:00 |
| Enraged Guardian Lord Nahma | Chaotic Middle Reshanta | Fri and Sun 22:00 | not in Global yet |
| Executioner Dramos, Ravager Marakha, Turncoat Ducal | Chaotic Middle Reshanta | Wed and Sat 22:30 | not in Global yet |

Bosses that spawn together are announced in one message. Zone field bosses (Altgard, Verteron, Eltnen, Morheim)
are left out, because aion2hub lists no spawn times for them.

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
| `/follow event` | Manage Server | Starts pinging an event or boss, or "All bosses", "All events" or "Everything" |
| `/unfollow event` | Manage Server | Stops pinging an event or boss |
| `/ping-time minutes` | Manage Server | Sets how many minutes before the start to ping, for the whole server (0 = at start) |
| `/ping-time minutes event` | Manage Server | Gives one event or boss (or "All bosses") its own ping time. Leave out `minutes` to go back to the server default |
| `/events` | Everyone | Shows settings, what's followed, and when each event or boss is next |
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
