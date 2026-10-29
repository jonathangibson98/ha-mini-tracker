#!/command/with-contenv bashio

export MT_LOG_LEVEL="$(bashio::config 'log_level')"
export MT_DATA=/data
export MT_HA_CONFIG=/homeassistant

bashio::log.info "Starting Mini Tracker..."
cd /app
exec python3 -u /app/main.py
