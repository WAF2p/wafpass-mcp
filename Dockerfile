FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir hatchling build

COPY pyproject.toml README.md VERSION ./
COPY wafpass_mcp/ wafpass_mcp/

RUN pip install --no-cache-dir .

EXPOSE 3001

CMD ["wafpass-mcp"]
