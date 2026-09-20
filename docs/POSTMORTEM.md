# Post-mortem: 17 days of silent blindness

**Project**: CROUS Sentinel, a Telegram bot that alerts on student housing in Val-de-Marne
**Incident**: 27 August 2026, 19:18 → 13 September 2026, 13:25 (detection), **17 days**
**Impact**: monitoring completely inoperative. No missed listing was demonstrated, but nothing guaranteed it either. Side effect: loss of the author's own access to the site from home.
**Written**: 16 September 2026, resolution added 18 September, figures recounted from the log on 20 September.

---

## Summary

A monitoring bot in production since July 2026 lost access to its data source on 27 August. It kept running, failing and retrying, one cycle every 150 to 180 seconds whenever the machine was awake, **for 17 days**: 505 consecutive failed cycles and 4 328 page-load attempts, counted in `bot.log`, without anyone noticing.

The health alert designed for this case **did work**: a Telegram message went out on 27 August at 19:58. One message. The code then marked the alert as "sent" and never repeated it. The message sank into the chat history, and the silence that followed was indistinguishable from normal operation.

The incident was only discovered because the author happened to ask "is it still running?".

---

## Timeline

| Date | Event |
|---|---|
| 08/07/2026 | Production. Windows scheduled task, one cycle every 2.5 to 3 min. |
| 17/08/2026 16:14 | Local Internet outage. The failure counter reaches 5, the alert fires **and fails** (`getaddrinfo failed`, 3/3 attempts). It is lost for good. |
| 26/08/2026 14:30 | Last successful cycle. |
| 27/08/2026 19:18 | First failure of the series. The site stops answering. |
| 27/08/2026 19:58 | Counter = 5. **Telegram alert delivered.** The only one. |
| 27/08 → 13/09 | 505 failed cycles. No further alert. No log read. |
| 13/09 13:25 | Discovered during a manual check. Bot stopped. |
| 16/09 10:40 | Test restart → `HTTP 429` on every request, home page included. |
| 16/09 12:33 | Access returns. One automated cycle makes it disappear again **in under a minute**. |
| 16/09 | Rework: backoff, automatic stop, repeated alerts, tests. |

---

## What went wrong

### 1. The one-shot alert (root cause)

```python
if echecs_consecutifs >= ALERT_AFTER_FAILURES and not alerte_envoyee:
    send_message(...)
    alerte_envoyee = True      # never re-armed before the next success
```

The flag was only reset on the first successful cycle. A 17-day outage therefore produced exactly **one** message, 39 minutes into the outage. A monitoring system that speaks once is not monitoring: it is notifying.

**Lesson**: an alert on a persistent state must repeat for as long as the state lasts. No news is never good news; it is ambiguous.

### 2. The alert lost when the send fails

On 17 August the alert fired during a DNS outage. `send_message` returned `False`, but the code marked the alert as sent anyway. The one moment when the infrastructure is down is precisely the moment the alert matters, and that is when it was thrown away.

**Lesson**: a notification is delivered only when the transport has confirmed it.

### 3. No backoff on `429`

The server answered `429 Too Many Requests`, an explicit order to slow down. The code never read the status code: it tried to parse the body as JSON, failed on `Expecting value: line 1 column 1`, and came back 170 seconds later. The bot was actively sustaining its own penalty.

**Lesson**: treat the server's control signals as instructions, not as generic errors.

### 4. A scraper with no damage limit

Nothing stopped the bot. No ceiling, no giving up. Thousands of requests for nothing, and as a consequence the loss of the author's own access to the site, from a fixed, non-renewable residential IPv4. Every block also blocked the browser the author needed to apply for housing.

**Lesson**: any automated process hitting a third-party service must know when to give up.

### 5. Two data bugs revealed by the tests

- **No pagination**: `page: 1, pageSize: 100`, no loop. Beyond 100 listings in the area, the rest was silently dropped.
- **Destructive deduplication**: `annonces[id] = a`. A listing present in two campaigns had the second overwrite the first, **together with its "low stock" and "high demand" flags**. Found by the first run of the new test suite.

### 6. A logger that killed its own error handler

Playwright error messages contain `→` (U+2192). On a Windows console in cp1252, `print` raises `UnicodeEncodeError`, from inside the `except` block. The original error was replaced by an unrelated crash.

**Lesson**: logging code must never be able to fail.

### 7. A design error: requests were not leaving the browser

The design rested on a verified observation: the site serves a waiting-room page to raw HTTP clients, and only a real browser gets through. The code nevertheless used `page.request.post()`, an HTTP client **on the Node side** that shares only cookies with the browser, not its network stack or session.

Symptom observed on 16/09: `page.goto()` loads the home page, then the next API call times out (`ETIMEDOUT`). Two network stacks, two treatments.

**Lesson**: check that the implementation does what the design says. The comment said "from the browser". The code did not.

---

## Diagnostic errors

The diagnosis itself produced three successive wrong conclusions, each corrected by a measurement:

1. **"Global service outage"**, based on an `ECONNREFUSED` from a third-party server. Refuted as soon as a friend loaded the site normally.
2. **"Block caused by request volume"**, refuted when the `429` turned out to **predate** the bot's restart, then when a handful of requests was enough to trigger the block again.
3. **"The browser goes through HTTP/3, the TCP client does not"**, a seductive hypothesis for why the author's browser worked while the automaton failed. Refuted: Playwright's browser stack fails too.

**Lesson**: measure before concluding, and state the hypothesis in a refutable form. All three errors were corrected by a test that contradicted them, never by more reasoning.

---

## Fixes

| Axis | Before | After |
|---|---|---|
| Nominal cadence | 1 cycle / 165 s, up to 524/day | 1 cycle / 900 s, **96/day** |
| Response to `429` | back in 170 s, indefinitely | 30 min → 1 h → 2 h → 4 h → 6 h cap |
| Prolonged block | 505 cycles in 17 days | **about 8 requests, then automatic stop at 24 h** |
| Alert | 1 message, never repeated | 1 message + reminder every 6 h |
| Undelivered alert | lost | retried on the next cycle |
| Back to normal | silent | recovery message |
| Pagination | first 100 results | all pages |
| Deduplication | last overwrites first | first occurrence + merged flags |
| Logging | crash on non-cp1252 character | clean degradation |
| API calls | Node client, outside the browser | `fetch()` from the page |
| Tests | isolated logic, hand-injected data | **44 tests, full chain in replay** |

---

## What worked

- **The single-instance lock** (Windows mutex) held for 66 days without a single duplicate, despite a scheduled task relaunching the bot every 10 minutes (970 duplicates killed).
- **Atomic writes** of `seen.json`: no corruption over thousands of cycles and two hard stops.
- **Logging with rotation**: the whole history of the incident was available for analysis. Without it, this post-mortem would not exist.
- **The global `try/except` net**: the bot never crashed (7 exceptions absorbed). It is also what made the outage invisible: reliability that masks a malfunction cuts both ways.

---

## Resolution, 18 September 2026

### The underlying question is settled

`seen.json` had been empty since day one. Detection bug, or a real shortage?

Since the site refuses any automated client (`429` within three seconds, even after access returned), the data was obtained **without circumventing the protection**: a HAR export of the Network tab from the author's own browser, then local extraction of the JSON bodies (`scripts/har_to_fixtures.py`). No additional request to the site.

Replay of the full chain on this real data (campaign 47):

| Detected | Screenshot of the site |
|---|---|
| PATIO, €323.31, 24000 Périgueux | "DE 323,31 À 646,61 €" |
| LE VELUM, €403.91, 40000 Mont-de-Marsan | "403,91 €" |
| Pierrette Grimaldi, €237.00, 20250 Corte | "237 €" |

**3/3 control towns detected, to the cent**, cents-to-euros conversion included.

**24 unique listings in the whole of France, across 20 départements. Zero in Val-de-Marne.**

→ **The detection chain was never at fault.** The bot missed nothing: there was nothing to report. Two months of doubt lifted by a one-second offline replay.

### What this changes about the block

The author's browser was getting the data at the exact moment Playwright received a `429`, **from the same IP**. The block is therefore not an address penalty but a discrimination on the client's signature, the `HeadlessChrome` header sent by default.

**Decision**: no stealth (User-Agent spoofing, `navigator.webdriver` patching, fingerprint blurring, proxy rotation). The work continues offline, on fixtures captured from the browser.

## Follow-up

The bot remains **stopped**, scheduled task disabled. Not out of technical caution any more, but because the site refuses automated access and the decision was made not to circumvent it.

The 44 tests cover parsing, filtering, pagination, deduplication, memory and message formatting, and the replay confirms all of it on real data. The project can be demonstrated end to end without any access to the site.

**The most uncomfortable part**: for 17 days this tool gave the illusion of watching over a vital need, finding a home. A silent system and a healthy system are indistinguishable until you design a way to tell them apart. That is the real lesson of this incident.
