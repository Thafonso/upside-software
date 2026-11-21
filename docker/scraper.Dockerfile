# ============================================================
# Build do serviço `scraper` a partir de vendor/mazyos-scraper.
#
# Por que não usamos o Dockerfile do próprio scraper: o `go.mod`
# dele tem `replace github.com/gosom/scrapemate => ./third_party/
# scrapemate`, mas o Dockerfile roda `go mod download` depois de
# copiar só `go.mod` e `go.sum` - o diretório do replace ainda não
# existe e o download falha. Aqui copiamos a fonte antes de
# resolver os módulos. Nada do código do scraper é alterado:
# o contexto de build é o submodule, intacto.
#
# O restante (versões, dependências de runtime, entrypoint) é
# igual ao Dockerfile de origem.
# ============================================================

# syntax=docker/dockerfile:1

ARG GO_VERSION=1.27.1

FROM golang:${GO_VERSION}-trixie AS playwright-deps
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/browsers
ENV PLAYWRIGHT_DRIVER_PATH=/opt/ms-playwright-go
ARG PLAYWRIGHT_GO_VERSION=v0.6100.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && go install github.com/mxschmitt/playwright-go/cmd/playwright@${PLAYWRIGHT_GO_VERSION} \
    && mkdir -p /opt/browsers \
    && playwright install chromium --with-deps

FROM golang:${GO_VERSION}-trixie AS builder
WORKDIR /app
# A fonte inteira vem antes do download por causa do replace local.
COPY . .
RUN go mod download
RUN CGO_ENABLED=0 go build -ldflags="-w -s" -o /usr/bin/google-maps-scraper

FROM debian:trixie-slim
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/browsers
ENV PLAYWRIGHT_DRIVER_PATH=/opt/ms-playwright-go

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY --from=playwright-deps /opt/browsers /opt/browsers
COPY --from=playwright-deps /opt/ms-playwright-go /opt/ms-playwright-go

RUN chmod -R 755 /opt/browsers \
    && chmod -R 755 /opt/ms-playwright-go

COPY --from=builder /usr/bin/google-maps-scraper /usr/bin/

ENTRYPOINT ["google-maps-scraper"]
