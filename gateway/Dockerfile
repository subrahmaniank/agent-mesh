FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir pyyaml httpx

COPY config.yaml ./config.yaml
COPY policies/ ./policies/
COPY server.py ./server.py

EXPOSE 8080

CMD ["python", "server.py"]
