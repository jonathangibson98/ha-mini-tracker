FROM ghcr.io/home-assistant/base:3.24

# Python + the two libraries we need, straight from Alpine (no compiling on the Pi)
RUN apk add --no-cache python3 py3-aiohttp py3-paho-mqtt tzdata

WORKDIR /data

COPY app /app
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
