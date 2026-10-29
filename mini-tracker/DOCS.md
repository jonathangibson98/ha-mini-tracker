# Mini Tracker

A lightweight location history map that lives inside Home Assistant. It records where your
people and devices have been, and shows it on a map with trails, playback, stops, places and a
heatmap. Everything is stored on your own Pi, and everything is set up from the app's own
**Settings** tab (the Configuration tab only has a log level).

## Quick start

1. Install and **Start** the app (see the repo README for how to add it).
2. Turn on **Show in sidebar**. A **Tracker** entry appears in your sidebar.
3. Open it. On the first start, every Home Assistant **person** that has GPS is tracked
   automatically. Your devices show up on the map within seconds.
4. Open **Settings > Tracked devices** to add or remove people and device trackers, set names,
   colors and pictures.
5. Want history from before today? **Settings > Data > Backfill** pulls past locations from
   Home Assistant, and **Import** reads files such as Google Takeout.

## Where the locations come from

Mini Tracker listens to Home Assistant over the Supervisor connection the app already has, so
there is **no token to create** and nothing to configure. Any `person.*` or `device_tracker.*`
entity that has latitude and longitude can be tracked. That includes the Companion app,
iCloud-style integrations, and your own device trackers.

Points are filtered before they are stored:

| Setting | Default | What it does |
| --- | --- | --- |
| Record when moved at least | 15 m | Ignores jitter while you are standing still |
| Heartbeat while still | 10 min | Stores a point this often even without movement, so stops have real durations |
| Ignore fixes worse than | 200 m | Drops very inaccurate GPS fixes |
| Keep history for | forever | Set days to auto-delete old points |

## The tabs

**Live**: who is where right now, how fresh the data is, battery and accuracy.

**History**: pick devices and a range (today, yesterday, 3/7/30 days, or custom), then **Show
history**. Options:

- *Show points* draws every recorded point (tap one for its time).
- *Heatmap* shows where you spend time.
- *Playback* appears under the summary: press play or drag the slider to watch the trip unfold.
  The whole range plays in about a minute at 1x.

**Timeline**: a day at a glance. Stops are detected automatically (default: 10 minutes within
75 m). Each stop is matched to your places and Home Assistant zones. Trips between stops show
distance, time and average speed. Tap a trip to draw it on the map. Unknown stops have a
**Save as place** button.

**Places**: your own named places (tap **Add place**, tap the map, set the radius) plus your
Home Assistant zones. **Time at places** totals how long a device spent at each place over
7, 30 or 90 days.

**Settings**: devices, display, recording, data tools, and integrations.

## Your own icons and pictures

For each person or place you can use:

- **A picture**: *Settings > Tracked devices > Upload picture*. It is cropped to a square and
  shrunk to 128 px.
- **An emoji**: type one into the icon field.
- **A custom SVG**: type `custom:name` to use `name.svg` from your icons folder. The default
  folder is `/config/custom_icons` (change it under *Settings > Display*). Subfolders work:
  `custom:rooms/kitchen`. The icon field offers your icons as suggestions, and the settings
  page tells you how many SVGs it found.

The app only has **read-only** access to your config folder, and only reads `.svg` files from
the icons folder.

## Dark mode

*Settings > Display > Theme* offers **Dark after sunset** (uses your `sun.sun` entity, falling
back to 7 pm to 7 am), **Follow device**, **Always light** and **Always dark**. With
OpenStreetMap tiles the map is inverted at night; with CARTO tiles the map switches to CARTO's
native dark style. You can also paste any tile URL.

## Units

Miles and feet by default. Switch to kilometers in *Settings > Display*.

## Read-only map for your dashboard

The app also serves a second, **read-only** page on port **8100**. It has no settings button
and cannot change anything:

```
http://<your-home-assistant-ip>:8100/
```

Add it to a dashboard with an iframe card:

```yaml
type: iframe
url: http://homeassistant.local:8100/?trail=12&chips=0
aspect_ratio: 100%
```

URL options (combine with `&`):

| Option | Example | Meaning |
| --- | --- | --- |
| `dev` | `dev=person.jon,person.mj` | Only show these devices |
| `trail` | `trail=24` | Draw the last N hours as a trail |
| `theme` | `theme=dark` | `dark`, `light`, `auto` or `system` |
| `chips` | `chips=0` | Hide the people bar at the top |
| `follow` | `follow=person.jon` | Keep the map centered on one device |

**HTTPS note:** if you open Home Assistant over https (Nabu Casa, DuckDNS, a reverse proxy),
browsers block an `http://` iframe inside it. Put the 8100 port behind your reverse proxy with
https (for example a Nginx Proxy Manager host pointing at `<ip>:8100`) and use that https URL.

**Privacy note:** the read-only page has no password. Anyone on your network who can reach port
8100 can see the map and history. You can turn it off in *Settings > Read-only map*, or remove
the port number in the app's **Network** section.

## Importing history

*Settings > Data > Import history file*. Choose which device the points belong to (or give a
new device a name), then pick a file. Supported:

- **Google Takeout / Timeline**: `Records.json` from older exports, and `Timeline.json`
  exported from the Google Maps app on your phone.
- **GPX** tracks, **GeoJSON** (including Overland exports), **OwnTracks** `.rec` files,
  **CSV** with `lat`, `lon` and a time column, and any JSON list with lat/lon/time fields.

Large files are streamed, so a several-hundred-MB Takeout file will not run the Pi out of
memory, but it can take a few minutes. Leave **Thin out redundant points** on unless you want
every single point. Re-importing the same file is safe: duplicates are skipped.

## Backfill from Home Assistant

*Settings > Data > Backfill* copies past locations from Home Assistant's own database for your
tracked devices. Home Assistant only keeps about 10 days by default, so this mainly helps right
after installing.

## Exporting and deleting

*Settings > Data > Export* downloads a device's history as GPX, CSV or JSON. The same section
can delete points older than N days, or everything for one device.

## Other apps that send locations (ingest API)

Turn on *Settings > Read-only map, ingest & MQTT > Ingest API* (this needs the read-only map
enabled, since it uses port 8100). Copy the **endpoint** and **token** shown there.

| App | How to set it up |
| --- | --- |
| **OwnTracks** | Mode: HTTP. URL: `http://<ip>:8100/api/ingest?token=TOKEN` |
| **Overland** | Receiver endpoint: `http://<ip>:8100/api/ingest?token=TOKEN&device_id=my-phone` |
| **Anything else** | `curl -X POST "http://<ip>:8100/api/ingest?token=TOKEN" -H "Content-Type: application/json" -d '{"device":"my gps","lat":40.1,"lon":-75.2}'` |

Each new device name becomes an *external* device (`ext.<name>`) with its own color and
picture. Accepted fields: `lat`, `lon`, plus optional `timestamp` (seconds, milliseconds or
ISO text), `accuracy` and `battery`. To use these apps away from home you need the port
reachable from outside, for example through your reverse proxy. Use https and keep the token
secret. **New token** invalidates the old one.

## Publish to Home Assistant (MQTT)

Optional, off by default. Turn on *Publish "place" and "distance today" sensors* and, if you
use the Mosquitto app, nothing else is needed: the broker is found automatically. For each
tracked device Home Assistant gets:

- `sensor ... place`: the name of the place or zone the device is at (or *Away*)
- `sensor ... distance today`: miles moved today
- a `device_tracker` too, for external devices

They are created with MQTT discovery and permanent unique IDs, so you can rename them and use
them in automations. If you use an external broker, fill in its host, port, username and
password.

## Backups

Everything lives in the app's `/data`, which is included in Home Assistant backups. The app is
set to stop briefly during a backup so the database is copied safely, so a few minutes of points
may be missed while a backup runs.

## Troubleshooting

**The map is empty.** Open *Settings > Tracked devices* and make sure at least one device is
ticked. If the page says it is not connected to Home Assistant, check the app log.

**A device never updates.** Mini Tracker only sees what Home Assistant sees. Check that the
entity itself updates in *Developer tools > States* and has `latitude` and `longitude`.

**My custom icon is blank.** Open *Settings > Display* and read the line under the icons
folder: it says how many SVGs were found. Check the folder name and that the file is
`name.svg`, then use `custom:name`.

**The dashboard iframe is blank.** Usually the https/http mix described above. Test the 8100
URL in its own browser tab first.

**Stops look wrong.** Adjust *A stop is at least* and *Stop radius* under *Recording & stops*.
A bigger radius merges nearby stops.

**Log shows MQTT errors.** Install and start the Mosquitto broker app, or enter your broker
details manually.

Set the log level to `debug` in the app's Configuration tab for more detail.

## What it does not do

- It does not talk to Apple, Google or any cloud service for locations. It only reads Home
  Assistant. (Your map tiles are fetched from the tile server you choose.)
- It does not do street-address lookups. Name your stops with **Save as place** instead.
