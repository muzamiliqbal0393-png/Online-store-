FROM python:3.12-slim
WORKDIR /app/stylehub
COPY index.html app.js store.js style.css polish.css server.py payments.py payment-ui.js reviews.js reviews.css ./
COPY assets ./assets
RUN mkdir -p /app/stylehub/data /app/access && useradd -u 10001 -m store && chown -R store:store /app
USER store
EXPOSE 3000
ENV PORT=3000 PYTHONUNBUFFERED=1 STYLEHUB_ACCESS_FILE=/app/access/StyleHub-admin-access.txt
CMD ["python", "server.py"]
