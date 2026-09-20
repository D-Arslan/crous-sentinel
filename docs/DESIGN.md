# CROUS Sentinel — design decisions and limits

The README keeps one line per decision. This file holds the reasoning, the mechanics of the
site, the operating details and the known debt. Every figure is traceable to a versioned
file or to `bot.log` (kept locally, not versioned), and says which.

## 1. Design decisions, and why

**A real browser, then the API from inside it.** The site (a SvelteKit app) serves a page
titled "Vous êtes trop nombreux !" to any client it does not like, and a plain `curl` or
`requests` never gets past it. A Playwright Chromium did, for two months. Once through,
the listings are not scraped from the HTML but read from the internal search API the page
itself calls, `POST /api/fr/search/{idTool}`: structured JSON, stable field names, no
dependence on the markup. The request must leave from the page: `crous.browser_fetch`
runs a `fetch()` via `page.evaluate`, so it shares the browser's network stack and session.
The first version used `page.request`, Playwright's Node-side HTTP client, which shares only
cookies; the design said "from the browser" and the code did not. See the post-mortem, §7.

**The waiting room is a loop, not an exception.** `load_through_queue` loads the home page,
reads the `<title>`, and if it contains "trop nombreux" waits 20 s and retries, up to 10
times. When in doubt (title unreadable) it assumes it is still queued. A `429` status on the
navigation raises `RateLimitedError` immediately: insisting on a rate limit is the behaviour
the rework removed.

**Campaign ids are read, never hard-coded.** Each CROUS campaign ("tool") has an id that
changes with the school year. `/api/global/context` lists `currentSchoolYear` and
`nextSchoolYear` with an `isEnabled` flag; both enabled campaigns are queried. Observed on
2026-09-18: 42 (`flow` mechanism) and 47 (`residual`). The synthetic fixture also carries a
disabled campaign 38 to prove it is skipped.

**Bounding box, then postal code.** The search takes a `location` of two corners,
north-west and south-east. `BOX_94` is `[{lon 2.30, lat 48.87}, {lon 2.63, lat 48.64}]`,
deliberately generous, so it overlaps Paris, Seine-Saint-Denis and Essonne. The exact filter
is the postal code parsed from the residence address: keep `94xxx`. The town label is the
text following the postal code in the address, with a three-town fallback table for
addresses that lack one. The same function with `postal_prefix=None` is the France-wide
diagnostic mode.

**Pagination with a guard.** `pageSize` is capped at 100 by the server. `search_tool`
walks pages until a page comes back short, with a ceiling of 20 pages per campaign; hitting
the ceiling is logged as a possible truncation. The first version read page 1 only, which
the test `test_pagination_250_logements` would have caught.

**Duplicates keep the first occurrence and merge the urgency flags.** A listing can appear
in both campaigns. Overwriting on id (`annonces[id] = a`) let the second campaign erase the
first's `lowStock` and `highDemand` flags. `collect` now keeps the first record and ORs the
flags: a signal that tells the user to hurry must not be lost to ordering.

**Rents are in cents.** `occupationModes[].rent.min` is an integer in cents; the minimum
across occupation modes is kept and converted once, in `parse_item`. The replay on real
data checks three prices to the cent against screenshots of the site (PATIO 323.31,
LE VELUM 403.91, Pierrette Grimaldi 237.00).

**Failure is a status, not an empty list.** `fetch_annonces` returns `("ok", listings)`,
`("rate_limited", [])` or `("fail", [])`. Only `ok` lets the caller touch memory or count a
"0 listings" cycle. `ok` with an empty list is the normal state of Val-de-Marne.

**Seen only after sent.** In `bot.une_verification`, a new listing is formatted, sent, and
only if `send_message` returned `True` is it written to memory. A Telegram outage therefore
re-sends on the next cycle rather than dropping the listing. Memory entries carry the
residence, town, postal code, rent, URL, `first_seen` and `last_seen`; known listings get
their `last_seen` refreshed every cycle.

**Atomic memory.** `save_seen` writes `seen.json.tmp` then `os.replace`: a shutdown mid-write
leaves the previous file intact. A missing, truncated or non-object file loads as an empty
memory with a log line, never an exception (tested in `tests/test_store.py`).

**Alerts repeat, and count only when delivered.** After 5 consecutive failed cycles the bot
sends a health message; while the failure lasts it sends one every 6 hours; when a cycle
succeeds again it sends a recovery message. The "sent" timestamp is updated only when
Telegram accepted the message. Both rules come from the incident: the original flag was set
once and never re-armed, and it was set even when the send had failed (the alert of
2026-08-17 died on a DNS error).

**429 is an instruction.** A `429` anywhere in the cycle switches the loop to a backoff of
30 min, then 1 h, 2 h, 4 h, capped at 6 h. After 24 hours of continuous limiting the bot
sends a final message and exits. The reason is not politeness: the site is also the one the
author must use to apply, from a fixed residential IPv4, and the block hit that access too.

**One process, enforced twice.** The scheduled task starts the bot at logon and again every
10 minutes with `MultipleInstances IgnoreNew`. That setting only stops the task from
overlapping itself; the real guard is a named Windows mutex acquired at startup in
`ensure_single_instance`, so a bot started by hand and one started by the task cannot
coexist. Over 66 days the mutex stopped 970 duplicate launches (`bot.log`).

**Logging that cannot fail.** Playwright's error text contains `→`; on a cp1252 console
`print` raised `UnicodeEncodeError` from inside an `except` block and replaced the real
error. `log` now retries with replacement characters and, failing that, gives up silently.
Console output is ASCII-safe; emoji are reserved for Telegram messages, which travel as
UTF-8 over HTTP. The log file is UTF-8 and is archived to `bot.log.old` when it exceeds
5 MB, checked at startup and at the end of every cycle (close, rename, reopen, the
previous `.old` overwritten): the incident's process lived 66 days without a restart and
its log reached 6 MB.

**Zero dependencies beyond Playwright.** Telegram goes through `urllib`, the memory is
JSON, the tests are `unittest`. It keeps the install to one line and the failure surface
small; it also means no `pytest` fixtures and a hand-written `.env` reader.

## 2. What was measured, and what it does and does not establish

**The run** (`bot.log.old`, 62 979 timestamped lines, counted on 2026-09-20). One process
from 2026-07-09 10:55 to 2026-09-13 13:24. Gaps longer than 30 minutes between consecutive
lines add up to 45 days, so the bot was actually cycling for about 21 days: the machine is a
laptop that sleeps. 7 467 cycles ended with `Tour termine`, all with 0 listings in the box
for both campaigns; 1 735 ended with `Recherche non aboutie` (1 230 before the outage,
mostly the queue and short network losses; 505 during it). 7 tracebacks, all Playwright
(`BrowserType.launch` timeouts and `TargetClosedError`), all absorbed. It establishes that
the loop, the lock and the memory hold up unattended. It does not establish that a listing
would have been notified: none appeared.

**The outage.** Last success 2026-08-26 14:30:43; first failure 2026-08-27 19:18; 505
consecutive failures and 4 328 `page.goto` attempts until the manual stop on 2026-09-13
13:25. One alert delivered at 19:58 on the first evening (the log shows no Telegram error
after the 5th failure); one alert lost on 2026-08-17 16:14 with three `getaddrinfo` failures.
The post-mortem's first draft said "about 8 900 attempts": that was 17 days divided by the
cycle length, not a count, and it ignored the sleep.

**The replay** (`fixtures/real/`, campaign 47, five pages, extracted from a HAR export of
the author's own browser session on 2026-09-18 by `scripts/har_to_fixtures.py`, no request
added to the site). `scripts/diagnostic.py --rejeu fixtures/real` finds 24 unique listings
in 20 départements and 0 in Val-de-Marne; the three control towns come out at the exact
prices seen on screen. It establishes that the parse-filter-dedup chain works on real
payloads, and that on that day there was nothing in 94. It says nothing about other days;
the log does (see above).

**The test restart of 2026-09-16** (`bot.log`, 53 lines): 4 cycles, 4 failures, first the
context endpoint returning a non-JSON body (a 429 the old code could not name), then
`ERR_CONNECTION_TIMED_OUT` on the home page, 10 attempts per cycle. The author's own
browser was loading the site at the same time from the same IP, which is why the block is
read as a signature check rather than an address ban.

## 3. Operating it

**Configuration** lives at the top of `bot.py` and `crous.py`, not in a file:

| setting | value | meaning |
|---|---|---|
| `INTERVAL_MIN` / `INTERVAL_MAX` | 840 / 960 s | jittered pause between cycles (was 150–180 s before 2026-09-16) |
| `ALERT_AFTER_FAILURES` | 5 | consecutive failed cycles before the first health alert |
| `ALERT_REPEAT_S` | 21 600 | reminder period while the failure lasts |
| `BACKOFF_START_S` / `BACKOFF_MAX_S` | 1 800 / 21 600 | backoff after a 429, doubling to the cap |
| `STOP_AFTER_RATE_LIMITED_S` | 86 400 | continuous limiting after which the bot exits |
| `LOG_MAX_BYTES` | 5 000 000 | size that triggers archiving, checked at startup and after each cycle |
| `PAGE_SIZE` / `MAX_PAGES` | 100 / 20 | server page cap, per-campaign page ceiling |
| `BOX_94`, `COMMUNES_94` | see `crous.py` | search box, fallback town labels |

To watch another département, change the box and pass its postal prefix to
`fetch_annonces`; nothing else knows about 94.

**Secrets**: `.env` next to `bot.py`, two keys, `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID`,
read by a ten-line parser in `telegram.py`. `.env.example` is the template.

**Scheduled task** (`install_task.ps1`, run from the project folder): kills any running
`bot.py`, registers `CrousSentinel` to run `pythonw.exe bot.py` at logon and every 10
minutes, no execution time limit, on battery allowed, as the current user while logged in.
`uninstall_task.ps1` removes the task and the process. Useful commands:

```powershell
Get-Content .\bot.log -Wait -Tail 20
Get-ScheduledTask -TaskName CrousSentinel
Disable-ScheduledTask -TaskName CrousSentinel   # pause; Enable-ScheduledTask to resume
```

**Telegram retries**: three attempts, 2 s then 4 s between them, no sleep after the last
(`tests/test_ops.py` counts the sleeps). Until 2026-09-20 the delay was linear and the
function slept once more after its final failure.

**Manual tools** (`scripts/`): `telegram_smoke.py` sends one real message to check the
token and chat id; `check_crous.py` prints the current listings without notifying;
`check_new.py` prints only unseen listings and updates the memory (do not run it while the
bot runs, they share `seen.json`); `diagnostic.py` runs one France-wide cycle, live or
`--rejeu`; `capture_fixtures.py` records one live cycle's raw responses into
`fixtures/real/`; `har_to_fixtures.py` does the same from a browser HAR export;
`make_demo_fixtures.py` regenerates the synthetic fixtures; `export_diagram.py` renders the
README's Mermaid block to `docs/architecture.svg`.

**Before restarting the bot**, in this order: check the site answers from the machine; run
`capture_fixtures.py` for a single cycle; replay it with `diagnostic.py --rejeu fixtures/real`;
only then enable the task. Do not shorten the cadence: the block re-triggered in under a
minute of automated activity on 2026-09-16.

## 4. Known debt

- **`check_new.py` writes `seen.json`** with no lock against the running bot.
- **The startup message is unconditional**; a bot restarted every few minutes would spam.
  Not observed thanks to the mutex, but not prevented either.
- **No metric of "cycles per day" in the log** beyond counting lines; a one-line daily
  summary would have made the outage visible in a glance.
- **`.env` parsing is hand-rolled** and does not support quotes inside values.
- **Windows-only operations**: mutex via `ctypes`, `pythonw.exe`, Task Scheduler. On other
  platforms `ensure_single_instance` is a no-op.

## 5. Pitfalls met along the way

- **`page.request` is not the browser.** It is a Node HTTP client that reuses cookies and
  nothing else. Anything that must look like the page must run in the page.
- **A 429 with an HTML body looks like a JSON error.** The old code reported
  `Expecting value: line 1 column 1` and retried in 170 s. Read the status first.
- **Windows sleep is invisible in a log.** Timestamps simply jump; wall-clock uptime and
  active time are different numbers, and only the second one measures anything.
- **A one-shot alert is not monitoring.** It is a notification, and a notification is
  forgotten in a day. Persistent states need persistent alerts.
- **Measure before diagnosing.** Three successive hypotheses about the block (global outage,
  request volume, HTTP/3) were each refuted by a test, never by more reasoning.
