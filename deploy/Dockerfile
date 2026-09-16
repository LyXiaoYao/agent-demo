FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

ENV WEB_HOST=0.0.0.0 \
    WEB_PORT=8000

EXPOSE 8000

CMD ["agent-web"]
