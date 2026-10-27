FROM python:3.12-alpine
WORKDIR /app
COPY server.py index.html ./
VOLUME /data
CMD ["python", "-u", "server.py"]
