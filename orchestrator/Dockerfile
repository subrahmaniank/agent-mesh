# Build context is the REPOSITORY ROOT.
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

RUN useradd --system --uid 10003 --create-home orchestrator && chown -R orchestrator:orchestrator /app
USER orchestrator

CMD ["python", "-m", "orchestrator.worker"]
