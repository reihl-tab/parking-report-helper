# syntax=docker/dockerfile:1

FROM python:3.13-slim@sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb AS build
WORKDIR /src
# Hash-locked installs only (#177): pinned build prerequisites first, then the
# project itself with --no-deps --no-build-isolation, so nothing else is fetched.
COPY requirements/build.txt ./requirements/build.txt
RUN pip install --no-cache-dir --require-hashes -r requirements/build.txt
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip wheel --no-cache-dir --no-deps --no-build-isolation --wheel-dir /wheels .

# Run the test suite: docker build --target test .
FROM python:3.13-slim@sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb AS test
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements/api.txt ./requirements/api.txt
RUN pip install --no-cache-dir --require-hashes -r requirements/api.txt
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-deps --no-build-isolation /wheels/*.whl
COPY sample.json ./sample.json
COPY tests ./tests
COPY scripts ./scripts
COPY docs ./docs
# Committed man pages, so the generated-page drift checks run here (#205).
COPY man ./man
# Fail, not skip, the man drift tests if man/ is ever missing here (#256).
ENV PARKING_REQUIRE_MAN_DRIFT=1
RUN python -m unittest discover -s tests -v

FROM python:3.13-slim@sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb AS runtime-base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8
COPY --from=build /wheels /wheels
# The core CLI has no third-party dependencies; --no-deps keeps it that way.
RUN pip install --no-cache-dir --no-deps --no-build-isolation /wheels/*.whl \
    && rm -rf /wheels
RUN useradd --create-home --uid 10001 app
USER app
WORKDIR /data
ENTRYPOINT ["parking-report"]
CMD ["--help"]

# Optional API runner, never embeds configuration or credentials.
FROM runtime-base AS api
USER root
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
# The wheel is already installed (runtime-base); add the api extra's locked set.
COPY requirements/api.txt /tmp/requirements/api.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements/api.txt \
    && rm -rf /tmp/requirements
USER app
ENTRYPOINT ["parking-report-106"]

# Render HTTP service: authenticated Login check only, no report submission.
FROM api AS http
EXPOSE 10000
ENTRYPOINT ["parking-report-http"]
CMD []

# Keep the default final image as the original core CLI.
FROM runtime-base AS runtime
