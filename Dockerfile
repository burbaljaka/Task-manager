# Use Python 3.13 slim image (PEP 604 unions, Django 5.1+)
FROM python:3.13-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Reduce flaky CI/build failures when PyPI is slow (ReadTimeoutError on large wheels).
ENV PIP_DEFAULT_TIMEOUT=300

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY learning_users/requirements.txt /app/
RUN pip install --no-cache-dir --retries 15 --default-timeout=300 --prefer-binary -r requirements.txt

# Copy project
COPY learning_users/ /app/

# Create directories for data, media and static files
RUN mkdir -p /app/data /app/media /app/static /app/staticfiles

# Copy entrypoint script
COPY docker-entrypoint.sh /app/
RUN chmod +x /app/docker-entrypoint.sh

# Expose port
EXPOSE 8000

# Use entrypoint script
ENTRYPOINT ["/app/docker-entrypoint.sh"]

