FROM python:3.12-bookworm AS base
COPY --from=ghcr.io/astral-sh/uv:0.10.9 /uv /usr/local/bin/uv
ARG TARGETARCH
ARG D2_VERSION=0.9.0
RUN case "${TARGETARCH}" in \
      amd64) D2_SHA=5669ddc46b99e942cc96078f4a4e36d5e62103348f4c05179ede27802fdd87a9 ;; \
      arm64) D2_SHA=ac2c028697199479acb321db1e3d68caee9f2ba492ed73caa3cd13f3829bf913 ;; \
      *) echo "Unsupported D2 architecture: ${TARGETARCH}"; exit 1 ;; \
    esac \
    && curl -fsSL "https://github.com/d2lang/d2/releases/download/v${D2_VERSION}/d2-v${D2_VERSION}-linux-${TARGETARCH}.tar.gz" -o /tmp/d2.tar.gz \
    && echo "${D2_SHA}  /tmp/d2.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/d2.tar.gz -C /tmp \
    && cp "/tmp/d2-v${D2_VERSION}/bin/d2" /usr/local/bin/d2 \
    && rm -rf /tmp/d2*
ENV UV_NO_CACHE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml README.md uv.lock ./
COPY src ./src
RUN uv sync --locked --extra all --no-dev
ENV PATH="/app/.venv/bin:$PATH"
WORKDIR /repo
ENTRYPOINT ["archer"]
CMD ["--help"]

FROM base AS test
WORKDIR /app
RUN uv sync --locked --extra all --extra test --no-dev
COPY tests ./tests
ENTRYPOINT ["pytest"]
CMD ["-q", "-p", "no:cacheprovider"]
