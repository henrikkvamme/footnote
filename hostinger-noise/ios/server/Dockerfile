FROM python:3.12-slim
WORKDIR /app
# Only for the APNs Live Activity push path (HTTP/2 and ES256). The rest is the standard library.
RUN pip install --no-cache-dir "httpx[http2]==0.28.1" "cryptography==44.0.0"
# server.py, detect.py, techniques.json, and BUILD (the commit, written by deploy.sh). .dockerignore keeps out WAVs and test data.
COPY . .
ENV PYTHONUNBUFFERED=1 PORT=3000
EXPOSE 3000
CMD ["python", "server.py"]
