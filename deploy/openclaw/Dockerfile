FROM node:22-slim

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*
RUN npm i -g openclaw@latest

EXPOSE 18791

CMD ["openclaw", "gateway", "--port", "18791"]
