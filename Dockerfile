FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements-app.lock ./requirements-app.lock
RUN pip install --no-cache-dir -r requirements-app.lock

COPY app ./app
COPY tools ./tools
COPY data_pipeline/lakehouse/registry.py ./data_pipeline/lakehouse/registry.py
COPY data_pipeline/bddk/build_monthly_all_dataset.py ./data_pipeline/bddk/build_monthly_all_dataset.py

EXPOSE 8870

CMD ["python", "-m", "tools.run_agent_app", "--host", "0.0.0.0", "--port", "8870"]