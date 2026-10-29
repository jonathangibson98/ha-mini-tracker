# Changelog

## 1.0.0 - 2026-10-29

First release.

- Live map of your Home Assistant people and device trackers, styled like the HA map (circle
  avatars, accuracy circles, zones), with dark mode after sunset.
- History stored in SQLite on the Pi: trails, optional points, heatmap, and playback scrubber.
- Day timeline: automatic stop detection, trips with distance/time/average speed, "save as place".
- Places: your own named places plus Home Assistant zones, with time-at-place stats.
- Your own pictures and `custom:` SVG icons for people and places.
- Import history (Google Takeout, GPX, GeoJSON, OwnTracks, CSV, generic JSON) into any device.
- Backfill from Home Assistant's own recorder history.
- Export to GPX / CSV / JSON. Retention and delete tools.
- Separate read-only map URL for dashboards (`?dev=`, `?trail=`, `?theme=`, `?chips=`, `?follow=`).
- Ingest API for OwnTracks (HTTP), Overland, and plain JSON.
- Optional MQTT discovery sensors (place, distance today) through the Mosquitto app.
- Miles by default (kilometers optional).
