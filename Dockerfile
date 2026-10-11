FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN useradd --uid 1000 --no-create-home --shell /usr/sbin/nologin portal
USER 1000
EXPOSE 8080
# Single worker on purpose: per-owner locks and rate limits are in-process.
CMD ["uvicorn", "app.wsgi:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
