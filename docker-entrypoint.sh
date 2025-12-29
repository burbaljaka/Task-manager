#!/bin/bash
set -e

# Set data directory for database (mounted volume)
export DATA_DIR=/app/data

# Ensure data directory exists
mkdir -p /app/data
chmod 755 /app/data || true

# Ensure database file exists and has proper permissions
DB_FILE="/app/data/db.sqlite3"
if [ ! -f "$DB_FILE" ]; then
    echo "Creating database file at $DB_FILE..."
    touch "$DB_FILE"
    chmod 666 "$DB_FILE" || true
else
    echo "Database file already exists at $DB_FILE"
    chmod 666 "$DB_FILE" || true
fi

# Ensure media directory exists
mkdir -p /app/media
chmod 755 /app/media || true

# Run migrations
echo "Running migrations..."
python manage.py migrate --noinput

# Collect static files (if needed)
echo "Collecting static files..."
python manage.py collectstatic --noinput || true

# Start server
echo "Starting Django development server..."
exec python manage.py runserver 0.0.0.0:8000

