FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
RUN python manage.py collectstatic --noinput && mkdir -p /app/media && useradd --create-home app && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["waitress-serve", "--host=0.0.0.0", "--port=8000", "--threads=8", "--max-request-body-size=6291456", "config.wsgi:application"]
