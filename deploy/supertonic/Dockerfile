FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir supertonic==1.1.2 fastapi uvicorn soundfile numpy

# Pre-download models so they're baked into the image
RUN python3 -c "from supertonic import TTS; tts = TTS(auto_download=True)"

RUN useradd -r -m appuser
USER appuser

WORKDIR /app
COPY server.py .

EXPOSE 8765

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8765"]
