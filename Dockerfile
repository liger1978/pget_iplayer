FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common ca-certificates gnupg curl git \
    build-essential pkg-config gcc g++ make cmake \
    ccache scons patchelf file upx-ucl \
    zlib1g-dev libbz2-dev liblzma-dev libffi-dev libssl-dev \
    libreadline-dev libsqlite3-dev libgdbm-dev tk-dev libncursesw5-dev \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get install -y python3.12-dev \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
