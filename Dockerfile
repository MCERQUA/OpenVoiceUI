FROM python:3.12-slim

WORKDIR /app

# System deps for cryptography, audio processing, vision, and canvas features
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev \
    libgl1 libglib2.0-0 \
    ffmpeg \
    libsndfile1 \
    git && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Version stamp — baked at build time by jambot-build-images.sh
ARG BUILD_COMMIT=unknown
ARG BUILD_BRANCH=unknown
ARG BUILD_DATE=unknown
RUN echo "{\"commit\":\"${BUILD_COMMIT}\",\"branch\":\"${BUILD_BRANCH}\",\"date\":\"${BUILD_DATE}\"}" > /app/version.json

# Writable dirs for runtime data
RUN mkdir -p runtime/uploads runtime/canvas-pages runtime/known_faces runtime/music runtime/generated_music runtime/faces runtime/transcripts

# Run as non-root user
RUN useradd -m -u 1001 appuser && chown -R appuser:appuser /app
USER appuser

# Allow git operations in /app (owner may differ between build and runtime)
RUN git config --global --add safe.directory /app

# Bind to all interfaces inside the container
ENV HOST=0.0.0.0

EXPOSE 5001

CMD ["python3", "server.py"]
