FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pytest \
    && npm install -g typescript
WORKDIR /work
# Customer code is mounted at runtime. The runner always uses --network none.
USER nobody
CMD ["python", "-m", "compileall", "-q", "."]
