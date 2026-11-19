# API de HotaruPodcast (FastAPI + FFmpeg; WhisperX y pyannote opcionales).
#
#   docker build -t hotaru-backend backend                       # con modelos, CPU
#   docker build --build-arg INSTALL_ML=false -t hotaru-backend backend   # sin modelos (HOTARU_ENGINE=fake)
#   docker build --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128 ...  # GPU NVIDIA
FROM python:3.12-slim

# FFmpeg/ffprobe para audio y vídeo y DejaVu para los rótulos del vídeo.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

ARG INSTALL_ML=true
# Por defecto torch de CPU (mucho más ligero). Para GPU, el índice de CUDA correspondiente.
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
RUN if [ "$INSTALL_ML" = "true" ]; then \
      pip install --extra-index-url "$TORCH_INDEX_URL" ".[ml]"; \
    else \
      pip install .; \
    fi

# Datos (episodios, perfiles, fondos) y caché de modelos, en volúmenes.
ENV HOTARU_DATA_DIR=/data \
    HF_HOME=/models/huggingface \
    TORCH_HOME=/models/torch \
    XDG_CACHE_HOME=/models/cache
RUN mkdir -p /data /models
VOLUME ["/data", "/models"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)" || exit 1

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
