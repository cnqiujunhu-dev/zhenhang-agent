FROM node:24-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20 AS web-build
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY web ./
RUN npm run build

FROM python:3.13-slim@sha256:8d9d0b8bcf6506481eae4907c18f5e3e7902e629f5f6d684f9e7c32e85e3ddf0 AS wheel-build
WORKDIR /app
ARG SOURCE_DATE_EPOCH=315532800
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}
COPY pyproject.toml README.md requirements-build.lock ./
RUN --mount=type=cache,target=/root/.cache/pip pip install --require-hashes -r requirements-build.lock
COPY src ./src
COPY --from=web-build /build/src/zhenxing/static/ui ./src/zhenxing/static/ui
RUN --mount=type=cache,target=/root/.cache/pip pip wheel --no-deps --no-build-isolation . --wheel-dir /wheels

FROM python:3.13-slim@sha256:8d9d0b8bcf6506481eae4907c18f5e3e7902e629f5f6d684f9e7c32e85e3ddf0 AS runtime
WORKDIR /app
COPY requirements.lock ./
RUN --mount=type=cache,target=/root/.cache/pip pip install --require-hashes -r requirements.lock
COPY --from=wheel-build /wheels /wheels
RUN pip install --no-deps --no-index /wheels/zhenxing_agent-*.whl && pip check
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin zhenxing && mkdir -p /app/var/attachments && chown -R zhenxing:zhenxing /app/var
USER zhenxing
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "zhenxing.app:app", "--host", "0.0.0.0", "--port", "8000"]
