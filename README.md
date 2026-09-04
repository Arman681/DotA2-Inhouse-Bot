# FeederBot

FeederBot is a Python Discord bot for running **Dota 2 inhouse lobbies** inside a Discord server. It handles lobby management, Steam account linking, MMR fetching, team generation, Immortal Draft flows, live match polling, betting, Feederbucks, and per-server configuration.

This README reflects the current project structure and behavior in the codebase, including the split modules under `bot/main.py`, `bot/commands/`, `bot/services/`, and `bot/storage/`.

---

## What FeederBot does

FeederBot is built around a per-server inhouse workflow:

1. Players link their Steam account with `!cfg`.
2. Admins create a lobby with `!lobby`.
3. Players join/leave using reactions, or admins manage the lobby manually.
4. At 10/10, the lobby can be rolled with 🚀.
5. In **regular mode**, FeederBot generates MMR-balanced teams.
6. In **immortal mode**, FeederBot generates captains and runs an interactive Immortal Draft.
7. Once the match appears on Steam, FeederBot polls the live match.
8. After the match ends, it resolves bets, adjusts inhouse MMR, awards Feederbucks, and posts a result summary.

---

## Inviting FeederBot

Want to run automated Dota 2 inhouse lobbies in your server?

**[Invite FeederBot](https://discord.com/oauth2/authorize?client_id=1386529622958805013&permissions=272723008&integration_type=0&scope=bot+applications.commands)**


---

## Core features

### Lobby system
- Per-guild lobby state stored in Firestore and restored on bot restart
- Lobby embed with current players, MMR, mode, balancing-toggle states, and password
- Player join/leave through reactions:
  - 👍 join
  - 👎 leave
  - 🚀 generate teams / captains and begin match wait flow
  - ♻️ reroll available team or captain setups
  - ⚔️ start Immortal Draft
  - 🎯 manual captain selection in Immortal mode
- Optional dedicated lobby channel via `!setlobbychannel`
- Supports **real Discord users** and **placeholder players** with custom MMR values

### Inhouse RSVP events
- Admins can manually post an all-ranks event with `!startrsvp <time> <games> [optional notes]`
- Live RSVP and fill lists update directly on the event embed
- RSVP rows show each player's stored public MMR and their recorded inhouse win rate when match-ledger history is available
- Players use persistent **RSVP**, **Fill**, and **Withdraw** buttons
- RSVP is capped at 10 committed players; fills remain available as standbys
- Players must have a linked Steam ID and usable MMR from `!cfg` before joining either list
- One hour before start, FeederBot confirms events with at least 10 combined RSVPs/fills or cancels events below 10
- FeederBot calls off undersubscribed events at that deadline so players are not left waiting and their time is respected
- Fills are promoted in signup order when the event is confirmed or a confirmed player later withdraws
- Direct RSVPs are locked after confirmation; fills, including promoted fills, may still withdraw
- Five minutes before the scheduled start time, a confirmed 10-player event automatically replaces the old idle lobby, loads the latest saved regular/immortal mode, and enters the same post-🚀 team/captain flow as an impromptu lobby
- Automatically opened RSVP lobbies keep their roster locked: 👍/👎 are omitted and ignored, while post-rocket reroll/draft controls remain available
- The event's `<games>` value drives a real series: after each unique result is processed, FeederBot records that game, prepares fresh regular teams or Immortal captains for the same locked roster, and starts another 15-minute Steam wait until all scheduled games are complete
- Completed match IDs are stored with the event, so automatic polling, `!submitmatch`, retries, and restart recovery cannot count the same game twice
- If a between-game Steam wait expires, that game remains pending and an Inhouse Admin can press 🚀 on the locked lobby to retry; a missing completed-match result pauses the series for `!submitmatch` instead of skipping a game
- Running series progress and its current waiting/live/paused/completed state are shown on the RSVP card
- An active inhouse match is never overwritten; the RSVP card and Inhouse Admin warning explain when automatic lobby opening is blocked
- Admins can use `!removersvp @user` for emergency confirmed-roster changes
- Confirmed-roster withdrawals without an available fill trigger an `Inhouse Admin` warning
- Active, confirmed, temporarily closed, and running series events are restored when the bot restarts
- FeederBot must be online for button clicks and on-time deadline/start actions; overdue events finalize and confirmed lobbies open when it next starts
- A second `!startrsvp` is rejected while an RSVP event is running, including while its signups are temporarily closed
- `!closersvp` temporarily locks the buttons while preserving the scheduled one-hour go/no-go decision
- `!cancelrsvp [reason]` can call off an active, temporarily closed, or confirmed event
- `!resetrsvp confirm` clears the roster and replaces the old card with a fresh unlocked one when the deadline is still ahead; after the deadline, it retires the old event and requires a new `!startrsvp`

### Two inhouse modes
#### Regular mode
- Generates 5v5 team combinations from the 10-player lobby
- Filters combinations by average-MMR difference threshold
- Scores valid combinations using:
  - MMR parity
  - optional preferred-role fit
- Stores the best rerolls for reuse instead of recomputing every time

#### Immortal mode
- Generates eligible captain pairs from non-placeholder players only
- Supports captain selection policies:
  - `min_diff`
  - `top2_if_close [threshold]`
  - `simulate`
- Supports manual captain selection by admins
- Runs an interactive **Immortal Draft** with:
  - 1–2–2–2–1 pick order
  - 5-second turn timer
  - 60-second per-captain reserve time bank
  - automatic lowest-MMR autopick on timeout
  - cancel button for captains/admins
- Posts final draft results with team MMR totals and average MMR

### Steam / STRATZ / OpenDota integration
- Links Discord users to Steam IDs with `!cfg`
- Fetches estimated MMR from **STRATZ** using `seasonRank`
- Falls back to **OpenDota** when needed
- Tracks live matches with the Steam API:
  - bound league matches
  - random public live matches
- Fetches completed match results from STRATZ, with OpenDota fallback
- Resolves hero names through `hero_id_map.json` / Steam hero API cache

### Inhouse MMR system
- Separate **inhouse MMR** from public ranked MMR
- Manual adjustment via match result processing
- Public MMR overrides via `!setmmr` (subject to later automatic ranked-MMR refreshes)
- Per-server private matchmaking MMR caps via `!deflate`, without changing public MMR displays
- Leaderboard command for top inhouse players
- Double-down token support for doubling inhouse MMR gain/loss during active inhouse matches

### Feederbucks, betting, and store
- Per-guild wallet system
- Default wallet seeding for users
- Betting on Radiant/Dire during active matches
- Bet updates allowed only as same-team increases
- Store system with purchasable `dd_tokens`
- Feederbucks transfer command between users
- Participation Feederbucks rewards after match completion

### Per-server configuration
Each Discord server can have its own:
- command prefix
- inhouse mode
- lobby password
- bound Steam league ID
- live-match channel
- lobby channel
- preferred-role integration setting
- captain selection policy
- separated player pairs
- private matchmaking MMR caps

### Persistence and restart recovery
- Firestore-backed persistence for:
  - linked players
  - guild settings
  - lobby players
  - lobby message IDs
  - inhouse MMR
  - wallets
  - bets
  - dd tokens / active double-downs
- Lobby players and lobby message are restored on startup
- Live channel and lobby channel IDs are restored on startup
- Cached guild prefixes are stored in memory after load

---

## Project structure

```text
FeederBot/
├── bot/main.py          # Main bot entry point, events, lobby flow, team balancing, polling
├── commands.py           # All text-command registration and handlers
├── immortal_draft.py     # Immortal Draft session, buttons, timer, autopick, cancel flow
├── match_tracker.py      # Completed match result lookup (STRATZ + OpenDota fallback)
├── mmr_manager.py        # Inhouse MMR storage, adjustment, leaderboard helpers
├── betting_manager.py    # Feederbucks, bets, dd token, and wallet helpers
├── firebase_setup.py     # Firebase Admin / Firestore initialization
├── hero_id_map.json      # Cached hero ID -> hero name mapping
├── requirements.txt      # Python dependencies
├── Procfile              # Heroku worker entry
└── README.md
```

---

## Commands

Below is the current command set reflected in `commands.py`.

### General commands
- `!cfg <steam_id> [@user] [--force]` — link Steam ID and fetch estimated MMR
- `!mmr [@user]` — show stored public MMR
- `!inhouse_mmr [@user]` — show inhouse MMR
- `!leaderboard` — show top inhouse players in the server
- `!balance [@user]` — show wallet balance
- `!bet [amount] [radiant|dire]` — place or increase a bet on the active match
- `!store` — show store items
- `!buy 1 <amount>` — buy double-down tokens
- `!dd_tokens [@user]` — show double-down token balance
- `!dd` — activate double-down for the current inhouse match before 2:00
- `!send <amount> <@user>` — send Feederbucks to another user
- `!setpreferredroles <1 2 3 4 5> [@user]` — set preferred roles in ranked order
- `!viewpreferredroles [@user]` — view preferred roles
- `!livematch` — repost/refresh the live match embed
- `!help` / `!help admin` — show command help

### Admin / Inhouse Admin commands
- `!add <@user|discord_id> ...` — add one or more users to the lobby
- `!add <placeholder_name> <mmr>` — add a placeholder player
- `!remove <@user|discord_id> ...` — remove one or more users from the lobby
- `!remove <placeholder_name>` — remove a placeholder
- `!replace <@user|discord_id|placeholder> <@user|discord_id|placeholder> [mmr]` — replace a lobby player; a new placeholder requires MMR
- `!lobby [regular|immortal]` — create or refresh the lobby and optionally set mode
- `!reset` — clear the lobby and create a fresh lobby embed
- `!startrsvp <time> <games> [optional notes]` — post one all-ranks RSVP event more than one hour before start; running events block duplicates
- `!closersvp` — temporarily lock signups while keeping the scheduled one-hour decision
- `!finalizersvp` — immediately run the 10-player go/no-go decision
- `!cancelrsvp [reason]` — manually call off an active, temporarily closed, or confirmed event and notify its players
- `!removersvp <@user>` — admin-remove a signup and promote the next fill when needed
- `!resetrsvp confirm` — clear the roster and replace the RSVP card before its deadline, or retire it after the deadline
- `!setmmr <mmr> <@user>` or `!setmmr <@user> <mmr>` — manually set a user’s public/stored MMR; automatic refreshes may replace it
- `!deflate <@user|discord_id> <mmr>` — cap the MMR used privately for matchmaking while preserving the public value
- `!undeflate <@user|discord_id>` — remove a private matchmaking MMR cap
- `!deflated [--verbose]` — list capped players by Discord ID; verbose mode includes names, public/capped/effective values, and audit details
- `!separate <@user|discord_id> <@user|discord_id>` — prevent two players from being placed on the same regular-inhouse team
- `!unseparate <@user|discord_id> <@user|discord_id>` — remove a separated-player pair
- `!separated [--verbose]` — list separated pairs by Discord ID; verbose mode includes names
- `!alert` — ping all 10 players when lobby is full
- `!setpassword <new_password>` — change the inhouse password shown on embeds
- `!changeprefix <new_prefix>` — set a per-server prefix
- `!viewlogs [--verbose]` — show guild configuration metadata from Firestore
- `!bindleague <league_id>` — bind a league ID for live inhouse match tracking
- `!setlivechannel` — set the channel for live-match updates
- `!setlobbychannel` — set the channel for lobby embeds/resets
- `!startpolling [match_id|next]` — poll the bound league normally, target an exact live match ID, or select the newest broadcast after the tracked match
- `!stoppolling` — stop polling and clear active match state
- `!randompoll` — poll a random public live match
- `!submitmatch <match_id>` — manually resolve match result, MMR, and bets
- `!toggle_roles <on|off>` — enable/disable preferred-role balancing
- `!toggle_stddev <on|off>` — rank valid regular teams using average-MMR difference plus `0.6 ×` the difference between team MMR standard deviations
- `!debug <on|off>` — make 🚀 generate teams without starting the Steam live-match search
- `!lobbyroles` — show preferred roles for the current 10-player lobby
- `!captainpolicy <policy> [threshold]` — show or set captain selection policy

### Global admin command
- `!pose @user <command>` — run a command as another user for testing/admin purposes

---

## Reactions used on the lobby embed

When a lobby embed is active, FeederBot uses reactions as part of the workflow:

- `👍` — join lobby
- `👎` — leave lobby
- `🚀` — lock in 10/10 lobby and generate teams/captains
- `♻️` — reroll generated teams or captain pair
- `⚔️` — start the Immortal Draft after captains are chosen
- `🎯` — open manual captain selection in Immortal mode

FeederBot resets the post-rocket state if the lobby changes after teams/captains were generated.

For a lobby automatically created from a confirmed RSVP, the signup roster is locked to that lobby message. Players cannot manually join or leave with 👍/👎; an Inhouse Admin can still use the normal add/remove/replace tools for an emergency. Running `!lobby` or `!reset` intentionally retires any unfinished RSVP series, creates a new ordinary impromptu lobby, and clears that lock.

---

## Data model overview

Firestore collections/documents used by the project currently include:

### `players`
Stores per-user linked data such as:
- `steam_id`
- `steam_name`
- `discord_username`
- `discord_nickname`
- `mmr`
- `seasonRank`
- `mmrSource`
- `mmrUpdatedAt`
- `steamLinkedAt`
- `preferred_roles`

### `guild_specific_info/{guild_id}`
Stores per-server settings such as:
- `prefix`
- `password`
- `inhouse_mode`
- `league_id`
- `lobby_message_id`
- `lobby_players`
- `lobby_roster_lock` (ties a confirmed RSVP roster lock to one lobby message)
- `preferred_roles_setting`
- `mmr_spread_setting`
- `debug_mode_setting`
- `live_channel_id`
- `lobby_channel_id`
- `captain_policy`

### `rsvp_events/{guild_id}`
Stores the current per-server RSVP event, including:
- event start time, one-hour confirmation deadline, five-minute-early lobby-open time, and game count
- Discord channel/message IDs
- active/confirmed/lobby-starting/lobby-open/completed/start-failed/cancelled/closed/reset lifecycle status
- RSVP and fill membership
- signup MMR/inhouse-record snapshots and automatic lobby handoff metadata
- persistent series progress, completed match IDs, current match/game, 15-minute wait deadline, and waiting/live/paused/completed state
- creator and update metadata

### `inhouse_mmr/{guild_id}/users/{user_id}`
Stores server-specific inhouse MMR and nickname.

### `deflated_mmr/{guild_id}`
Stores per-server matchmaking MMR caps keyed by Discord user ID, including the last-known name and administrator/timestamp audit metadata. FeederBot uses the lower of the current public MMR and the configured cap for regular team balancing, role tie-breaking, automatic Immortal captain selection, and timeout autopicks. Public lobby, RSVP, draft, and `!mmr` displays continue to use the public MMR.

### `wallets/{guild_id}/users/{user_id}`
Stores Feederbucks balances and optionally nicknames.

### `bets/{guild_id}/entries/{user_id}`
Stores active bet entry per user.

### `dd_tokens/{guild_id}/users/{user_id}`
Stores purchased double-down token count.

### `double_downs/{guild_id}/users/{user_id}`
Stores currently active double-downs for the live inhouse match.

---

## Environment variables

Create a `.env` file locally, or set these as config vars in your hosting environment.

### Required
- `DISCORD_TOKEN` — Discord bot token
- `STRATZ_TOKEN` — STRATZ API token
- `STEAM_API_KEY` — Steam Web API key
- `GOOGLE_APPLICATION_CREDENTIALS_JSON` — JSON string for Firebase service account credentials

### Example `.env`

```env
DISCORD_TOKEN=your_discord_bot_token
STRATZ_TOKEN=your_stratz_token
STEAM_API_KEY=your_steam_web_api_key
GOOGLE_APPLICATION_CREDENTIALS_JSON={"type":"service_account",...}
```

> Note: `GOOGLE_APPLICATION_CREDENTIALS_JSON` is expected to be the **raw JSON payload as a single string**, not a file path.

---

## Local setup

### 1. Clone the repository
```bash
git clone https://github.com/Arman681/DotA2-Inhouse-Bot.git
cd DotA2-Inhouse-Bot
```

### 2. Create and activate a virtual environment
#### Windows (PowerShell)
```bash
python -m venv dc_env
.\dc_env\Scripts\Activate.ps1
```

#### macOS / Linux
```bash
python3 -m venv dc_env
source dc_env/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Add environment variables
Create a `.env` file in the project root with the required variables shown above.

### 5. Run the bot
```bash
python -m bot.main
```

---

## Heroku deployment

The included `Procfile` is currently:

```text
worker: python -m bot.main
```

### Basic deployment steps
1. Create a Heroku app.
2. Push the repository to Heroku or deploy from GitHub.
3. Add the required config vars:
   - `DISCORD_TOKEN`
   - `STRATZ_TOKEN`
   - `STEAM_API_KEY`
   - `GOOGLE_APPLICATION_CREDENTIALS_JSON`
4. Ensure the **worker dyno** is enabled.
5. Deploy the latest commit.

### Notes
- This project is structured as a worker process, not a web server.
- If you use Heroku Scheduler or another external start/stop mechanism, make sure the worker dyno is scaled appropriately.
- Firestore persistence allows lobby/guild configuration data to survive dyno restarts.

---

## Startup behavior

On startup, FeederBot currently:
- initializes Firebase
- clears active bets across connected guilds
- loads the hero ID cache
- restores saved lobby players from Firestore
- restores the saved lobby message if it can still be found
- restores live channel IDs and lobby channel IDs
- starts the periodic MMR refresh task
- creates a shared `aiohttp` session

The periodic MMR refresh task runs every **18 hours**.

---

## Match tracking behavior

### Automatic flow after 🚀
After a 10/10 lobby is rolled, FeederBot starts waiting for a matching live game to appear on Steam.

- wait window: up to **15 minutes**
- poll interval while waiting: **30 seconds**
- if the lobby stays under 10 players for **30 seconds**, waiting is cancelled

### Live polling
Once a live match is found, FeederBot waits **30 seconds** before posting the first live match embed, then polls the match every **15 seconds** and updates that embed.

For remade lobbies that overlap on Steam's live endpoint, an Inhouse Admin can use `!startpolling <match_id>` to select an exact match being broadcast under the bound league ID. If the replacement match ID is not readily available, `!startpolling next` selects the highest live match ID newer than the currently tracked match. If nothing is currently tracked, `next` selects the highest live match ID for the bound league.

### Match resolution
When the match disappears from the Steam live endpoint, FeederBot retries completed-match lookup up to:
- **10 attempts**
- **30 seconds apart**

If a result is found:
- bets are resolved
- inhouse MMR is adjusted for inhouse matches
- double-downs are applied and cleared
- all participants are awarded 100 Feederbucks

For an active scheduled RSVP series, a successfully processed result advances the persisted game counter. If more games remain, FeederBot resets only the post-rocket team/draft state, keeps the confirmed roster locked, generates the next setup, and opens a new 15-minute Steam wait. The final planned result marks the series complete and does not open another wait. Automatic polling and manual `!submitmatch` processing share this same idempotent progression path.

---

## Permissions model

FeederBot currently recognizes three permission levels:

### Everyone
Most player-facing commands like `!cfg`, `!bet`, `!balance`, `!mmr`, and role preference commands.

### Server admin or role named `Inhouse Admin`
Used for lobby management and server configuration commands.

### Global admin
A single hard-coded Discord user ID in `bot/main.py` is allowed to use `!pose` and automatically bypasses admin-role checks.

---

## Current dependencies

Main packages presently used by the project include:
- `discord.py`
- `aiohttp`
- `python-dotenv`
- `requests`
- `firebase-admin`

See `requirements.txt` for the exact pinned versions.

---

## Practical setup checklist for a new server

1. Invite FeederBot to the server.
2. Run `!setlobbychannel` in the channel where you want lobby embeds.
3. Run `!setlivechannel` in the channel where you want live match updates.
4. Run `!bindleague <league_id>` for your inhouse league.
5. Run `!lobby regular` or `!lobby immortal`.
6. Ask players to run `!cfg <steam_id>`.
7. Optional: set `!setpassword <password>`.
8. Optional: set `!toggle_roles on` and have players set role preferences.

---

## Notes and implementation details

- Public MMR, private matchmaking MMR caps, and inhouse MMR are separate systems.
- A deflated MMR is a ceiling, so it can never raise a player whose public MMR later falls below the configured value.
- Placeholder players can join lobbies, but cannot be captains.
- Hero names are resolved from `hero_id_map.json`, with Steam API caching support.
- Prefixes are cached in memory after load for faster command prefix resolution.
- The bot uses a shared `aiohttp` session and closes it on shutdown.
- `firebase_setup.py` expects credentials to exist before any Firestore-dependent imports execute.
- `!livematch` is rate-limited per guild.
- `!bet` and some admin log commands also use cooldowns.

---

## Future README maintenance

If you add or change commands, update at minimum these sections:
- **Core features**
- **Commands**
- **Environment variables**
- **Firestore data model overview**
- **Practical setup checklist**

---

## License

No license file is currently included in the project.
