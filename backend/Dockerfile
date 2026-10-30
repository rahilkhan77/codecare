FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends docker.io && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY backend /app/backend
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
ENV PYTHONPATH=/app/backend
WORKDIR /app/backend
EXPOSE 8000
CMD ["uvicorn", "codecare.main:app", "--host", "0.0.0.0", "--port", "8000"]
