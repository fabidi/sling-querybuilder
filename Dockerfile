FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

EXPOSE 8081

ENTRYPOINT ["sling-querybuilder"]
CMD ["gateway", "--sling-url", "http://sling:8080", "--host", "0.0.0.0", "--port", "8081"]
