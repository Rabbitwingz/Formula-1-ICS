# Formula 1 calendar feed

A subscribable `.ics` feed of every Formula 1 session in the current season (practice, sprint
qualifying, sprint, qualifying and race), with all times in Indian Standard Time (IST).

## Subscribe (Google Calendar)

1. Open Google Calendar on the web and go to **Other calendars** → **+** → **From URL**
2. Paste:

   ```
   https://raw.githubusercontent.com/Rabbitwingz/Formula-1-ICS/main/f1.ics
   ```

Google refreshes subscribed calendars on its own schedule, usually every 12–24 hours.

## How it works

A GitHub Actions workflow (`.github/workflows/update-calendar.yml`) runs every 6 hours. It
regenerates `f1.ics` with `scripts/build_calendar.py` from the [Jolpica F1 API](https://github.com/jolpica/jolpica-f1)
and commits the result whenever something changed.

- **Whole-season view:** every session of the current season stays in the calendar, past sessions included.
- **Season rollover:** three hours after the final race of the season starts, the feed switches to
  the next season. If that schedule isn't published yet, the feed stays empty until it is.
- **Titles:** `🏁 <GP Name> - <Session>`, for example `🏁 Australian GP - Practice 1`, `🏁 Chinese GP - Sprint Quali`, `🏁 Monaco GP - Quali`, `🏁 Monaco GP - Race`.
- **Location:** circuit, city and country.
- **Reschedules:** each session has a stable UID, so a rescheduled session moves the existing event
  and never creates a duplicate.
- **Missing times:** sessions without a published start time are left out until the time is announced.
- **Source outages:** if the API can't be reached, the run fails and the previously published feed stays as it was.

Run locally: `python scripts/build_calendar.py` (Python 3.9+, standard library only).
