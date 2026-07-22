# Image for the Streamlit dashboard and the standalone CLI entrypoints.
# Python 3.11: 3.9 could not resolve current pandas/pyarrow/pandera pins.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# curl is used by the container healthcheck; no build toolchain is needed because
# pandas/pyarrow ship manylinux wheels for 3.11.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
# Theme config: Streamlit reads .streamlit/config.toml relative to the CWD.
COPY .streamlit/ .streamlit/

# Run as a non-root user.
RUN useradd --create-home --uid 1000 sepa && chown -R sepa:sepa /app
USER sepa

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8501/_stcore/health || exit 1

# --server.headless suppresses the interactive e-mail prompt that otherwise
# blocks Streamlit's first start inside a container.
CMD ["streamlit", "run", "src/dashboard.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
