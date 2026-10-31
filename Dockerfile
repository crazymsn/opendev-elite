# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.13-slim-bookworm
FROM ${PYTHON_IMAGE} AS builder
WORKDIR /build
COPY pyproject.toml ./
COPY worker ./worker
COPY src/py ./src/py
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels '.[oidc]'

FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="OpenDev Elite" \
      org.opencontainers.image.description="GitHub discovery and developer sponsorship workspace"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    OPENDEV_DATA_ROOT=/var/lib/opendev-elite HOME=/home/opendev
RUN groupadd --gid 10001 opendev \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin opendev \
    && install -d -m 0700 -o 10001 -g 10001 /var/lib/opendev-elite
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels 'github-sponsorship-worker[oidc]' \
    && rm -rf /wheels
COPY deploy/healthcheck.py deploy/backup.py /opt/opendev/deploy/
WORKDIR /opt/opendev
USER 10001:10001
ENTRYPOINT ["python", "-I", "-u", "-m", "worker.container"]
CMD ["api"]
