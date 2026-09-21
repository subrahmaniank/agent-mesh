FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY runner_shim.py worker_poll.py ./

CMD ["python", "worker_poll.py"]
