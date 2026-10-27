# Mini Tracker

A tiny self-hosted location server: one Python file (no dependencies), SQLite, and a Leaflet map.
It accepts the same OwnTracks-style endpoint Reitti does, so the "Find My to Reitti" add-on works with it as-is.

## Run it
    docker compose up -d --build
Open http://SERVER-IP:8090, add a device, copy its token.

## Point the add-on at it
In the add-on: Reitti address = `http://SERVER-IP:8090`. For each Find My device, paste that device's token into **Token override**.

## Endpoint
POST /api/v1/ingest/owntracks?token=TOKEN  with  {"_type":"location","lat":..,"lon":..,"tst":..,"acc":..}

## Settings (environment variables)
ADMIN_PASSWORD (protects the map and settings; ingest still uses tokens), TILE_URL, PORT, DB_PATH.
