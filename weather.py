"""
weather — current conditions and forecasts

Usage:
  weather current [--location NAME]
  weather forecast [--location NAME]
  weather clear-location

Help: help weather | weather --help
"""

import argparse
import os, json, urllib.request, urllib.parse, datetime
from logui import logui

_OUT = '.out/weather.json'

_WMO = {
    0:  'clear sky',
    1:  'mainly clear',          2:  'partly cloudy',          3:  'overcast',
    45: 'fog',                   48: 'rime fog',
    51: 'light drizzle',         53: 'drizzle',                55: 'heavy drizzle',
    56: 'light freezing drizzle',57: 'freezing drizzle',
    61: 'light rain',            63: 'rain',                   65: 'heavy rain',
    66: 'light freezing rain',   67: 'freezing rain',
    71: 'light snow',            73: 'snow',                   75: 'heavy snow',
    77: 'snow grains',
    80: 'light showers',         81: 'showers',                82: 'heavy showers',
    85: 'light snow showers',    86: 'snow showers',
    95: 'thunderstorm',          96: 'light thunderstorm with hail', 99: 'thunderstorm with hail',
}

_EMOJI = {
    'clear':   (frozenset([0, 1]),                        '☀️'),
    'cloudy':  (frozenset([2, 3]),                        '☁️'),
    'fog':     (frozenset([45, 48]),                      '🌫️'),
    'drizzle': (frozenset([51, 53, 55, 56, 57]),          '🌦️'),
    'rain':    (frozenset([61, 63, 65, 66, 67,
                           80, 81, 82]),                  '🌧️'),
    'snow':    (frozenset([71, 73, 75, 77, 85, 86]),      '❄️'),
    'storm':   (frozenset([95, 96, 99]),                  '⛈️'),
}

def _emoji(wmo_code):
    for _, (codes, emoji) in _EMOJI.items():
        if wmo_code in codes:
            return emoji
    return ''


def _load():
    if os.path.exists(_OUT):
        return json.loads(open(_OUT).read())
    return {}


def _save(data):
    os.makedirs('.out', exist_ok=True)
    open(_OUT, 'w').write(json.dumps(data, indent=2))


def _resolve_location(location_arg=None):
    if location_arg:
        return location_arg
    v = _load().get('location', '').strip()
    if v: return v
    raise ValueError('no location set — use: weather set-location <name>')


def _geocode(location):
    url = f'https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(location)}&count=1&language=en&format=json'
    with urllib.request.urlopen(url, timeout=8) as r:
        data = json.loads(r.read())
    results = data.get('results')
    if not results:
        raise ValueError(f'location not found: {location}')
    r = results[0]
    return r['latitude'], r['longitude'], r.get('name', location)


def _fetch(lat, lon):
    params = (f'latitude={lat}&longitude={lon}'
              f'&current=temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code'
              f'&wind_speed_unit=kmh&timezone=auto'
              f'&hourly=temperature_2m,weather_code&forecast_days=1')
    with urllib.request.urlopen(f'https://api.open-meteo.com/v1/forecast?{params}', timeout=8) as r:
        return json.loads(r.read())


def current(location=None, *args):
    try:
        explicit    = location
        location    = _resolve_location(location)
        lat, lon, name = _geocode(location)
        d        = _fetch(lat, lon)
        c        = d['current']
        temp     = round(c['temperature_2m'])
        feels    = round(c['apparent_temperature'])
        humid    = round(c['relative_humidity_2m'])
        wind     = round(c['wind_speed_10m'])
        wmo_code = c['weather_code']
        current_data = dict(location=name, temp=temp, feels=feels, humidity=humid, wind_kmh=wind, wmo_code=wmo_code)
        now   = datetime.datetime.now()
        hours = []
        for t, temp, wmo_code in zip(d['hourly']['time'], d['hourly']['temperature_2m'], d['hourly']['weather_code']):
            dt = datetime.datetime.fromisoformat(t)
            if dt < now: continue
            condition = _WMO.get(wmo_code, '?')
            hours.append({'time': dt.strftime('%H:%M'), 'temp': round(temp), 'condition': condition, 'wmo_code': wmo_code})
            if len(hours) == 5: break
        hours_text='|'.join(f"{h['time']},{h['temp']},{h['condition']},{h['wmo_code']}" for h in hours)
        forecast_data = dict(location=name, count=len(hours), hours=hours_text)
        data = {**_load(), 'current': current_data, 'forecast': forecast_data,
                'updated': now.isoformat(timespec='seconds')}
        if explicit:
            data['location'] = name
        _save(data)
        logui('weather.updated', **{**current_data, **forecast_data})
    except Exception as e:
        logui('weather.error', msg=str(e))


def forecast(location=None, *args):
    return current(location, *args)


def clear_location():
    _save({})
    logui('weather.cleared')


def refresh():
    """Refresh weather, discovering the location if none is saved."""
    try:
        deferred = _discover()
        if callable(deferred):
            deferred()
    except Exception:
        pass


def _discover():
    import json, urllib.request
    from pathlib import Path
    _OUT = Path(__file__).parent / ".out"
    _WEATHER_F = _OUT / "weather.json"
    try:
        existing = json.loads(_WEATHER_F.read_text(encoding="utf-8"))
    except Exception:
        existing = {}
    city = existing.get("location", "").strip()
    if not city:
        try:
            with urllib.request.urlopen("https://ipwho.is/", timeout=5) as response:
                data = json.loads(response.read())
            city = data.get("city") if data.get("success") is True else None
            if city:
                existing["location"] = city
                _OUT.mkdir(parents=True, exist_ok=True)
                _WEATHER_F.write_text(json.dumps(existing, indent=2), encoding="utf-8")
                logui("location.discovered", city=city)
        except Exception:
            return
    if not city:
        return
    return lambda: current(city)




CLI_SCHEMA = {"ops": {
    "current": {"params": ["--location"]},
    "forecast": {"params": ["--location"]},
    "clear-location": {},
}}


def cli(argv):
    """Parse weather command tokens from mediactl and invoke the operation."""
    if not argv or argv == ["help"] or "--help" in argv or "-h" in argv:
        print(__doc__)
        return
    p = argparse.ArgumentParser(prog="weather", add_help=False, exit_on_error=False)
    p.add_argument("action", choices=CLI_SCHEMA["ops"], nargs="?")
    p.add_argument("args", nargs="*")
    p.add_argument("--location", default=None)
    args = p.parse_args(argv)

    a = args.action
    try:
        if   not a:              print("  usage: weather current|forecast [--location <n>] | clear-location")
        elif a == "current":     current(args.location)
        elif a == "forecast":    forecast(args.location)
        elif a == "clear-location": clear_location()
    except Exception as e:
        print(f"  ERROR: {e}")
