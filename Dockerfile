FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GRADIO_ANALYTICS_ENABLED=False \
    PLAYLIST_HOST=0.0.0.0 \
    PORT=8080

WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY playlist_agent/*.py ./playlist_agent/
COPY app.py ./
COPY data/catalogue.sqlite ./data/catalogue.sqlite
RUN mkdir -p data/sessions data/exports data/security \
    && chown -R 10001:10001 data \
    && chmod 0444 data/catalogue.sqlite

USER 10001:10001
EXPOSE 8080
CMD ["python", "app.py"]
