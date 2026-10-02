#!/bin/sh
# Run pending migrations only when asked (the API service sets RUN_MIGRATIONS=1;
# on Fly the release command migrates instead). Then exec the service command.
set -e

if [ "$RUN_MIGRATIONS" = "1" ]; then
    echo "running migrations..."
    python -m knowledge_desk.migrate
fi

exec "$@"
