FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AI_HAWKEYE_GOOD_ROOT=/app/ai-hawkeye/Good-Badminton \
    AI_HAWKEYE_MAX_UPLOAD_MB=1024

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        git \
        libglib2.0-0 \
        libgl1 \
        libsm6 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN python -m pip install --upgrade pip \
    && python ai-hawkeye/setup_good_badminton.py --download-weights

EXPOSE 5050

CMD ["python", "ai-hawkeye/run_good_badminton.py", "--host", "0.0.0.0", "--port", "5050"]
