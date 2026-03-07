FROM python:3.12-slim

WORKDIR /app

# Install OS deps needed by lxml (python-docx/pptx) and matplotlib
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 \
    libxslt1.1 \
    libfreetype6 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

EXPOSE 8900

CMD ["python", "server.py"]
