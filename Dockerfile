# Official Playwright Python image: Chromium plus the Linux system packages
# Playwright needs are already installed. We re-run `playwright install` after
# pip install so the browser binaries match whatever version pip resolves for
# the `playwright` Python package. The -noble variant ships Python 3.12 —
# the -jammy variant is still on 3.10, which does not satisfy pyproject's
# `requires-python >= 3.11`.
FROM mcr.microsoft.com/playwright/python:v1.48.0-noble

USER root
WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e '.[dev]' \
    && playwright install --with-deps chromium

ENTRYPOINT ["flatpilot"]
CMD ["--help"]
