FROM python:3.12-bookworm
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && python -m playwright install --with-deps chromium
COPY tour_api ./tour_api
ENV TOUR_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8000
CMD ["python", "-m", "tour_api", "serve", "--host", "0.0.0.0", "--port", "8000"]
