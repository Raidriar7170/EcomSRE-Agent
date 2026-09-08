FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf

LABEL io.ecomsre.product="ecomsre-product-mvp-v01"

COPY --from=ghcr.io/astral-sh/uv:0.11.15@sha256:e590846f4776907b254ac0f44b5b380347af5d90d668138ca7938d1b0c2f98d3 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY config/product-v040/remediation-registry.v1.json ./config/product-v040/remediation-registry.v1.json

RUN chmod -R a+rX /app/src /app/config /app/pyproject.toml /app/uv.lock \
    && useradd --create-home --uid 10001 ecomsre \
    && mkdir -p /var/lib/ecomsre /var/lib/remediation-control /run/remediation-read /run/remediation-write /run/remediation-config \
    && chown -R ecomsre:ecomsre /var/lib/ecomsre /var/lib/remediation-control /run/remediation-read /run/remediation-write /run/remediation-config

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONPATH="/app/src" \
    PYTHONUNBUFFERED="1" \
    ECOMSRE_PRODUCT_DATA_ROOT="/var/lib/ecomsre"

USER ecomsre

CMD ["python", "-m", "ecomsre.product.app"]
