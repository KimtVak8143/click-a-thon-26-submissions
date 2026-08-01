FROM otel/opentelemetry-collector-contrib:latest

COPY otel-collector.yaml /etc/otel-collector-config.yaml

EXPOSE 4317 4318 55679

CMD ["--config=/etc/otel-collector-config.yaml"]
