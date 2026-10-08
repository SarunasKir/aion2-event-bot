# Aion 2 Event Bot

A Discord bot that pings a role in a channel before the Aion 2 events and world bosses you choose,
and before field bosses you add from a game screenshot.

> @Raiders **Executor Argo**, **Executor Kaira** spawn in 10 minutes
> 🕒 21:30 your time · 22:30 server time (KST)
> 📍 Chaotic Lower Reshanta

- Pick the **channel**, the **role** to ping and your **region** (Korea, Taiwan or Global).
- Pick exactly which **events and bosses** to follow. Nothing else gets pinged.
- Pick **how early** to ping: a server default (10 minutes unless you change it), and if you like, a different time for each event or boss.
- Show times as **local time** (each reader's own timezone), **game server time**, or both.
- **Field bosses:** post a screenshot of the in-game respawn timers and the bot reads them and pings before each spawn.
  A ✅ **Killed** button on the ping starts the next respawn timer.
- **Separate channels and roles** for events, world bosses and field bosses, plus a **Notify me** button menu so players pick their own pings.
- Optional **quiet hours**, a **second ping at start**, a **daily summary** after reset, and **auto-deleting** old pings.

Schedules come from aion2hub's [event timer](https://aion2hub.com/tools/event-timer) and [world boss timers](https://aion2hub.com/tools/world-bosses).

---

## Contents

1. [How the bot works](#how-the-bot-works)
2. [What it can ping](#what-it-can-ping)
3. [Setup, step 1: create the bot in Discord](#step-1-create-the-bot-in-discord)
4. [Setup, step 2: run the bot](#step-2-run-the-bot)
5. [Setup, step 3: configure it in your server](#step-3-configure-it-in-your-server)
6. [Hosting it 24/7](#hosting-it-247)
7. [Field boss timers from screenshots](#field-boss-timers-from-screenshots)
8. [Server options](#server-options)
9. [Commands](#commands)
10. [Updating the schedule](#updating-the-schedule)
11. [Troubleshooting](#troubleshooting)
12. [Project layout and development](#project-layout-and-development)

---

## How the bot works

**Where the times come from.** aion2hub datamines the times from the game client and builds them into its pages as a fixed weekly schedule.
It has no public API. This bot ships with the same schedule as data in [`bot/schedule.py`](bot/schedule.py).

**Daily schedule check.** Once a day, and when it starts, the bot reads aion2hub's
[world boss](https://aion2hub.com/tools/world-bosses) and [event timer](https://aion2hub.com/tools/event-timer) pages.
It looks for phrases like "Wed & Sat · 22:30 server" or "Every 4h from 01:00 server" next to each event or boss name.
If a time differs from what the bot has, the bot:
1. switches to the new time straight away,
2. saves it in the database, so it survives restarts, and
3. posts a short notice in each server's announcement channel, without pinging anyone. For example:
   "📅 aion2hub updated its schedule: **Turncoat Ducal** (Korea): Wed & Sat 22:30 → Tue & Fri 21:00".

Safety rules:
- Only times for events and bosses the bot already knows are updated. New bosses still need adding to `schedule.py` by hand.
- If the site is down, or its layout changed so that fewer than 5 boss times can be read, nothing changes and the bot keeps its current times.
- Bosses and events change together only when each one is read clearly. Anything unreadable keeps its current time.

Admins can run `/schedule-check` to check right away and see what changed. To turn the daily check off, set `SCHEDULE_AUTO_UPDATE=0`.
If aion2hub goes away entirely, the bot keeps working with the last times it knew.

**Regions and timezones.** Every event and boss is defined in *server time* for each region:

| Region | Server clock |
|---|---|
| Korea | GMT+9 |
| Taiwan | GMT+8 |
| Global (NA / EU / SA / JP) | GMT+9, as aion2hub lists it |

Some content exists only in some regions. For example, Abyss Rift Zone and the Middle Reshanta bosses are Korea and Taiwan only.
The bot converts server time to an exact moment, and Discord's `<t:…>` timestamps then show it in each reader's local time.

**The check loop.** Every 20 seconds the bot looks at each server that has run `/setup`:
1. For every followed event or boss, and every field boss timer, it finds any start time inside the ping window. The window is "now" to "now + ping time".
2. Anything starting during **quiet hours** is skipped.
3. Things of the same kind that start at the same moment are grouped into **one message**. For example, the three Executors produce one ping, not three.
4. It posts each message in the channel for that kind (events, world bosses or field bosses) and mentions that kind's role.
   Without `/route`, everything goes to the `/setup` channel and role.
5. It records each ping it posted, so every occurrence is pinged **once**, even across restarts.

**When something goes wrong:**
- If Discord has a hiccup and a ping fails to post, the bot tries again on the next check instead of dropping it.
- If the channel was deleted, or the bot isn't allowed to post there, the bot tells the server's admins once, in the server's
  system channel or by DM to the owner. It warns again only if the problem comes back after being fixed.
- If the bot was offline and comes back inside a ping window, it still pings, just with less notice. If it was offline past an event's start, that event is skipped.
- If the check loop ever freezes for 5 minutes, the bot exits on purpose, so Docker or systemd restarts it fresh.

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
- **Zone field bosses** (Altgard, Verteron, Eltnen, Morheim) have no fixed schedule, so they aren't on this list.
  Add their timers from a screenshot instead: see [Field boss timers from screenshots](#field-boss-timers-from-screenshots).
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
     Also tick `Manage Roles` if you'll use the `/role-menu` buttons.
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
| `SCHEDULE_AUTO_UPDATE` | no | `1` | Set to `0` to turn off the daily aion2hub schedule check |
| `ANTHROPIC_API_KEY` | for screenshots | none | Lets `/fieldboss screenshot` read images. Without it, use `/fieldboss add` |
| `FIELD_BOSS_MODEL` | no | `claude-opus-5-5` | Which Claude model reads screenshots |
| `HEALTH_TIMEOUT` | no | `300` | Seconds without a check before the bot restarts itself |
| `HEALTH_FILE` | no | `/tmp/aion2bot.heartbeat` | Heartbeat file read by `python -m bot.health` (the Docker health check) |

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
7. Optional extras are described in [Server options](#server-options): separate channels and roles, Notify-me buttons,
   quiet hours, start pings, a daily summary and auto-delete.

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

## Field boss timers from screenshots

Field bosses respawn on their own timers, not on a weekly schedule. The game shows a countdown,
so the bot takes that from a screenshot and pings before each spawn.

### How it works

1. An admin runs `/fieldboss screenshot` and attaches a screenshot showing field boss respawn timers.
2. The bot sends the image to Claude, Anthropic's AI model, which reads each boss name and its countdown or spawn time.
3. Countdowns count from the moment the screenshot was posted. A time of day such as "21:30" is read as **server time**
   for your region, and if that time has already passed today, it's taken as tomorrow.
4. The bot posts what it read with **Save timers** and **Discard** buttons. Nothing is saved until the person who posted it,
   or an admin, taps Save. Unconfirmed results are dropped after 10 minutes.
   A new screenshot or `/fieldboss add` for the same boss replaces its old timer.
5. It pings the role before each spawn, using the server's ping time. You can give field bosses their own ping time with
   `/ping-time minutes:5 event:Field bosses (timers you add)`.
6. Each field boss ping has a ✅ **Killed** button. Tap it after the kill to start the next respawn timer straight away.
   The first time, the bot asks how long that boss takes to respawn (like `2h`), then remembers it.
   You can also set it up front: `/fieldboss add boss:Silent Dartan spawns_in:45m respawn:2h`.
7. Timers are cleared an hour after their spawn time if nobody taps Killed.

> @Raiders **Silent Dartan** spawns in 10 minutes
> 🕒 21:30 your time · 22:30 server time (KST)
> 📍 Altgard · field boss

Tips for good results:
- Crop the screenshot to the timer list. Smaller, sharper images read better.
- Check the list the bot posts. If a boss or time is wrong, fix it with `/fieldboss add` or drop it with `/fieldboss remove`.
- All current timers are listed at the bottom of `/events`.

### Setting up screenshot reading

1. Create an API key in the [Claude Console](https://platform.claude.com/) (Settings → API keys) and add some credit.
2. Add it to `.env` as `ANTHROPIC_API_KEY=...` and restart the bot.

Each screenshot is one request to Claude. With the default model, a typical screenshot costs around a cent or less.
To keep costs predictable, the `/fieldboss` commands are limited to **Manage Server** by default.
To let a raid-leader role use them, go to **Server Settings → Integrations → your bot → /fieldboss** and add that role.

Without an API key, everything else still works. `/fieldboss screenshot` just says it isn't set up,
and you can type timers in with `/fieldboss add`.

### Typing timers in by hand

`/fieldboss add boss:<name> spawns_in:<time> [zone:<zone>]`, where the time is any of:

| You type | Meaning |
|---|---|
| `1h 20m`, `45m`, `2h`, `90s` | Time left until the spawn |
| `1:23:45` | Time left, as hours:minutes:seconds |
| `at 21:30` | Spawn at 21:30 server time (the next 21:30 coming up) |

The boss name suggests the known field bosses as you type, but any name works.

---

## Server options

All of these are optional and per server. `/events` shows which are on.

**Separate channels and roles: `/route`.** Send each kind of ping to its own channel and role, for example:
```
/route category:World bosses channel:#boss-alerts role:@Bosses
/route category:Events role:@PvP              ← same channel as /setup, different role
/route category:Field bosses                  ← back to the /setup channel and role
```

**Notify-me buttons: `/role-menu`.** Posts a message with one button per ping role (the `/setup` role and any `/route` roles).
Players tap a button to add or remove that role themselves. The bot needs **Manage Roles**, and its own role must sit
above those roles in Server Settings → Roles. The buttons keep working after restarts.

**Quiet hours: `/quiet-hours start:02:00 end:08:00`.** Nothing that *starts* between those times (server time) gets pinged.
The range can cross midnight. Run `/quiet-hours` with no times to turn it off.

**Ping at start too: `/start-ping enabled:True`.** Adds a short "🔔 … starting now!" ping when each event or boss starts,
on top of the early warning.

**Daily summary: `/digest enabled:True`.** Right after each daily reset, posts the next 24 hours of followed events,
bosses and field boss timers in the `/setup` channel, without pinging anyone.

**Auto-delete: `/auto-delete minutes:30`.** Deletes each ping (and start ping) that many minutes after the event starts,
to keep the channel tidy. Run `/auto-delete` with no minutes to keep pings.

**More detail in pings.** Pings for events with a known length show when they end (Abyss Rift Zone, Rift Domination),
and every ping links to the matching aion2hub page.

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
| `/schedule-check` | Manage Server | Checks aion2hub now and applies any changed times |
| `/fieldboss screenshot image` | Manage Server* | Reads field boss timers from a screenshot and pings before each spawn |
| `/fieldboss add boss spawns_in [zone] [respawn]` | Manage Server* | Adds or replaces one field boss timer by hand, optionally with its respawn time |
| `/fieldboss remove boss` | Manage Server* | Removes a field boss timer |
| `/time-display mode` | Manage Server | Shows times in pings as **local time** (each reader's own timezone), **server time** (the game clock), or **both** (default) |
| `/route category [channel] [role]` | Manage Server | Sends events, world bosses or field bosses to their own channel and role |
| `/role-menu` | Manage Server | Posts buttons so members can turn ping roles on or off |
| `/quiet-hours [start] [end]` | Manage Server | No pings for anything starting between two server times |
| `/start-ping enabled` | Manage Server | Also ping when things start |
| `/digest enabled` | Manage Server | Daily summary after the reset |
| `/auto-delete [minutes]` | Manage Server | Deletes pings that many minutes after the start |
| `/events` | Everyone | Shows settings, what's followed, and when each event or boss is next |
| `/test-ping` | Manage Server | Posts a sample ping in the configured channel |

\* Server admins can open these to another role under Server Settings → Integrations.

Most command replies are only visible to you. Field boss replies are public, so everyone can see the timers.

---

## Updating the schedule

The daily check keeps known times up to date by itself (see [How the bot works](#how-the-bot-works)).
To add something new, or to fix a time the check can't read, edit [`bot/schedule.py`](bot/schedule.py). Each entry gives:
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
| `/schedule-check` says it couldn't read the boss page | aion2hub changed its page layout. Times stay as they were. Update `bot/watcher.py` or edit `bot/schedule.py` by hand. |
| `/fieldboss screenshot` says it isn't set up | Add `ANTHROPIC_API_KEY` to `.env` and restart the bot. |
| A field boss time is off | Check the bot's list after posting. Countdowns start when the screenshot is posted, so post it right after taking it. Fix one with `/fieldboss add`. |
| Notify-me buttons say "I can't change that role" | Give the bot **Manage Roles**, and drag its role above the ping roles in Server Settings → Roles. |
| The bot sent me a DM about a channel | It couldn't post pings there. Fix the channel or its permissions; pings resume on their own. |
| Pings arrive at the wrong time | Check the region in `/setup`. Korea and Taiwan are an hour apart. |
| The bot can't post in the channel | Give the bot View Channel and Send Messages in that channel's permissions. |

---

## Project layout and development

```
bot/
  main.py        Discord client, slash commands and the 20-second check loop
  announcer.py   Decides what is due now, and formats the ping message
  schedule.py    Event and boss schedule data, plus time calculations
  storage.py     SQLite: server settings, routes, followed items, ping times, sent pings, schedule updates, field timers
  views.py       Buttons and forms: Notify-me roles, field boss Killed, screenshot Save/Discard
  health.py      Watchdog that restarts a frozen bot, and the Docker health check
  watcher.py     Daily aion2hub check: reads the pages, finds changed times, applies them
  fieldboss.py   Field boss timers: reads screenshots with Claude, and parses typed-in times
tests/           pytest tests for the schedule, pings, delivery, options, aion2hub check and field bosses
deploy/          systemd service file
Dockerfile       Container image
```

Run the tests:

```bash
pip install pytest
python -m pytest
```
