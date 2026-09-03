FROM python:3.11-slim

WORKDIR /app

# Create necessary directories
RUN mkdir -p /app/certs /app/logs

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    gcc \
    g++ \
    git \
    curl \
    libssl-dev \
    libffi-dev \
    python3-dev \
    libyaml-dev \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip, setuptools, wheel FIRST
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Install PyYAML with pre-built wheels
RUN pip install --no-cache-dir --only-binary :all: pyyaml==6.0.1

# Copy and install requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY app.py config.py pytest.ini ./

# Copy the LLM gateway package (entire directory)
COPY gateway/ ./gateway/

# Copy utils package (entire directory) — generic LLM-response JSON repair
# used by app.py's legacy /generate-with-image handler (ARCHITECTURE.md §9)
COPY utils/ ./utils/

# Copy tests package (entire directory)
COPY tests/ ./tests/

# Ensure proper permissions
RUN chmod 755 /app/certs /app/logs

EXPOSE 8443

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f https://localhost:8443/health || exit 1

# Run application
CMD ["python", "app.py"]