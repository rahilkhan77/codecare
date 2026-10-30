FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends docker.io \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 codecare

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend /app/backend
COPY deploy/entrypoint.sh /entrypoint.sh
COPY index.html app.js styles.css theme.css pages.css favicon.svg og-image.svg robots.txt sitemap.xml /app/
COPY pricing /app/pricing
COPY about /app/about
COPY contact /app/contact
COPY how-it-works /app/how-it-works
COPY privacy-policy /app/privacy-policy
COPY terms /app/terms
COPY cookie-policy /app/cookie-policy
COPY acceptable-use /app/acceptable-use
COPY workspace /app/workspace

RUN chmod 755 /entrypoint.sh \
    && chown -R codecare:codecare /app
USER codecare
WORKDIR /app/backend

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=8 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz')"
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "codecare.main:app", "--host", "0.0.0.0", "--port", "8000"]
