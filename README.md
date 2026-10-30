<p align="center"><img src="mini_tracker/logo.png" alt="Mini Tracker" width="420"></p>

# Mini Tracker for Home Assistant OS

A lightweight location history map that runs as a Home Assistant **app** (add-on) on your Pi.
It reads your people and device trackers straight from Home Assistant, stores history in SQLite,
and gives you a map with **trails, playback, stops, places, a heatmap**, your own icons and
pictures, and a read-only map you can embed on a dashboard. No cloud, no extra accounts.

See [mini_tracker/DOCS.md](mini_tracker/DOCS.md) for the full guide.

## Install

You need Home Assistant OS (or Supervised) with the Apps store, and a way to put files on it
(the **Samba share** or **Terminal & SSH** app).

### Option A: local app (copy the folder)

1. Install **Samba share** (or **Terminal & SSH**) from the App store if you have not already.
2. Copy the whole `mini_tracker` folder from this repo into the **`local_apps`** share
   (`/local_apps` over SSH; older versions call it `addons`).
3. **Settings > Apps > Install app**, three-dot menu (top right) > **Check for updates**
   (refresh the page if needed).
4. Under **Local apps**, open **Mini Tracker** > **Install**. The first build takes a few minutes
   on a Pi.
5. **Start** it, and switch on **Show in sidebar** and **Start on boot**.
6. Open **Tracker** in the sidebar.

### Option B: add this repository

1. **Settings > Apps > Install app**, three-dot menu > **Repositories**.
2. Add `https://github.com/jonathangibson98/ha-mini-tracker`.
3. Install **Mini Tracker** from the list, then continue from step 5 above.

Updating a local app: copy the new files over the old ones, then press **Rebuild** on the app page
(or raise `version` in `config.yaml` and press **Update**). Your data in the app's `/data` is kept across rebuilds
and is included in Home Assistant backups.

## First run

On first start every Home Assistant `person` with GPS is tracked automatically. Open
**Settings > Tracked devices** to change who is tracked, and **Settings > Data > Backfill** to pull
in the last days of history from Home Assistant.

## Requirements

- Home Assistant 2025.1 or newer, `aarch64` (Pi 4/5) or `amd64`.
- Your people/device trackers must report latitude and longitude in Home Assistant.
- Optional: the **Mosquitto broker** app for the MQTT sensors.

## Development

```bash
pip install -r requirements-dev.txt
pytest -q tests
```

The tests run the real backend against a small fake Home Assistant (websocket + REST) and cover
recording, stop detection, ingest, imports, backfill, exports and path-traversal protection.

## Layout

```
mini_tracker/
  config.yaml  Dockerfile  run.sh  DOCS.md  README.md  CHANGELOG.md  icon.png  logo.png
  translations/en.yaml
  app/         Python backend (aiohttp + SQLite) and static/ web UI (Leaflet, bundled)
tests/         unit + end-to-end tests with a fake Home Assistant
```

## Credits

Map rendering by [Leaflet](https://leafletjs.com) and [leaflet.heat](https://github.com/Leaflet/Leaflet.heat)
(both bundled, BSD-2-Clause) and [Material Design Icons](https://pictogrammers.com/library/mdi/)
(bundled, Pictogrammers Free License, see `mini_tracker/app/MDI-LICENSE.txt`). Map tiles are served by the provider you pick in Settings
(OpenStreetMap by default, &copy; OpenStreetMap contributors).

## License

[MIT](LICENSE)
