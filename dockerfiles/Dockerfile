FROM ghcr.io/astral-sh/uv:0.12.17@sha256:10787c682e4184e4f290de1171fd4703dc63de99221f10fe1c99002ce7fa9acc AS uv

FROM docker.io/library/python:3.14.7-trixie@sha256:0876e54cf728d89fd9d0fdaf5837b9ee879ea5fbbd6fd0cddbe5eb0cce3f5f9e
COPY --from=uv /uv /uvx /usr/local/bin/

WORKDIR /tmp/titiler-build
COPY pyproject.toml uv.lock ./
COPY src/titiler/ src/titiler/
RUN UV_PROJECT_ENVIRONMENT=/opt/titiler-venv uv sync --frozen --package titiler-application --extra server --no-dev --no-default-groups --no-editable --compile-bytecode \
  && rm -rf /tmp/titiler-build /usr/local/bin/uv /usr/local/bin/uvx
ENV PATH=/opt/titiler-venv/bin:$PATH
ENV CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

###################################################
# For compatibility (might be removed at one point)
ENV MODULE_NAME=titiler.application.main
ENV VARIABLE_NAME=app
ENV HOST=0.0.0.0
ENV PORT=80
ENV WEB_CONCURRENCY=1
CMD gunicorn -k uvicorn.workers.UvicornWorker ${MODULE_NAME}:${VARIABLE_NAME} --bind ${HOST}:${PORT} --workers ${WEB_CONCURRENCY}
