# The UI, built once
FROM node:22-bookworm-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# The service. Debian's ffmpeg includes x264, AAC and AMR-NB (needed by the phone-recording bench attack).
FROM python:3.13-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 1000 leaktrace
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY leaktrace/ leaktrace/
COPY --from=ui /ui/dist frontend/dist
ENV LEAKTRACE_DATA=/data PYTHONUNBUFFERED=1
RUN mkdir /data && chown leaktrace /data
USER leaktrace
VOLUME /data
EXPOSE 8003
# LEAKTRACE_SECRET and LEAKTRACE_ADMIN_TOKEN come from the environment at run time, never the image.
CMD ["python", "-m", "uvicorn", "leaktrace.server:app", "--host", "0.0.0.0", "--port", "8003"]
