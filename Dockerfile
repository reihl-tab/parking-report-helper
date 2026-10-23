# syntax=docker/dockerfile:1

FROM python:3.13-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /wheels .

# Run the test suite: docker build --target test .
FROM python:3.13-slim AS test
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir "$(ls /wheels/*.whl)[api]"
COPY sample.json ./sample.json
COPY tests ./tests
COPY scripts ./scripts
COPY docs ./docs
RUN python -m unittest discover -s tests -v

FROM python:3.13-slim AS runtime-base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels
RUN useradd --create-home --uid 10001 app
USER app
WORKDIR /data
ENTRYPOINT ["parking-report"]
CMD ["--help"]

# Optional API runner, never embeds configuration or credentials.
FROM runtime-base AS api
USER root
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir "$(ls /wheels/*.whl)[api]" && rm -rf /wheels
USER app
ENTRYPOINT ["parking-report-106"]

# Render HTTP service: authenticated Login check only, no report submission.
FROM api AS http
EXPOSE 10000
ENTRYPOINT ["parking-report-http"]
CMD []

# Keep the default final image as the original core CLI.
FROM runtime-base AS runtime
