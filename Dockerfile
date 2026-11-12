# syntax=docker/dockerfile:1.7

# Normal Workpiece resin-service builds never compile PrusaSlicer.
# Build Dockerfile.toolchain once (PrusaSlicer 2.9.7 + UVtools 7.1.2, 1-2 h) and pass its
# image as TOOLCHAIN_IMAGE; the host records that image id in its images lock.
ARG TOOLCHAIN_IMAGE=workpiece-resin-toolchain:local
FROM ${TOOLCHAIN_IMAGE}

# Optional: pass --build-arg SERVICE_COMMIT=$(git rev-parse HEAD) so /v1/estimate reports it.
ARG SERVICE_COMMIT=
ENV WORKPIECE_RESIN_SERVICE_COMMIT=${SERVICE_COMMIT}

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
COPY scripts ./scripts
COPY NOTICE LICENSE README.md ./

RUN useradd --create-home --uid 10001 slicer \
    && chown -R slicer:slicer /app /opt/venv
USER slicer

EXPOSE 8080
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
