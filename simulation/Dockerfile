FROM python:3.12-slim

WORKDIR /app

# Dependencies
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Code
COPY . /app/simulation

RUN chmod +x /app/simulation/run_once.sh

CMD ["/app/simulation/run_once.sh"]
