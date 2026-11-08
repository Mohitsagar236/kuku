FROM node:24-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock pyproject.toml ./
COPY src ./src
COPY --from=frontend /build/src/promo_finder/resources/web ./src/promo_finder/resources/web
RUN pip install --no-cache-dir -c requirements.lock . \
    && useradd --create-home --uid 10001 promo \
    && mkdir /app/outputs && chown promo:promo /app/outputs
USER promo
ENV PORT=8765
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/readyz' % os.environ.get('PORT', '8765'), timeout=2)"
# PORT is set by Render; --proxy-headers makes the https Origin check work behind its proxy.
CMD ["sh", "-c", "exec uvicorn promo_finder.interfaces.web.app:create_app --factory --host 0.0.0.0 --port ${PORT} --no-access-log --proxy-headers --forwarded-allow-ips='*'"]
