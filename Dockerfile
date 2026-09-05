# syntax=docker/dockerfile:1.7

# Normal Workpiece resin-service builds never compile PrusaSlicer.
# CI/production must supply an immutable TOOLCHAIN_IMAGE reference from
# toolchain.lock.json (image@sha256:...). The local tag is only for developers
# who explicitly build Dockerfile.toolchain themselves.
ARG TOOLCHAIN_IMAGE=workpiece-resin-toolchain:local
FROM ${TOOLCHAIN_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PROFILE_ROOT=/app/profiles \
    PORT=8080

RUN test -x /opt/prusaslicer/prusa-slicer \
    && test -x /opt/uvtools/UVtoolsCmd \
    && test -s /opt/workpiece-toolchain/manifest.json

RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY profiles ./profiles
COPY NOTICE LICENSE README.md ./

RUN useradd --create-home --uid 10001 slicer \
    && chown -R slicer:slicer /app /opt/venv
USER slicer

EXPOSE 8080
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
