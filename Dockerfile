# Oberfläche und API. Liest den Datenstand aus PostgreSQL; kein Modell, keine GPU nötig.
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY trendradar ./trendradar
RUN pip install --no-cache-dir ".[test]"
COPY taxonomie ./taxonomie
COPY daten ./daten
COPY nachweis ./nachweis
COPY tests ./tests
ENV PYTHONUNBUFFERED=1 TAXONOMIE=/app/taxonomie/trends.yaml
EXPOSE 8000
CMD ["uvicorn", "trendradar.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
