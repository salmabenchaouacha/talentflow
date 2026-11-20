FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[api,postgres,dashboard]"

COPY dashboard ./dashboard
COPY data/market ./data/market
RUN useradd --create-home talentflow && mkdir -p /app/outputs /app/data && \
    chown -R talentflow /app
USER talentflow

EXPOSE 8000 8501
CMD ["uvicorn", "talentflow.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
