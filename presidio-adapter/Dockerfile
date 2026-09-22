FROM python:3.12-slim

WORKDIR /app

# No dependencies: stdlib only. Presidio is reached over plain HTTP, which
# keeps this image small and the adapter easy to audit.
COPY main.py ./

RUN useradd --system --uid 10011 --create-home adapter && chown -R adapter:adapter /app
USER adapter

EXPOSE 8080
CMD ["python", "-u", "main.py"]
