# Production Dockerfile for GridWise LLM-Assisted Smart Campus Energy Optimization Service
# Compatible with Hugging Face Spaces (Docker SDK), Docker Hub, and local containers.
FROM python:3.12-slim

# Install system dependencies including coinor-cbc solver if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    coinor-cbc \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=7860 \
    HOST=0.0.0.0

# Copy dependency specifications
COPY pyproject.toml requirements.txt ./

# Install Python dependencies using uv
RUN uv pip install --system --no-cache -r requirements.txt

# Copy application code and verification artifacts
COPY app/ ./app/
COPY test_samples.py ./
COPY BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json ./

# Ensure non-root user execution if running on Hugging Face Spaces
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app
USER appuser

# Expose service port
EXPOSE 7860

# Health check
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:7860/health || exit 1

# Start the FastAPI service via uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
