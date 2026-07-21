# syntax=docker/dockerfile:1
FROM eclipse-temurin:17-jdk-jammy AS base

# ---------------------------------------------------------------------
# System deps + Python 3.13
# ---------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common curl unzip ca-certificates \
    && add-apt-repository ppa:deadsnakes/ppa -y \
    && apt-get update && apt-get install -y --no-install-recommends \
        python3.13 python3.13-venv python3-pip \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------
# Android SDK command-line tools -> build-tools (zipalign, apksigner)
# ---------------------------------------------------------------------
ENV ANDROID_SDK_ROOT=/opt/android-sdk
ENV ANDROID_BUILD_TOOLS_VERSION=34.0.0
RUN mkdir -p ${ANDROID_SDK_ROOT}/cmdline-tools && \
    curl -sSL -o /tmp/cmdline-tools.zip \
        https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip && \
    unzip -q /tmp/cmdline-tools.zip -d ${ANDROID_SDK_ROOT}/cmdline-tools && \
    mv ${ANDROID_SDK_ROOT}/cmdline-tools/cmdline-tools ${ANDROID_SDK_ROOT}/cmdline-tools/latest && \
    rm /tmp/cmdline-tools.zip && \
    yes | ${ANDROID_SDK_ROOT}/cmdline-tools/latest/bin/sdkmanager --licenses > /dev/null && \
    ${ANDROID_SDK_ROOT}/cmdline-tools/latest/bin/sdkmanager \
        "build-tools;${ANDROID_BUILD_TOOLS_VERSION}" "platform-tools" > /dev/null

ENV PATH="${ANDROID_SDK_ROOT}/build-tools/${ANDROID_BUILD_TOOLS_VERSION}:${ANDROID_SDK_ROOT}/platform-tools:${PATH}"

# ---------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------
WORKDIR /app

COPY requirements.txt .
RUN python3.13 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt
ENV PATH="/opt/venv/bin:${PATH}"

COPY . .

RUN mkdir -p uploads signed keystores logs data \
    && useradd --create-home appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# gthread worker class required for the Server-Sent-Events log stream
CMD ["gunicorn", "-k", "gthread", "--threads", "8", "-w", "2", \
     "-b", "0.0.0.0:8000", "--timeout", "600", "app:app"]
