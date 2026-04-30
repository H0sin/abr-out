# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*

# Install xray-core (latest release for linux-64)
RUN curl -L -o /tmp/xray.zip \
      "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip" \
    && unzip /tmp/xray.zip -d /usr/local/xray \
    && chmod +x /usr/local/xray/xray \
    && rm /tmp/xray.zip
ENV PATH="/usr/local/xray:${PATH}"

RUN pip install httpx==0.28.1 loguru==0.7.3 pydantic==2.10.3 pydantic-settings==2.6.1

COPY prober/main.py /app/main.py

CMD ["python", "-u", "/app/main.py"]
