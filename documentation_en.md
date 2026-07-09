# CROUS Sentinel — CROUS housing alert bot (Val-de-Marne, France)

> A Telegram bot that continuously monitors the official French CROUS housing
> website (`trouverunlogement.lescrous.fr`) and sends a **real-time notification**
> the moment a student flat becomes available in the Val-de-Marne (dept. 94).
> Engineered with **reliability as the top priority**: never miss a listing, no
> duplicates, never crashes.

---

## 1. The problem

CROUS student housing is **in very high demand** and listings **disappear within
minutes**. The official website has two major hurdles:

1. A **waiting room** ("Vous êtes trop nombreux !" — *too many visitors*) that
   blocks access when traffic is high.
2. An **anti-bot protection**: a naive HTTP request (a simple script, `requests`,
   `curl`) always receives the waiting-room page instead of the actual content.

Watching the site by hand is impossible (it would mean refreshing day and night).
Hence this bot: it stands guard and sends a Telegram notification — with the
**residence name, price and a direct link** — as soon as a listing appears.

---

## 2. Solution at a glance

```
┌──────────────┐   every ~3 min         ┌────────────────────────┐
│    bot.py    │ ─────────────────────► │  Playwright (Chromium) │
│ (main loop)  │                        │  clears the waiting room│
└──────┬───────┘                        └───────────┬────────────┘
       │                                            │ calls the internal API
       │                                            ▼
       │                               POST /api/fr/search/{idTool}
       │                                            │  (structured JSON)
       │        ┌───────────────┐                   ▼
       │        │  store.py     │◄──── filter 94xxx + parsing
       │        │  seen.json    │      (name, price, town, link)
       │        └───────┬───────┘
       │  new listings  │
       ▼  only          ▼
┌──────────────┐   ┌────────────────┐
│ telegram.py  │──►│  📱 Telegram   │
└──────────────┘   └────────────────┘
```

**Key idea**: instead of scraping the HTML (fragile, breaks on any redesign), the
bot drives a **real browser** to clear the waiting room, then calls the website's
**internal JSON API** from within that browser. This yields clean, structured data
with the robustness of a real browser session.

---

## 3. Tech stack

| Component | Choice | Why |
|---|---|---|
| Language | **Python 3.12** | Simple, rich ecosystem |
| Browser automation | **Playwright** (Chromium) | Clears waiting room + anti-bot |
| Notifications | **Telegram Bot API** via `urllib` (stdlib) | Zero dependency, rock-solid |
| Memory store | Local **JSON file** (`seen.json`) | Simple, sufficient, atomic writes |
| Always-on | **Windows Task Scheduler** | Auto-start + auto-restart |
| Environment | Local **venv** | Isolated dependencies |

The only external dependency is `playwright`. Everything else is standard library.

---

## 4. How it works in detail

### 4.1 Clearing the waiting room
The site returns a page titled "Vous êtes trop nombreux !" when saturated.
`load_through_queue()` (in `crous.py`):
1. loads the page,
2. detects the waiting room (looks for the text in the `<title>`),
3. if detected: waits 20 s then retries (up to 10 times),
4. otherwise: we're through, continue.

### 4.2 Finding listings via the internal API
Once the browser is through, we read `/api/global/context` to discover the
**active campaigns** (`idTool`), then query each one:

```
POST /api/fr/search/{idTool}
{
  "idTool": 47,
  "location": [{"lon": 2.30, "lat": 48.87}, {"lon": 2.63, "lat": 48.64}],
  "price": {"max": 10000000},
  "pageSize": 100,
  ...
}
```

- `location` is a **bounding box** (two corners of a geographic rectangle)
  covering the whole Val-de-Marne.
- We query **all** active campaigns (current year + next year) and **de-duplicate**
  by listing `id`.

### 4.3 Precise dept.-94 filtering
The bounding box can slightly overlap Paris (75), Seine-Saint-Denis (93) or Essonne
(91). So we keep only listings whose **postal code starts with `94`** (parsed from
the address). The **exact town name** is read straight from the address
(`… 94800 Villejuif` → "Villejuif"): every Val-de-Marne town is covered and
correctly labelled.

### 4.4 Notifying only what's new
`store.py` maintains a `seen.json` file (a dictionary keyed by listing `id`). Each
cycle, a listing whose `id` is not already in memory is "new" → we notify it.
Reliability details:
- **Atomic writes** (`file.tmp` then `os.replace`): the file is never corrupted,
  even if the PC shuts down mid-write.
- Missing/corrupt file → start from an empty memory without crashing.

### 4.5 Sending to Telegram
`telegram.py` sends the message via the Telegram API (`urllib`, 3 retries with
backoff). A listing is marked "seen" **only after a successful send**: if Telegram
is momentarily unreachable, the listing is **retried** next cycle → no listing is
ever lost.

### 4.6 The main loop
`bot.py` orchestrates everything, continuously:
```
startup → Telegram "started" message
repeat forever:
    (ok, listings) = search dept. 94
    if failure (waiting room unbeatable, ...):
        count the failure; alert after 5 consecutive failures
    else:
        for each new listing: notify, then persist if send succeeded
    wait 150–180 s (jittered) then repeat
```

---

## 5. Reliability guarantees (the heart of the project)

| Guarantee | How |
|---|---|
| **Never crashes** | Every cycle is wrapped in `try/except`; any error is logged and the loop continues |
| **Never misses a listing** | Marked "seen" only after a successful notification (retry otherwise) |
| **No duplicates** | Persistent memory keyed by `id`, atomic writes |
| **Survives the waiting room** | Real browser + detect/wait/retry |
| **Tells "0 listings" apart from "failure"** | Search returns an `ok` status, never a misleading silence |
| **Warns if the bot goes blind** | Telegram alert after 5 consecutive failures |
| **Readable logs** | Journal in `bot.log` (even windowless), rotation at 5 MB |

These guarantees were **validated with deterministic tests** (simulating several
cycles, a Telegram outage, a corrupt file): 12/12 checks passed.

---

## 6. Project structure

```
CrousSentinel/
├── bot.py               # Main loop (orchestration + logging)
├── crous.py             # CROUS search (waiting room, API, dept.-94 filter)
├── store.py             # Seen-listings memory (seen.json)
├── telegram.py          # Telegram notifications
├── check_crous.py       # Manual tool: list dept.-94 listings (step 2)
├── check_new.py         # Manual tool: list new listings only (step 3)
├── test_telegram.py     # Manual tool: Telegram test message (step 1)
├── start_bot.bat        # Manual launcher (double-click)
├── install_task.ps1     # Install the auto-start service (Windows)
├── uninstall_task.ps1   # Remove the auto-start service
├── .env                 # SECRET: Telegram token + chat_id (not committed)
├── .env.example         # Config template
├── seen.json            # Memory (auto-generated)
├── bot.log              # Journal (auto-generated)
├── docs/                # Portfolio assets (Telegram preview mockup)
├── README.md            # Showcase / quick start
├── documentation.md     # Full documentation (French)
└── documentation_en.md  # Full documentation (English) — this file
```

### Role of each module
- **`crous.py`**: `load_through_queue()`, `get_active_tools()`, `search_tool()`,
  `parse_item()`, `is_in_94()`, `fetch_annonces_94()` → returns `(ok, listings)`.
- **`store.py`**: `load_seen()`, `save_seen()` (atomic), `mark_seen()`,
  `touch_seen()`, `split_new()`.
- **`telegram.py`**: `load_env()`, `send_message()` (with retries),
  `format_annonce_telegram()`.
- **`bot.py`**: `setup_logging()` (console + file logging), `une_verification()`
  (one cycle), `main()` (the loop).

---

## 7. Installation (from scratch)

```powershell
# 1. Create the virtual environment
cd D:\CrousBot
python -m venv .venv

# 2. Install Playwright + the Chromium browser
.\.venv\Scripts\python.exe -m pip install playwright
.\.venv\Scripts\python.exe -m playwright install chromium

# 3. Configure secrets: copy .env.example to .env and fill in
#    TELEGRAM_TOKEN=...   (from @BotFather on Telegram)
#    TELEGRAM_CHAT_ID=... (your chat id)
```

### Creating the Telegram bot
1. On Telegram, message **@BotFather** → `/newbot` → follow the steps → copy the
   **token**.
2. Open a chat with your new bot and press **Start**.
3. Get your **chat_id** (e.g. via @userinfobot, or the `getUpdates` API).

---

## 8. Usage — all commands

### Run manually (visible window, live logs)
```powershell
& "D:\CrousBot\.venv\Scripts\python.exe" "D:\CrousBot\bot.py"
```
or double-click **`start_bot.bat`**. Stop with **Ctrl + C**.

### Enable auto-start (once)
```powershell
powershell -ExecutionPolicy Bypass -File "D:\CrousBot\install_task.ps1"
```
Creates a `CrousSentinel` scheduled task that:
- starts on user logon,
- auto-restarts on crash,
- runs **windowless** (via `pythonw.exe`),
- writes its logs to `bot.log`.

### Manage the task
```powershell
# Watch logs live
Get-Content D:\CrousBot\bot.log -Wait -Tail 20

# Check status
Get-ScheduledTask -TaskName CrousSentinel

# Temporary pause (easily re-enabled)
Disable-ScheduledTask -TaskName CrousSentinel
Stop-ScheduledTask    -TaskName CrousSentinel   # also stops the running bot
Enable-ScheduledTask  -TaskName CrousSentinel   # re-enable

# Full removal
powershell -ExecutionPolicy Bypass -File "D:\CrousBot\uninstall_task.ps1"
```

### Diagnostic tools
```powershell
# Test Telegram only
& "D:\CrousBot\.venv\Scripts\python.exe" "D:\CrousBot\test_telegram.py"

# Print all dept.-94 listings (without notifying)
& "D:\CrousBot\.venv\Scripts\python.exe" "D:\CrousBot\check_crous.py"

# Print new listings only (and update memory)
& "D:\CrousBot\.venv\Scripts\python.exe" "D:\CrousBot\check_new.py"
```

---

## 9. Configuration

### In `bot.py`
| Setting | Default | Purpose |
|---|---|---|
| `INTERVAL_MIN` / `INTERVAL_MAX` | 150 / 180 s | Delay between two checks (jittered) |
| `HEADLESS` | `True` | `False` to watch the browser (debug) |
| `ALERT_AFTER_FAILURES` | 5 | Consecutive failures before a Telegram alert |
| `LOG_MAX_BYTES` | 5 MB | Max `bot.log` size before rotation |

### In `crous.py`
| Setting | Purpose |
|---|---|
| `BOX_94` | Geographic bounding box covering dept. 94 |
| `COMMUNES_94` | Fallback town labels (the name is mostly read from the address) |

**Target another department**: replace `BOX_94` with the desired bounding box and
adapt the postal-code filter in `is_in_94()` / `_postal_and_city()`.

---

## 10. Notable technical details

- **The waiting room only hits "simple" clients**: a `curl` gets the "too many
  visitors" page, but a Playwright browser gets through. This is what justifies the
  whole architecture.
- **`idTool` is dynamic**: each CROUS campaign (current year, next year…) has an id
  that changes over time. We read it live from `/api/global/context` rather than
  hard-coding it → the bot survives campaign changes.
- **Rent is returned in cents** by the API (`29893` → `€298.93`).
- **A listing's direct link**: `/tools/{idTool}/accommodations/{id}`.
- **Windows encoding**: the Windows console (cp1252) can't render emoji → console
  logs stay plain text, emoji are reserved for Telegram messages (sent as UTF-8
  over HTTP). File logs are UTF-8.

---

## 11. Known limitations & roadmap

**Current limitations:**
- Runs while the Windows session is **open** (PC on and logged in).
- Does not cover the "PC off / asleep" case.

**Possible improvements (portfolio roadmap):**
- ☁️ **Cloud deployment** (small VPS / Raspberry Pi) → 24/7 monitoring independent
  of the personal PC.
- 🔁 **Re-notification** when a listing disappears then reappears.
- 🎛️ **Advanced filters**: max price, type (studio / 1-bed / shared), min area.
- 📊 **History & stats**: which hours/days listings appear most.
- 🐳 **Docker** for reproducible deployment.
- 🤖 **Interactive Telegram commands** (`/status`, `/pause`, `/filter 400`).

---

## 12. What this project demonstrates (skills)

- **Reverse-engineering** a modern web app (SvelteKit): inspecting network traffic,
  discovering and reusing an undocumented internal API.
- **Browser automation** (Playwright) and bypassing a waiting room / anti-bot layer.
- **Reliability engineering**: exhaustive error handling, atomic writes, idempotency
  (no duplicates), failure recovery, telling failure apart from an empty result.
- **Deterministic testing** of network-dependent logic (via simulation).
- **Deployment / ops**: an always-on service via Windows Task Scheduler, log
  rotation, windowless execution.
- **Incremental design**: built and validated in verifiable steps (Telegram →
  search → memory → full loop → always-on).

---

*Personal project — developed with the assistance of Claude Code.*
