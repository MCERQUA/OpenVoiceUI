FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir supertonic==1.1.2 fastapi uvicorn soundfile numpy

RUN useradd -r -m appuser

WORKDIR /app
COPY server.py .
RUN chown -R appuser:appuser /app

USER appuser

# Pre-download models as appuser so the cache path matches runtime
RUN python3 -c "from supertonic import TTS; tts = TTS(auto_download=True)"

EXPOSE 8765

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8765"]
