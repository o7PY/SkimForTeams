FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8080
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install .

COPY migrations ./migrations
COPY scripts ./scripts

RUN useradd --system --uid 10001 skim
USER skim

EXPOSE 8080
CMD ["python", "-m", "skim"]
