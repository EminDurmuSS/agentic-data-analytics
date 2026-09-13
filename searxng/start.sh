#!/bin/sh
set -eu

# This private search service needs no shared or checked-in session secret.
# An operator may supply a stable secret; otherwise generate one per startup.
if [ -z "${SEARXNG_SECRET:-}" ]; then
    SEARXNG_SECRET=$(head -c 32 /dev/urandom | base64)
    export SEARXNG_SECRET
fi

exec /usr/local/searxng/entrypoint.sh
