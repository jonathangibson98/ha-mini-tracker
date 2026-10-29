"""Optional: publish each device's current place + distance today to Home Assistant over MQTT.

Uses MQTT discovery, so sensors appear on their own with permanent unique IDs. Broker details
come from the Supervisor MQTT service (Mosquitto app) unless a host is set in Settings.
"""
from __future__ import annotations

import json
import logging
import re
import threading

log = logging.getLogger("mini_tracker.mqtt")

try:
    import paho.mqtt.client as mqtt  # type: ignore
except Exception:  # noqa: BLE001
    mqtt = None


def slug(dev: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", dev.lower())


class MqttPublisher:
    def __init__(self) -> None:
        self.client = None
        self.connected = False
        self.error = ""
        self.prefix = "mini_tracker"
        self._announced: set[str] = set()
        self._last: dict[str, str] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- lifecycle
    def start(self, host: str, port: int, username: str, password: str, prefix: str) -> None:
        self.stop()
        if mqtt is None:
            self.error = "paho-mqtt is not installed in this image"
            log.error(self.error)
            return
        self.prefix = prefix
        try:
            if hasattr(mqtt, "CallbackAPIVersion"):
                self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="mini_tracker")
            else:  # paho 1.x
                self.client = mqtt.Client(client_id="mini_tracker")
            if username:
                self.client.username_pw_set(username, password or None)
            self.client.will_set(f"{prefix}/status", "offline", retain=True)
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.reconnect_delay_set(min_delay=2, max_delay=60)
            self.client.connect_async(host, int(port), keepalive=60)
            self.client.loop_start()
            self.error = ""
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            log.exception("MQTT start failed")

    def stop(self) -> None:
        c, self.client = self.client, None
        if c is not None:
            try:
                c.publish(f"{self.prefix}/status", "offline", retain=True)
                c.loop_stop()
                c.disconnect()
            except Exception:  # noqa: BLE001
                pass
        self.connected = False
        with self._lock:
            self._announced.clear()
            self._last.clear()

    def _on_connect(self, client, userdata, *args) -> None:
        rc = args[1] if len(args) >= 2 else 0
        ok = (getattr(rc, "is_failure", None) is False) or rc == 0
        self.connected = bool(ok)
        if ok:
            log.info("MQTT connected")
            client.publish(f"{self.prefix}/status", "online", retain=True)
            with self._lock:
                self._announced.clear()  # re-announce after reconnects
                self._last.clear()
        else:
            self.error = f"connect refused: {rc}"

    def _on_disconnect(self, client, userdata, *args) -> None:
        self.connected = False

    # --------------------------------------------------------------- publishing
    def _announce(self, dev: str, name: str, is_external: bool) -> None:
        s = slug(dev)
        if s in self._announced or self.client is None:
            return
        device = {"identifiers": ["mini_tracker"], "name": "Mini Tracker", "manufacturer": "jonathangibson98",
                  "model": "Mini Tracker app"}
        avail = {"topic": f"{self.prefix}/status"}
        base = f"{self.prefix}/{s}"

        def cfg(kind: str, key: str, extra: dict) -> None:
            payload = {"name": f"{name} {extra.pop('label')}", "unique_id": f"mini_tracker_{s}_{key}",
                       "device": device, "availability": avail, **extra}
            self.client.publish(f"homeassistant/{kind}/mini_tracker_{s}/{key}/config",
                                json.dumps(payload), retain=True)

        cfg("sensor", "place", {"label": "place", "state_topic": f"{base}/place", "icon": "mdi:map-marker"})
        cfg("sensor", "distance_today", {"label": "distance today", "state_topic": f"{base}/distance_today",
                                         "unit_of_measurement": "mi", "device_class": "distance",
                                         "state_class": "total_increasing", "suggested_display_precision": 1})
        if is_external:
            cfg("device_tracker", "location", {"label": "location", "state_topic": f"{base}/state",
                                               "json_attributes_topic": f"{base}/attributes",
                                               "source_type": "gps"})
        self._announced.add(s)

    def publish_device(self, dev: str, name: str, place: str | None, distance_mi: float, fix: dict | None,
                       is_external: bool) -> None:
        if self.client is None or not self.connected:
            return
        s = slug(dev)
        base = f"{self.prefix}/{s}"
        with self._lock:
            self._announce(dev, name, is_external)
            updates = {f"{base}/place": place or "Away", f"{base}/distance_today": f"{distance_mi:.2f}"}
            if is_external and fix:
                updates[f"{base}/state"] = place or "not_home"
                updates[f"{base}/attributes"] = json.dumps({
                    "latitude": fix["lat"], "longitude": fix["lon"],
                    "gps_accuracy": fix.get("acc") or 0, "battery_level": fix.get("batt")})
            for topic, payload in updates.items():
                if self._last.get(topic) != payload:
                    self._last[topic] = payload
                    self.client.publish(topic, payload, retain=True)

    def remove_device(self, dev: str) -> None:
        if self.client is None:
            return
        s = slug(dev)
        for kind, key in (("sensor", "place"), ("sensor", "distance_today"), ("device_tracker", "location")):
            self.client.publish(f"homeassistant/{kind}/mini_tracker_{s}/{key}/config", "", retain=True)
        with self._lock:
            self._announced.discard(s)
