FROM golang:1.25.9-alpine AS build
WORKDIR /src
COPY s0/agent-registry-service/go.mod s0/agent-registry-service/go.sum ./s0/agent-registry-service/
WORKDIR /src/s0/agent-registry-service
RUN go mod download
WORKDIR /src
COPY s0/agent-registry-service/ ./s0/agent-registry-service/
WORKDIR /src/s0/agent-registry-service
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /out/are-agent-registry ./cmd/are-agent-registry

FROM gcr.io/distroless/static-debian12
COPY --from=build /out/are-agent-registry /usr/local/bin/are-agent-registry
EXPOSE 9090 8080 8081
ENTRYPOINT ["/usr/local/bin/are-agent-registry"]
