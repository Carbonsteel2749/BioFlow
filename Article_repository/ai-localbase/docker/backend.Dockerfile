FROM golang:1.25-alpine AS builder

# 使用国内 Go 代理，避免 proxy.golang.org 被墙导致 go mod download 超时
ENV GOPROXY=https://goproxy.cn,direct
ENV GOSUMDB=off

WORKDIR /app

COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN go build -o main . && \
    go build -o migrate-qdrant-vectors ./eval/cmd/migrate_qdrant_vectors

FROM alpine:latest

RUN apk --no-cache add ca-certificates
WORKDIR /root/

COPY --from=builder /app/main .
COPY --from=builder /app/migrate-qdrant-vectors .

CMD ["./main"]