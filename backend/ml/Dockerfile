FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV ML_INTERVAL_SECONDS=3600

WORKDIR /app/ml

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x /app/ml/entrypoint.sh

# 기본은 학습 파이프라인 러너
CMD ["/app/ml/entrypoint.sh"]
