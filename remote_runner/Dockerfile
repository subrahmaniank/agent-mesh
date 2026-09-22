# Build context is the REPOSITORY ROOT (see docker-compose.yml), because this
# worker imports the orchestrator package for its workflow and activity
# definitions — Temporal requires both ends to agree on them.
FROM python:3.12-slim

WORKDIR /app

# Trust any corporate TLS-inspecting proxy before reaching PyPI — see
# certs/README.md. Harmless and empty on a network without one.
COPY certs/ /usr/local/share/ca-certificates/extra/
RUN update-ca-certificates
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

COPY orchestrator/requirements.txt ./orchestrator/requirements.txt
COPY remote_runner/requirements.txt ./remote_runner/requirements.txt
RUN pip install --no-cache-dir \
    -r orchestrator/requirements.txt \
    -r remote_runner/requirements.txt

COPY observability/ ./observability/
COPY orchestrator/ ./orchestrator/
COPY remote_runner/ ./remote_runner/

RUN useradd --system --uid 10004 --create-home runner && chown -R runner:runner /app
USER runner

CMD ["python", "-m", "remote_runner.worker_poll"]
