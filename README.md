# Pokémon GO events calendar

A subscribable calendar (`pokemon-go.ics`) of Pokémon GO events — Community Days,
Raid Hours, Raid Days, Spotlight Hours, Max Mondays, GO Battle League, seasons and
the season's daily bonuses (e.g. Friendship Friday) — with the useful details in
each event's description: bonuses, free Raid Passes, featured Pokémon, research, shiny.

Rebuilt twice a day by GitHub Actions.

## Subscribe

Google Calendar (web) → **Other calendars** → **+** → **From URL**:

```
https://raw.githubusercontent.com/Maxim12445/pokemon-go-calendar/main/pokemon-go.ics
```

On Android, then enable **Sync** for the calendar in the Google Calendar app settings.

## Run locally

```
python3 build_calendar.py
```

Python 3.10+, standard library only. Local-time events are written in `Europe/Lisbon`
(change `TIMEZONE` in the script if needed).

## Credits

All event data comes from [Leek Duck](https://leekduck.com/events/), with the event
list from [ScrapedDuck](https://github.com/bigfoott/ScrapedDuck). Not affiliated with
Niantic, Scopely, The Pokémon Company or Leek Duck.
