#!/bin/sh
# ---------------------------------------------------------------------------
# Container entrypoint.
#
# `app.main` only calls `create_all()` outside deployed environments, so a
# production container starts against whatever schema already exists. Platforms
# without a release/pre-deploy hook -- Render's free plan among them -- have
# nowhere else to run migrations, so the container does it itself when asked.
#
# Off by default: docker-compose runs a dedicated `migrate` service and both the
# API and worker wait on it, and having every replica race to migrate on boot is
# not something to opt into silently.
# ---------------------------------------------------------------------------
set -e

if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
    echo "entrypoint: applying database migrations (alembic upgrade head)"
    alembic upgrade head
    echo "entrypoint: migrations applied"
fi

exec "$@"
