# Build context: the prepared, revision-locked .runtime/sources directory.
FROM python:3.12.12-slim-bookworm
ENV PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends git build-essential libgl1 libglib2.0-0 ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
ARG ENGINE
COPY ${ENGINE} /opt/sources/${ENGINE}/
COPY gguf /opt/sources/gguf/
WORKDIR /opt/sources/${ENGINE}
RUN if [ "$ENGINE" = "see-through" ]; then pip install -c requirements.lock.txt -r requirements-inference.txt; else pip install -c requirements.lock.txt -r requirements.txt; fi
RUN if [ "$ENGINE" = "comfy" ]; then pip install -c requirements.lock.txt -r /opt/sources/gguf/requirements.txt; fi
# No model download, GPU access, or server startup during build.
ENTRYPOINT ["python"]
