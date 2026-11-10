FROM python:3.12-slim-bookworm

WORKDIR /opt/sentient-voice
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir numpy==2.3.5 sherpa-onnx==2.0.1

COPY download_models.py .
RUN python download_models.py
COPY embedding.py server.py .
CMD ["python", "server.py"]
