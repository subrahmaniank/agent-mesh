# Build context is the REPOSITORY ROOT (see docker-compose.yml) so the shared
# certs/ directory is reachable.
FROM python:3.12-slim

WORKDIR /app

# Trust any corporate TLS-inspecting proxy before reaching PyPI. Empty on a
# network without one, which is why the COPY targets the directory rather than
# a glob — a glob matching nothing is a build error.
COPY certs/ /usr/local/share/ca-certificates/extra/
RUN update-ca-certificates
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# temporalio is the only dependency, and only so approvals can be read from
# the workflow rather than trusted from a request header.
COPY cedar-shim/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY cedar-shim/main.py ./

RUN useradd --system --uid 10010 --create-home shim && chown -R shim:shim /app
USER shim

EXPOSE 9000
CMD ["python", "-u", "main.py"]
