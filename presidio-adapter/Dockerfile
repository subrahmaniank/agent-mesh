# Build context is the REPOSITORY ROOT (see docker-compose.yml), to keep every
# image in this repo building the same way.
FROM python:3.12-slim

WORKDIR /app

# No dependencies: stdlib only, so there is nothing to fetch and no CA layer to
# add. Presidio is reached over plain HTTP inside the compose network, which
# keeps this image small and the adapter easy to audit.
COPY presidio-adapter/main.py ./

RUN useradd --system --uid 10011 --create-home adapter && chown -R adapter:adapter /app
USER adapter

EXPOSE 8080
CMD ["python", "-u", "main.py"]
