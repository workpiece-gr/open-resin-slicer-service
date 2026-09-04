# syntax=docker/dockerfile:1.7

# Prusa recommends building against its pinned dependency bundle for supported Linux source builds.
FROM ubuntu:24.04 AS prusa-builder
ARG PRUSA_SLICER_COMMIT=b028299c770b8380ee81c921a2867d522f288123
ARG BUILD_JOBS=4
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends \
    git build-essential autoconf automake libtool cmake libglu1-mesa-dev libgtk-3-dev \
    libdbus-1-dev libwebkit2gtk-4.1-dev libncurses-dev texinfo ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/prusa3d/PrusaSlicer.git /src/PrusaSlicer \
    && cd /src/PrusaSlicer \
    && git checkout "${PRUSA_SLICER_COMMIT}" \
    && sed -i 's#https://gmplib.org/download/gmp/gmp-6.2.1.tar.bz2#https://ftp.gnu.org/gnu/gmp/gmp-6.2.1.tar.bz2#' deps/+GMP/GMP.cmake
WORKDIR /src/PrusaSlicer/deps/build
RUN cmake .. -DDEP_WX_GTK3=ON && make -j"${BUILD_JOBS}"
WORKDIR /src/PrusaSlicer/build
RUN cmake .. \
    -DSLIC3R_STATIC=1 \
    -DSLIC3R_GTK=3 \
    -DSLIC3R_GUI=no \
    -DSLIC3R_PCH=OFF \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_PREFIX_PATH=/src/PrusaSlicer/deps/build/destdir/usr/local \
    && make -j"${BUILD_JOBS}" prusa-slicer

FROM ubuntu:24.04
ARG UVTOOLS_VERSION=6.2.0
ARG UVTOOLS_SHA256=cf0ce15f78f33a1e59d3948d224bc060bcbba2171e669513dcd2d6af92d2e90f
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PRUSA_SLICER_BIN=/opt/prusaslicer/prusa-slicer \
    UVTOOLS_CMD=/opt/uvtools/UVtoolsCmd \
    PROFILE_ROOT=/app/profiles \
    PORT=8080
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl unzip python3 python3-venv \
    libgl1 libglu1-mesa libgtk-3-0t64 libdbus-1-3 libwebkit2gtk-4.1-0 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=prusa-builder /src/PrusaSlicer/build/src/prusa-slicer /opt/prusaslicer/prusa-slicer
COPY --from=prusa-builder /src/PrusaSlicer/resources /opt/prusaslicer/resources
RUN mkdir -p /opt/uvtools \
    && curl --fail --location --retry 3 \
      "https://github.com/sn4k3/UVtools/releases/download/v${UVTOOLS_VERSION}/UVtools_linux-x64_v${UVTOOLS_VERSION}.zip" \
      -o /tmp/uvtools.zip \
    && echo "${UVTOOLS_SHA256}  /tmp/uvtools.zip" | sha256sum -c - \
    && unzip -q /tmp/uvtools.zip -d /opt/uvtools \
    && rm /tmp/uvtools.zip \
    && chmod +x /opt/uvtools/UVtoolsCmd
RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY profiles ./profiles
COPY NOTICE LICENSE README.md ./
RUN useradd --create-home --uid 10001 slicer \
    && chown -R slicer:slicer /app /opt/prusaslicer /opt/uvtools
USER slicer
EXPOSE 8080
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
