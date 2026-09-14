# Pass the reviewed official Python image digest at build time.
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN python -m pip install --no-cache-dir uv==0.12.11 \
    && uv sync --frozen --no-dev --no-editable --python /usr/local/bin/python \
    && rm -rf /root/.cache
USER 65532:65532
ENTRYPOINT ["/app/.venv/bin/python", "-m", "ai_company.cli"]
