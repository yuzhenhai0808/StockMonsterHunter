FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home appuser
WORKDIR /home/appuser/app

# Install dependencies first so changes to source code don't invalidate the layer.
COPY --chown=appuser:appuser pyproject.toml README.md ./
RUN pip install --no-cache-dir --timeout 300 --retries 5 \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    $(python3 -c "import tomllib; deps=tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']; print(' '.join(deps))")

COPY --chown=appuser:appuser . .
RUN pip install --no-cache-dir --no-deps .

USER appuser

# No ENTRYPOINT: each compose service sets its own `command`.
