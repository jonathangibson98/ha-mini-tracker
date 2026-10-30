# Changelog

## 1.1.1 - 2026-10-30

- Fixed pictures being cropped to the middle of the photo, which cut the top off heads in portrait
  photos. Pictures now keep the top by default. New setting *Display > Pictures in the circles*:
  keep the top, center crop, or show the whole picture.
- Coordinate links are readable in dark mode.
- Accuracy is hidden when Home Assistant reports 0.

## 1.1.0 - 2026-10-30

- People and device trackers now automatically use the **picture Home Assistant has for them**
  (person photo), or else the **icon Home Assistant has for them** (for example `mdi:account-heart`).
  Zones show their Home Assistant icon too. Changes in Home Assistant show up on the map by themselves.
- Every icon set you have is usable: all ~7,400 **MDI** icons (`mdi:` and the old `hass:`, bundled, works
  offline), your `custom_icons` folder as **`local:`** (and `custom:`), and any folder you added with
  **Auto Custom Icons** (found automatically, used as `<your prefix>:name`).
- New **icon picker** with search across all sets, in Settings (people) and in the place editor.
- Icons that cannot be loaded fall back to initials instead of a broken image.
- Your own choice in Mini Tracker still wins; leave the icon blank to follow Home Assistant.

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
