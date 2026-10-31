FROM python:3.12-slim
WORKDIR /app
COPY frontend ./frontend
COPY backend ./backend
WORKDIR /app/backend
RUN mkdir -p /app/backend/data /app/access && useradd -u 10001 -m store && chown -R store:store /app
USER store
EXPOSE 3000
ENV PORT=3000 PYTHONUNBUFFERED=1 STYLEHUB_ACCESS_FILE=/app/access/StyleHub-admin-access.txt
CMD ["python", "server.py"]
