FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /app

COPY requirements-app.lock ./requirements-app.lock
RUN pip install --no-cache-dir -r requirements-app.lock
# EMA-TTS only imports VoxCPM's Apache-2.0 AudioVAE decoder. Installing this
# pinned wheel without its unused full TTS stack avoids CUDA and web UI packages.
RUN pip install --no-cache-dir --no-deps voxcpm==2.0.3

RUN groupadd --gid 10001 agent \
    && useradd --uid 10001 --gid agent --create-home agent \
    && mkdir -p /app/.lakehouse-runtime /app/.voice-models /app/data_pipeline/lakehouse \
    && chown agent:agent /app/.lakehouse-runtime /app/.voice-models

COPY agentic_analytics ./agentic_analytics
COPY app ./app
# Code and reference data needed for on-demand EVDS acquisition
# (agentic_analytics/agent/tools/evds_acquisition.py, app/routes/evds.py):
# the audited EVDS download tools and the (small, static) series catalog they
# validate codes against. Observation data itself is never baked into the
# image; it is written at runtime under .lakehouse-runtime (see
# data_pipeline/evds/acquisition.py).
COPY tools ./tools
COPY data_pipeline/evds/acquisition.py ./data_pipeline/evds/acquisition.py
COPY data_pipeline/catalog/evds_series_catalog.parquet ./data_pipeline/catalog/evds_series_catalog.parquet

USER 10001:10001
EXPOSE 8870

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import json, urllib.request; r = urllib.request.urlopen('http://127.0.0.1:8870/api/status', timeout=3); assert json.load(r)['status'] == 'ok'"]

CMD ["python", "-m", "app", "--host", "0.0.0.0", "--port", "8870"]
