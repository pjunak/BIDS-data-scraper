# One image for the whole project, run as four services:
#   collector  python -m collector daemon      (scheduler, writes /data)
#   datasette  datasette serve ...             (read-only SQL browser under /db/)
#   auth       python /app/web/auth.py serve   (password login for nginx)
#   web        nginx -c /app/web/nginx.conf    (dashboard, files, logs; the only public service)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Europe/Prague
WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends nginx \
 && rm -rf /var/lib/apt/lists/* /etc/nginx/sites-enabled /etc/nginx/conf.d

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY collector ./collector
COPY config.yaml ./config.yaml
COPY datasette ./datasette
COPY web ./web
RUN useradd --uid 1000 --create-home app && mkdir -p /data && chown app /data
USER app

EXPOSE 8080 8001
CMD ["python", "-m", "collector", "daemon"]
