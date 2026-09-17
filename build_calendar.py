#!/usr/bin/env python3
"""Build a Pokémon GO events calendar (.ics) with useful descriptions.

Event list: ScrapedDuck (JSON scraped from Leek Duck).
Descriptions: each event's Leek Duck page (bonuses, raids, spawns, research...).
Daily Discoveries (e.g. Friendship Friday): the current season's page.

Only the Python standard library is used.
"""

import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ICS_PATH = ROOT / "pokemon-go.ics"
STATE_PATH = ROOT / "data" / "events.json"

EVENTS_URL = "https://raw.githubusercontent.com/bigfoott/ScrapedDuck/data/events.json"
USER_AGENT = "Mozilla/5.0 (compatible; pokemon-go-calendar; personal calendar feed)"
REQUEST_DELAY = 1.5  # seconds between Leek Duck requests
KEEP_PAST_DAYS = 30  # ended events stay in the calendar this long

# Leek Duck "local time" events are written in this time zone. Floating times
# are read as UTC by Google Calendar (events end up an hour off in Portugal).
TIMEZONE = "Europe/Lisbon"
VTIMEZONE = [
    "BEGIN:VTIMEZONE", "TZID:Europe/Lisbon", "X-LIC-LOCATION:Europe/Lisbon",
    "BEGIN:DAYLIGHT", "TZOFFSETFROM:+0000", "TZOFFSETTO:+0100", "TZNAME:WEST",
    "DTSTART:19700329T010000", "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU", "END:DAYLIGHT",
    "BEGIN:STANDARD", "TZOFFSETFROM:+0100", "TZOFFSETTO:+0000", "TZNAME:WET",
    "DTSTART:19701025T020000", "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU", "END:STANDARD",
    "END:VTIMEZONE",
]

MAX_DESCRIPTION = 6000
MAX_SECTION = {"features": 2000}
DEFAULT_MAX_SECTION = 1800
SKIPPED_SECTIONS = {"sales", "graphic"}

TYPE_LABELS = {
    "community-day": "Community Day",
    "raid-hour": "Raid Hour",
    "raid-day": "Raid Day",
    "raid-battles": "Raid",
    "pokemon-spotlight-hour": "Spotlight Hour",
    "max-mondays": "Max Monday",
    "max-battles": "Max Battle",
    "go-battle-league": "GBL",
    "season": "Season",
    "go-pass": "GO Pass",
    "wild-area": "Wild Area",
}

# Paragraphs that are the same on every page and add nothing.
BOILERPLATE = [
    r"^Trainers will be able to purchase and gift tickets",
    r"^Please note that Timed Research expires",
    r"^Visit the Pokémon GO Map",
    r"^If you’ve never attended a meetup",
    r"^Check it out in the Pokémon GO Web Store",
    r"^Tickets cannot be purchased",
    r"^Advertisement$",
    r"^REWARDS?$",
    r"able to purchase and gift tickets",
    r"^Tickets are nonrefundable",
    r"^\*Certain restrictions apply",
    r"^Stay tuned for when tickets",
]
BOILERPLATE_RE = re.compile("|".join(BOILERPLATE))

WEEKDAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]


# ---------------------------------------------------------------- fetching

def fetch(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            if attempt == retries - 1:
                print(f"  ! failed {url}: {exc}", file=sys.stderr)
                return None
            time.sleep(3 * (attempt + 1))


# ---------------------------------------------------------------- page parsing

TEXT_TAGS = {"p", "li", "h2", "h3", "h4"}
TEXT_CLASSES = {"bonus-text", "pkmn-name", "footnote"}
SKIP_TAGS = {"script", "style", "svg", "noscript", "h5"}
SKIP_CLASSES = {"event-toc", "ad-slot-group", "ad-label", "author-box", "daily-discoveries",
                "special-research-header", "special-research-subtitle", "rewards-header",
                "reward-bubble", "cp-values", "plus-more-rewards"}
# Inside a captured block, these start a new piece of text.
JOINERS = {"reward-list": " → ", "reward-label": ", "}
VOID_TAGS = {"img", "br", "hr", "source", "input", "meta", "link", "wbr"}


class PageParser(HTMLParser):
    """Turns a Leek Duck event page into sections of text blocks."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections = [("intro", [])]
        self.skip_tag = None
        self.skip_depth = 0
        self.cap_tag = None
        self.cap_depth = 0
        self.cap_kind = None
        self.buf = []

    def handle_starttag(self, tag, attrs):
        if tag in VOID_TAGS:
            if tag == "br" and self.cap_tag:
                self.buf.append(" ")
            return
        attrs = dict(attrs)
        classes = set((attrs.get("class") or "").split())

        if self.skip_tag:
            if tag == self.skip_tag:
                self.skip_depth += 1
            return
        if tag in SKIP_TAGS or classes & SKIP_CLASSES:
            self.skip_tag, self.skip_depth = tag, 1
            return

        if self.cap_tag:
            if tag == self.cap_tag:
                self.cap_depth += 1
            for cls in classes & JOINERS.keys():
                self.buf.append(JOINERS[cls])
            return

        if tag == "h2" and "event-section-header" in classes:
            self.sections.append((attrs.get("id") or "section", []))
            self.skip_tag, self.skip_depth = tag, 1  # header text is the section id
            return
        if "pkmn-list-item" in classes:
            return  # the name inside is captured via .pkmn-name
        if tag in TEXT_TAGS or classes & TEXT_CLASSES:
            if "pkmn-name" in classes:
                kind = "pokemon"
            elif tag in {"h2", "h3", "h4"}:
                kind = "heading"
            elif tag == "li" or "bonus-text" in classes:
                kind = "bullet"
            else:
                kind = "text"
            self.cap_tag, self.cap_depth, self.cap_kind, self.buf = tag, 1, kind, []

    def handle_endtag(self, tag):
        if self.skip_tag:
            if tag == self.skip_tag:
                self.skip_depth -= 1
                if self.skip_depth == 0:
                    self.skip_tag = None
            return
        if self.cap_tag and tag == self.cap_tag:
            self.cap_depth -= 1
            if self.cap_depth == 0:
                text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
                text = re.sub(r"\s*→\s*,\s*", " → ", text)
                if text:
                    self.sections[-1][1].append((self.cap_kind, text))
                self.cap_tag = None

    def handle_data(self, data):
        if self.cap_tag and not self.skip_tag:
            self.buf.append(data)


def page_content(html):
    start = html.find('class="event-description"')
    if start == -1:
        return None
    start = html.rfind("<", 0, start)
    end = html.find('class="author-box"', start)
    return html[start:end if end != -1 else len(html)]


def cut(text, limit):
    if len(text) <= limit:
        return text
    return text[:limit].rsplit("\n", 1)[0] + "\n…"


def describe_page(html):
    content = page_content(html)
    if content is None:
        return None
    parser = PageParser()
    parser.feed(content)

    parts = []
    for name, blocks in parser.sections:
        if name in SKIPPED_SECTIONS:
            continue
        lines, pokemon = [], []

        def flush_pokemon():
            if pokemon:
                lines.append("Pokémon: " + ", ".join(dict.fromkeys(pokemon)))
                pokemon.clear()

        for kind, text in blocks:
            if BOILERPLATE_RE.search(text):
                continue
            if kind == "pokemon":
                pokemon.append(text)
                continue
            flush_pokemon()
            if kind == "heading":
                lines.append(f"\n▸ {text}")
            elif kind == "bullet":
                lines.append(f"• {text}")
            else:
                lines.append(text)
        flush_pokemon()
        lines = [re.sub(r"\s*Learn more[^.!]*? here\.", "", l) for l in lines]

        body = "\n".join(lines).strip()
        if not body:
            continue
        if name == "intro":
            parts.append(body)
        else:
            title = name.replace("-", " ").upper()
            limit = MAX_SECTION.get(name, DEFAULT_MAX_SECTION)
            parts.append(f"━━ {title} ━━\n{cut(body, limit)}")
    return "\n\n".join(parts)


def describe_extra(event):
    """Fallback description from ScrapedDuck's extraData."""
    extra = event.get("extraData") or {}
    lines = []
    cd = extra.get("communityday") or {}
    if cd.get("bonuses"):
        lines.append("━━ BONUSES ━━")
        lines += [f"• {b['text']}" for b in cd["bonuses"]]
    if cd.get("spawns"):
        lines.append("Pokémon: " + ", ".join(s["name"] for s in cd["spawns"]))
    spot = extra.get("spotlight") or {}
    if spot.get("name"):
        lines.append(f"Featured: {spot['name']}")
    if spot.get("bonus"):
        lines.append(f"Bonus: {spot['bonus']}")
    raids = extra.get("raidbattles") or {}
    if raids.get("bosses"):
        lines.append("Bosses: " + ", ".join(b["name"] for b in raids["bosses"]))
    return "\n".join(lines)


def daily_discoveries(html):
    """[(weekday_index, title, [bullets], footnote)] from a season page."""
    days = []
    for card in re.findall(r'<div class="day-card">(.*?)</ul>(.*?)</div>\s*(?=<div class="day-card">|</div>)',
                           html, re.S):
        body, tail = card
        label = re.search(r'class="day-label">(.*?)<', body)
        title = re.search(r'class="day-title">(.*?)<', body)
        if not label or not title or label.group(1).strip().upper() not in WEEKDAYS:
            continue
        bullets = [clean(li) for li in re.findall(r"<li>(.*?)</li>", body, re.S)]
        note = re.search(r'class="footnote">(.*?)$', tail, re.S)
        days.append((WEEKDAYS.index(label.group(1).strip().upper()), clean(title.group(1)),
                     bullets, clean(note.group(1)) if note else ""))
    return days


def clean(fragment):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


# ---------------------------------------------------------------- dates

def parse_time(value):
    """ScrapedDuck times: '...Z' is UTC, otherwise local time (floating)."""
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt


def ics_time(dt):
    if dt.tzinfo:
        return ":" + dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f";TZID={TIMEZONE}:" + dt.strftime("%Y%m%dT%H%M%S")


def naive(dt):
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


# ---------------------------------------------------------------- ics output

def escape(text):
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r", "").replace("\n", "\\n"))


def fold(line):
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    out, chunk, limit = [], b"", 75
    for ch in line:
        b = ch.encode("utf-8")
        if len(chunk) + len(b) > limit:
            out.append(chunk.decode("utf-8"))
            chunk, limit = b"", 74  # continuation lines start with a space
        chunk += b
    out.append(chunk.decode("utf-8"))
    return "\r\n ".join(out)


def build_ics(entries, stamp):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//pokemon-go-calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Pokémon GO Events",
        "X-WR-CALDESC:" + escape("Pokémon GO events with bonuses and details. "
                                 "Data from Leek Duck (leekduck.com) via ScrapedDuck."),
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
        f"X-WR-TIMEZONE:{TIMEZONE}",
        *VTIMEZONE,
    ]
    for e in entries:
        lines += ["BEGIN:VEVENT", f"UID:{e['uid']}", f"DTSTAMP:{stamp}",
                  "SUMMARY:" + escape(e["summary"])]
        if e.get("all_day"):
            lines.append(f"DTSTART;VALUE=DATE:{e['start']}")
            lines.append(f"DTEND;VALUE=DATE:{e['end']}")
        else:
            lines.append("DTSTART" + e["start"])
            lines.append("DTEND" + e["end"])
        if e.get("rrule"):
            lines.append("RRULE:" + e["rrule"])
        if e.get("exdates"):
            lines.append("EXDATE;VALUE=DATE:" + ",".join(e["exdates"]))
        lines.append("DESCRIPTION:" + escape(e["description"]))
        if e.get("url"):
            lines.append("URL:" + e["url"])
        lines += ["TRANSP:TRANSPARENT", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"


# ---------------------------------------------------------------- main

def summary_for(event):
    name = re.sub(r"\s*\|[^|]*$", "", event["name"]).strip() if event["eventType"] == "go-battle-league" \
        else event["name"].strip()
    label = TYPE_LABELS.get(event["eventType"], "")
    if not label or label.lower() in name.lower():
        return name
    return f"{label}: {name}"


def main():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}

    raw = fetch(EVENTS_URL)
    if raw is None:
        sys.exit("Could not download the event list.")
    events = json.loads(raw)

    entries = {}
    season_pages = []
    global_weeks = set()

    for event in events:
        start, end = parse_time(event.get("start")), parse_time(event.get("end"))
        if not start or not end:
            continue
        uid = f"{event['eventID']}@pokemon-go-calendar"

        if event["eventType"] in {"wild-area", "go-fest", "go-tour"} and "global" in event["name"].lower():
            day = naive(start).date()
            while day <= naive(end).date():
                global_weeks.add(day - timedelta(days=day.weekday()))
                day += timedelta(days=1)

        print(f"- {event['name']}")
        html = fetch(event["link"])
        time.sleep(REQUEST_DELAY)
        details = describe_page(html) if html else None
        if not details:
            details = (state.get(uid) or {}).get("details") or describe_extra(event)
        if html and event["eventType"] == "season":
            season_pages.append((event, start, end, html))

        entries[uid] = {
            "uid": uid,
            "summary": summary_for(event),
            "start": ics_time(start),
            "end": ics_time(end),
            "sort": naive(start).isoformat(),
            "ends": naive(end).isoformat(),
            "details": details,
            "description": f"{details}\n\n🔗 {event['link']}".strip(),
            "url": event["link"],
        }

    # Daily Discoveries of the current season(s), as weekly all-day events.
    for event, start, end, html in season_pages:
        first, last = naive(start).date(), naive(end).date()
        for weekday, title, bullets, note in daily_discoveries(html):
            day = first + timedelta(days=(weekday - first.weekday()) % 7)
            exdates, d = [], day
            while d < last:
                if d - timedelta(days=d.weekday()) in global_weeks:
                    exdates.append(d.strftime("%Y%m%d"))
                d += timedelta(days=7)
            name = title.rstrip("*").strip()
            weekday_name = WEEKDAYS[weekday].title()
            if weekday_name.lower() not in name.lower():
                name = f"{weekday_name}: {name}"
            text = "\n".join(f"• {b}" for b in bullets)
            if note:
                text += f"\n\n{note}"
            text += ("\n\nDaily Discovery of the " + event["name"] + " season, all day unless stated. "
                     "Not active during the week of global GO Fest, GO Wild Area and GO Tour events.")
            uid = f"daily-{event['eventID']}-{WEEKDAYS[weekday].lower()}@pokemon-go-calendar"
            entries[uid] = {
                "uid": uid,
                "summary": f"{name} (daily bonus)",
                "all_day": True,
                "start": day.strftime("%Y%m%d"),
                "end": (day + timedelta(days=1)).strftime("%Y%m%d"),
                "rrule": f"FREQ=WEEKLY;UNTIL={(last - timedelta(days=1)).strftime('%Y%m%d')}",
                "exdates": exdates,
                "sort": day.isoformat(),
                "ends": last.isoformat(),
                "details": text,
                "description": f"{text}\n\n🔗 {event['link']}",
                "url": event["link"],
            }

    # Keep recently ended events that ScrapedDuck no longer lists.
    cutoff = (now - timedelta(days=KEEP_PAST_DAYS)).isoformat()
    for uid, old in state.items():
        if uid not in entries and old.get("ends", "") >= cutoff:
            entries[uid] = old

    ordered = sorted(entries.values(), key=lambda e: e["sort"])
    for e in ordered:
        e["description"] = cut(e["description"], MAX_DESCRIPTION)

    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps({e["uid"]: e for e in ordered}, ensure_ascii=False, indent=1) + "\n")

    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    ics = build_ics(ordered, stamp)
    old = ICS_PATH.read_bytes().decode("utf-8") if ICS_PATH.exists() else ""
    no_stamp = lambda s: re.sub(r"DTSTAMP:\S+", "", s)  # noqa: E731
    if no_stamp(old) != no_stamp(ics):
        ICS_PATH.write_bytes(ics.encode("utf-8"))
        print(f"Wrote {ICS_PATH.name} with {len(ordered)} events.")
    else:
        print("No changes.")


if __name__ == "__main__":
    main()
