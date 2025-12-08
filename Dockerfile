FROM python:3.9-slim

WORKDIR /app

# Install system dependencies if needed (e.g. for pyarrow/pandas build)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ src/

# Create data directory structure (but not the data itself, which will be mounted)
RUN mkdir -p data/raw data/processed data/gold

# Default command
CMD ["python", "-m", "src.fetch_sepa_range", "--type", "minorista"]
