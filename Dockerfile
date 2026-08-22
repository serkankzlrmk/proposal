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

# Run with gunicorn for production (fallback to Flask dev server if gunicorn missing)
CMD ["python", "-c", "\
import sys; \
sys.path.insert(0, '.'); \
try:\n    from app import app, HOST, PORT\n    app.run(host=HOST, port=PORT, debug=False, use_reloader=False)\nexcept ImportError:\n    from app import app\n    app.run(host='0.0.0.0', port=5002, debug=False, use_reloader=False)\n"]