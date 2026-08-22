# =============================================================================
# Proposal Studio — Dockerfile
# =============================================================================
# Build:  docker build -t proposal-studio:latest .
# Run:    docker compose up -d proposal

FROM python:3.12-slim

WORKDIR /app

# Install system deps (Typst needs some libs)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directory
RUN mkdir -p /app/data /app/output

# Expose port
EXPOSE 5002

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:5002/health || exit 1

# Start the Flask app
CMD ["python", "app.py"]