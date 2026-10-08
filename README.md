# Aion 2 Event Bot

A Discord bot that pings a role in a channel before the Aion 2 events and world bosses you choose.

> @Raiders **Executor Argo**, **Executor Kaira** spawn in 10 minutes
> 🕒 21:30 your time · 22:30 server time (KST)
> 📍 Chaotic Lower Reshanta

- Pick the **channel**, the **role** to ping and your **region** (Korea, Taiwan or Global).
- Pick exactly which **events and bosses** to follow. Nothing else gets pinged.
- Pick **how early** to ping: a server default (10 minutes unless you change it), and if you like, a different time for each event or boss.
- Show times as **local time** (each reader's own timezone), **game server time**, or both.

Schedules come from aion2hub's [event timer](https://aion2hub.com/tools/event-timer) and [world boss timers](https://aion2hub.com/tools/world-bosses).

---

## Contents

1. [How the bot works](#how-the-bot-works)
2. [What it can ping](#what-it-can-ping)
3. [Setup, step 1: create the bot in Discord](#step-1-create-the-bot-in-discord)
4. [Setup, step 2: run the bot](#step-2-run-the-bot)
5. [Setup, step 3: configure it in your server](#step-3-configure-it-in-your-server)
6. [Hosting it 24/7](#hosting-it-247)
7. [Commands](#commands)
8. [Updating the schedule](#updating-the-schedule)
9. [Troubleshooting](#troubleshooting)
10. [Project layout and development](#project-layout-and-development)

---

## How the bot works

**Where the times come from.** aion2hub has no public API. Its timers are calculated in your browser from a fixed weekly schedule.
This bot keeps that same schedule as data in [`bot/schedule.py`](bot/schedule.py), so it never has to download anything from the site.
If aion2hub goes down, the bot keeps working.

**Regions and timezones.** Every event and boss is defined in *server time* for each region:

| Region | Server clock |
|---|---|
| Korea | GMT+9 |
| Taiwan | GMT+8 |
| Global (NA / EU / SA / JP) | GMT+9, as aion2hub lists it |

Some content exists only in some regions. For example, Abyss Rift Zone and the Middle Reshanta bosses are Korea and Taiwan only.
The bot converts server time to an exact moment, and Discord's `<t:…>` timestamps then show it in each reader's local time.

**The check loop.** Every 20 seconds the bot looks at each server that has run `/setup`:
1. For every followed event or boss, it finds any start time inside that event's ping window. The window is "now" to "now + ping time".
2. Events that start at the same moment are grouped into **one message**. For example, the three Executors produce one ping, not three.
3. It posts that message in the chosen channel and mentions the chosen role.
4. It records each event and start time it announced, so every occurrence is pinged **once**, even across restarts.

If the bot was offline and comes back inside a ping window, it still pings, just with less notice. If it was offline past an event's start, that event is skipped.

**Storage.** Settings live in a small SQLite file (`bot.db` by default). Each Discord server has its own settings, so one running bot can serve many servers.

---

## What it can ping

### Events

| Event | Korea / Taiwan | Global |
|---|---|---|
| Spacetime Rift | 02, 05, 08, 11, 14, 17, 20, 23:00 | 00, 03, 06, 09, 12, 15, 18, 21:00 |
| Beritra Air Raid | every hour at :30 | every hour at :30 |
| Abyss Rift Zone | Tue and Thu 22:00 | not in Global yet |
| Spacetime Rift Domination | Mon, Thu and Sat at 20:00 and 23:00 | not in Global yet |
| Daily Reset | 05:00 | 16:00 |
| Weekly Reset | Wed 05:00 | Wed 16:00 |

### World bosses

| Boss | Where | Korea / Taiwan | Global |
|---|---|---|---|
| Watcher Kaira | Chaotic Lower Reshanta | every 4h from 01:00 | every 3h from 01:00 |
| Executor Argo, Kaira, Tamasa | Chaotic Lower Reshanta | Wed and Sat 22:30 | Mon, Thu and Sat 21:30 |
| Abyss Siege Boss | Abyss | Fri and Sun 22:00 | Fri and Sun 21:00 |
| Enraged Guardian Lord Nahma | Chaotic Middle Reshanta | Fri and Sun 22:00 | not in Global yet |
| Executioner Dramos, Ravager Marakha, Turncoat Ducal | Chaotic Middle Reshanta | Wed and Sat 22:30 | not in Global yet |

All times are server time.

**What's not included, and why:**
- **Hourly minigames** (Nyerk Shooter, Mysterious Track, Hidden Lugi, Up! Up! Up!, Defend Shugo Merchants, Goldrin's Treasure): aion2hub doesn't say what minute of the hour they start.
- **Zone field bosses** (Altgard, Verteron, Eltnen, Morheim): aion2hub lists their locations but no spawn times.
- **Weekly Reset** time is an assumption. aion2hub gives only the day (Wednesday), so the bot uses the daily reset hour.

You can add or fix any of these in [`bot/schedule.py`](bot/schedule.py). See [Updating the schedule](#updating-the-schedule).

---

## Step 1: create the bot in Discord

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and click **New Application**. Give it a name, such as "Aion2 Timer".
2. Open the **Bot** tab and click **Reset Token**. Copy the token: this is your `DISCORD_TOKEN`.
   **Treat it like a password.** Never commit it or share it. If it leaks, click Reset Token again.
3. The bot needs no privileged intents, so leave those switches off.
4. Open **OAuth2 → URL Generator**:
   - Under **Scopes**, tick `bot` and `applications.commands`.
   - Under **Bot Permissions**, tick `View Channels`, `Send Messages` and `Mention @everyone, @here and All Roles`.
5. Copy the generated URL at the bottom, open it, choose your server and click **Authorize**.

The bot now appears in your member list, offline. It comes online once you run it in step 2.

> The "Mention All Roles" permission lets the bot ping a role even when the role isn't set as mentionable.
> If you'd rather not grant it, open the role's settings in Discord and turn on **Allow anyone to @mention this role**.

---

## Step 2: run the bot

You need **Python 3.10 or newer** and **git**.

```bash
git clone https://github.com/SarunasKir/aion2-event-bot.git
cd aion2-event-bot
python3 -m venv .venv
```

Activate the virtual environment:
- **Windows (PowerShell):** `.venv\Scripts\Activate.ps1`
- **macOS or Linux:** `source .venv/bin/activate`

Then install the dependencies and add your token:

```bash
pip install -r requirements.txt
cp .env.example .env      # on Windows: copy .env.example .env
```

Open `.env` and paste your token after `DISCORD_TOKEN=`. Then start the bot:

- **macOS or Linux:**
  ```bash
  export $(grep -v '^#' .env | xargs)
  python -m bot.main
  ```
- **Windows (PowerShell):**
  ```powershell
  Get-Content .env | Where-Object { $_ -and $_ -notmatch '^#' } | ForEach-Object { $k,$v = $_ -split '=',2; Set-Item "env:$k" $v }
  python -m bot.main
  ```

When you see `Logged in as …`, the bot is online. It pings only while this program keeps running. To run it around the clock, see [Hosting it 24/7](#hosting-it-247).

### Settings (`.env`)

| Variable | Required | Default | What it does |
|---|---|---|---|
| `DISCORD_TOKEN` | yes | none | The bot token from step 1 |
| `DATABASE_PATH` | no | `bot.db` | Where settings are stored |
| `DEFAULT_LEAD_MINUTES` | no | `10` | Ping time for servers that haven't set one |

---

## Step 3: configure it in your server

Slash commands can take a minute or two to appear the first time the bot starts.
Commands that change settings need the **Manage Server** permission.

1. **`/setup`**: choose the channel, the role to ping and your region. You can also set the ping time here.
   ```
   /setup channel:#boss-alerts role:@Raiders region:Global (NA / EU / SA / JP)
   ```
2. **`/follow`**: choose what to ping. Run it once for each event or boss, or use a shortcut:
   ```
   /follow event:Boss: Executor Argo
   /follow event:Spacetime Rift
   /follow event:All bosses
   ```
3. **`/ping-time`** (optional): change how early pings arrive.
   ```
   /ping-time minutes:15                                 ← whole server
   /ping-time minutes:30 event:Boss: Watcher Kaira       ← just this boss
   ```
4. **`/test-ping`**: posts a sample ping so you can check that the channel and role work.
5. **`/time-display`** (optional): show times as local time, server time, or both.
6. **`/events`**: shows your settings, what's followed (✅), and when each event or boss is next.

---

## Hosting it 24/7

The bot uses very little memory and CPU, so the smallest server any host offers is plenty.
Avoid hosts that **sleep when idle**, such as Render's free web services: a sleeping bot misses pings.
Prices and free tiers below are a guide, so check the current terms when you sign up.

| Option | Cost | Effort |
|---|---|---|
| Oracle Cloud "Always Free" virtual machine | free | medium |
| Google Cloud e2-micro (free tier, some US regions) | free | medium |
| A PC or Raspberry Pi that is always on | electricity | low |
| Hetzner Cloud small server | about €4 a month | medium |
| Railway | about $5 a month | lowest |

### Linux server (Oracle, Google Cloud, Hetzner or a Raspberry Pi)

1. Create an Ubuntu server and connect to it with SSH.
2. Install Python and git, then set up the bot:
   ```bash
   sudo apt update && sudo apt install -y python3-venv git
   git clone https://github.com/SarunasKir/aion2-event-bot.git
   cd aion2-event-bot
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   cp .env.example .env && nano .env      # paste your token, then press Ctrl+O, Enter and Ctrl+X
   ```
3. Install it as a service, so it starts on boot and restarts after crashes.
   [`deploy/aion2-bot.service`](deploy/aion2-bot.service) assumes the user `ubuntu` and the folder `/home/ubuntu/aion2-event-bot`. Edit it first if yours differ.
   ```bash
   sudo cp deploy/aion2-bot.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now aion2-bot
   ```
4. Use these commands to manage it:
   ```bash
   sudo systemctl status aion2-bot      # is it running?
   journalctl -u aion2-bot -f           # live logs
   sudo systemctl restart aion2-bot     # after changing .env
   ```

The bot only makes outgoing connections to Discord, so you don't need to open any firewall ports.

### Docker

```bash
docker build -t aion2-event-bot .
docker run -d --name aion2-bot --restart unless-stopped \
  -e DISCORD_TOKEN=your-token \
  -v aion2-bot-data:/data \
  aion2-event-bot
```

Settings live in the `aion2-bot-data` volume, so they survive rebuilds.

### Railway

1. Create a new project from your GitHub repo.
2. Add a variable `DISCORD_TOKEN`.
3. Add a volume mounted at `/data` and set `DATABASE_PATH=/data/bot.db`, so settings survive redeploys.

Railway detects the Dockerfile and runs the bot. It doesn't need a public domain or port.

### Updating a running bot

```bash
cd aion2-event-bot && git pull
sudo systemctl restart aion2-bot     # or: docker build … and re-run, or redeploy on Railway
```

Your settings are kept, because they live in the database, not in the code.

---

## Commands

| Command | Who | What it does |
|---|---|---|
| `/setup channel role [region] [lead_minutes]` | Manage Server | Sets where to ping, which role to ping, the region, and optionally the ping time |
| `/follow event` | Manage Server | Starts pinging an event or boss. Shortcuts: "All bosses", "All events", "Everything" |
| `/unfollow event` | Manage Server | Stops pinging an event or boss (the same shortcuts work) |
| `/ping-time minutes` | Manage Server | Sets the server-wide ping time, 0 to 120 minutes (0 = at start) |
| `/ping-time minutes event` | Manage Server | Gives one event or boss, or a shortcut group, its own ping time |
| `/ping-time event` | Manage Server | With no minutes, puts that event back on the server-wide time |
| `/time-display mode` | Manage Server | Shows times in pings as **local time** (each reader's own timezone), **server time** (the game clock), or **both** (default) |
| `/events` | Everyone | Shows settings, what's followed, and when each event or boss is next |
| `/test-ping` | Manage Server | Posts a sample ping in the configured channel |

Command replies are only visible to you. Only the real pings appear publicly.

---

## Updating the schedule

Everything the bot knows about times is in [`bot/schedule.py`](bot/schedule.py). Each entry gives:
- **key**: an internal id. Don't rename keys that servers already follow.
- **name**: what Discord shows.
- **description**: a short line under the ping (for bosses, the location).
- **rules**: per region, which **days** and **times** (server time) it starts.

Example: adding a minigame that starts at minute 15 of every hour in all regions:

```python
Event(
    key="nyerk_shooter",
    name="Nyerk Shooter",
    description="Hourly minigame.",
    rules={r: (Rule(_hours(*range(24), minute=15)),) for r in REGIONS},
),
```

Example: adding a boss on Tuesdays and Fridays at 21:00, in Korea and Taiwan only:

```python
_boss(
    "new_boss", "New Boss", "Some Zone",
    **_same(Rule(_hours(21), frozenset({TUE, FRI}))),
),
```

After editing, run the tests and restart the bot. New entries appear in `/follow` automatically.
Discord allows at most 25 choices per option. There are 18 now: 15 events and bosses, plus 3 shortcuts.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Slash commands don't show up | Wait a minute, then restart Discord (Ctrl+R). Check that the invite URL included `applications.commands`. |
| The bot is offline | The program isn't running. Check the terminal, or `systemctl status aion2-bot` and `journalctl -u aion2-bot`. |
| "Set DISCORD_TOKEN" on start | `.env` wasn't loaded, or the token line is empty. |
| A ping posts but nobody is notified | The role isn't mentionable and the bot lacks "Mention All Roles". `/setup` warns about this. |
| No pings at all | Run `/events`: check that the channel is set, items have ✅, and they show a "next" time. "Not in this region" means that content doesn't exist on your server's region. |
| Pings arrive at the wrong time | Check the region in `/setup`. Korea and Taiwan are an hour apart. |
| The bot can't post in the channel | Give the bot View Channel and Send Messages in that channel's permissions. |

---

## Project layout and development

```
bot/
  main.py        Discord client, slash commands and the 20-second check loop
  announcer.py   Decides what is due now, and formats the ping message
  schedule.py    Event and boss schedule data, plus time calculations
  storage.py     SQLite: server settings, followed items, ping times, sent pings
tests/           pytest tests for the schedule, ping windows and storage
deploy/          systemd service file
Dockerfile       Container image
```

Run the tests:

```bash
pip install pytest
python -m pytest
```
