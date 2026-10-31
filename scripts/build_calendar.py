#!/usr/bin/env python3
"""Build a subscribable .ics feed of the current Formula 1 season (all sessions, in IST).

Source: Jolpica F1 API (api.jolpi.ca), the community-maintained successor to Ergast.

Season rules
  The whole current season stays in the calendar, past sessions included. A season ends
  SEASON_END_BUFFER after the start of its final race. After that the feed switches to the
  next season. If the next season's schedule is not published yet, the feed is empty until it is.

Sessions without a published start time are left out until the time appears. Every session
has a stable UID, so a rescheduled session moves in place instead of being duplicated.
state.json tracks SEQUENCE/DTSTAMP so calendar clients notice those changes.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICS_PATH = ROOT / "f1.ics"
STATE_PATH = ROOT / "state.json"

API = "https://api.jolpi.ca/ergast/f1"
IST = timezone(timedelta(hours=5, minutes=30))
UID_DOMAIN = "formula-1-ics.rabbitwingz.github.io"
SEASON_END_BUFFER = timedelta(hours=3)

# API key -> (title label, uid slug, duration), in weekend order.
SESSIONS = [
    ("FirstPractice", "Practice 1", "fp1", timedelta(hours=1)),
    ("SecondPractice", "Practice 2", "fp2", timedelta(hours=1)),
    ("ThirdPractice", "Practice 3", "fp3", timedelta(hours=1)),
    ("SprintQualifying", "Sprint Quali", "sq", timedelta(hours=1)),
    ("SprintShootout", "Sprint Quali", "sq", timedelta(hours=1)),  # 2023 name for the same session
    ("Sprint", "Sprint", "sprint", timedelta(hours=1)),
    ("Qualifying", "Quali", "quali", timedelta(hours=1)),
    ("Race", "Race", "race", timedelta(hours=2)),
]


def fetch_json(url: str, retries: int = 3):
    req = urllib.request.Request(url, headers={"User-Agent": "Formula-1-ICS/1.0", "Accept": "application/json"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except Exception as exc:  # network blips are common on shared CI runners
            if attempt == retries - 1:
                raise RuntimeError(f"GET {url} failed: {exc}") from exc
            time.sleep(2 * (attempt + 1))


def season_races(season: int) -> list[dict]:
    data = fetch_json(f"{API}/{season}.json?limit=100")
    return data["MRData"]["RaceTable"]["Races"]


def parse_start(block: dict) -> datetime | None:
    if not block.get("date") or not block.get("time"):
        return None
    return datetime.strptime(f"{block['date']}T{block['time']}", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def season_over(races: list[dict], now: datetime) -> bool:
    final = max(races, key=lambda r: int(r["round"]))
    start = parse_start(final) or datetime.strptime(final["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return start + SEASON_END_BUFFER <= now


def active_season(now: datetime) -> tuple[int, list[dict]]:
    """The earliest season whose final race hasn't finished yet (empty if not yet published)."""
    for season in (now.year - 1, now.year):
        races = season_races(season)
        if not races or not season_over(races, now):
            return season, races
    return now.year + 1, season_races(now.year + 1)


def gp_name(race_name: str) -> str:
    return race_name.replace("Grand Prix", "GP").replace("—", "-").replace("–", "-").strip()


def build_events(season: int, races: list[dict]) -> list[dict]:
    events = []
    for race in races:
        sessions = dict(race)
        sessions["Race"] = {"date": race.get("date"), "time": race.get("time")}
        circuit = race["Circuit"]
        loc = circuit.get("Location") or {}
        location = ", ".join(p for p in (circuit.get("circuitName"), loc.get("locality"), loc.get("country")) if p)
        for key, label, slug, duration in SESSIONS:
            start = parse_start(sessions.get(key) or {})
            if start is None:
                continue
            events.append({
                "uid": f"f1-{season}-r{int(race['round']):02d}-{slug}@{UID_DOMAIN}",
                "summary": f"🏁 {gp_name(race['raceName'])} - {label}",
                "start": start,
                "end": start + duration,
                "location": location,
                "description": f"{race['raceName']} - Round {race['round']} of the {season} season",
            })
    return events


# --------------------------------------------------------------------------- ICS output

def ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line: str) -> str:
    """Fold to 75-octet lines (RFC 5545 3.1) without splitting UTF-8 characters."""
    out, current, size = [], "", 0
    for ch in line:
        width = len(ch.encode("utf-8"))
        if size + width > 75:
            out.append(current)
            current, size = " ", 1
        current += ch
        size += width
    out.append(current)
    return "\r\n".join(out)


def fmt_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def fmt_ist(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%Y%m%dT%H%M%S")


def apply_state(events: list[dict], now: datetime) -> dict:
    """Bump SEQUENCE/DTSTAMP when an event changes; keep them stable otherwise."""
    old = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}
    new = {}
    for e in events:
        key = "|".join([e["start"].isoformat(), e["end"].isoformat(), e["summary"], e["location"]])
        fp = hashlib.sha1(key.encode("utf-8")).hexdigest()
        prev = old.get(e["uid"])
        if prev is None:
            entry = {"fingerprint": fp, "sequence": 0, "stamp": fmt_utc(now)}
        elif prev["fingerprint"] != fp:
            entry = {"fingerprint": fp, "sequence": prev["sequence"] + 1, "stamp": fmt_utc(now)}
        else:
            entry = prev
        e["sequence"], e["stamp"] = entry["sequence"], entry["stamp"]
        new[e["uid"]] = entry
    return new


def build_ics(season: int, events: list[dict]) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Rabbitwingz//Formula-1-ICS//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Formula 1",
        f"X-WR-CALDESC:Formula 1 {season} season - every session in IST",
        "X-WR-TIMEZONE:Asia/Kolkata",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
        "BEGIN:VTIMEZONE",
        "TZID:Asia/Kolkata",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0530",
        "TZOFFSETTO:+0530",
        "TZNAME:IST",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]
    for e in events:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{e['uid']}",
            f"DTSTAMP:{e['stamp']}",
            f"SEQUENCE:{e['sequence']}",
            f"DTSTART;TZID=Asia/Kolkata:{fmt_ist(e['start'])}",
            f"DTEND;TZID=Asia/Kolkata:{fmt_ist(e['end'])}",
            f"SUMMARY:{ics_escape(e['summary'])}",
            f"LOCATION:{ics_escape(e['location'])}",
            f"DESCRIPTION:{ics_escape(e['description'])}",
            "STATUS:CONFIRMED",
            "TRANSP:OPAQUE",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"


def main() -> int:
    now = datetime.now(timezone.utc)
    try:
        season, races = active_season(now)
    except Exception as exc:
        # Leave the published feed untouched rather than publishing a broken calendar.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    events = sorted(build_events(season, races), key=lambda e: (e["start"], e["uid"]))
    state = apply_state(events, now)
    ICS_PATH.write_bytes(build_ics(season, events).encode("utf-8"))
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    for e in events:
        print(f"{e['start'].astimezone(IST):%Y-%m-%d %H:%M IST}  {e['summary']}")
    print(f"{len(events)} sessions from the {season} season written to {ICS_PATH.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
