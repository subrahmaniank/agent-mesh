FROM python:3.12-slim

WORKDIR /app

# temporalio is the only dependency, and only so approvals can be read from
# the workflow rather than trusted from a request header.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./

RUN useradd --system --uid 10010 --create-home shim && chown -R shim:shim /app
USER shim

EXPOSE 9000
CMD ["python", "-u", "main.py"]
