FROM python:3.12-slim

# Point pip at a mirror when the default PyPI is unreachable, e.g.
#   docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple ...
ARG PIP_INDEX_URL=https://pypi.org/simple

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

# python:3.12-slim ships only pip (no setuptools). Install the build backend
# from the same index explicitly, then build without isolation so pip does not
# try to fetch setuptools again from a possibly-unreachable index.
RUN pip install --no-cache-dir --root-user-action=ignore \
        --index-url "$PIP_INDEX_URL" setuptools wheel \
 && pip install --no-cache-dir --root-user-action=ignore \
        --no-build-isolation --index-url "$PIP_INDEX_URL" .

ENV WEB_HOST=0.0.0.0 \
    WEB_PORT=8000

EXPOSE 8000

CMD ["agent-web"]
