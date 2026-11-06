#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
release=${1:?commit SHA required}
[[ "$release" =~ ^[0-9a-f]{40}$ ]] || exit 2
root=/opt/inspiration
release_dir="$root/releases/$release"
cd "$release_dir"
export RELEASE="$release"
if [[ -f release-images.env ]]; then
    set -a
    source release-images.env
    set +a
fi
compose=(docker compose --env-file "$root/runtime.env" -f "$release_dir/compose.yml")
"${compose[@]}" config --quiet
# Leave enough headroom for database backup and uploaded media.
available=$(df -Pk "$root" | awk 'NR==2 {print $4}')
(( available >= 3145728 )) || { echo 'Less than 3 GiB free; deployment stopped'; exit 1; }
if [[ -f images.tar.gz ]]; then
    docker load -i images.tar.gz
    rm images.tar.gz
else
    docker image inspect "${API_IMAGE:-inspiration-api:$release}" "${WEB_IMAGE:-inspiration-web:$release}" inspiration-minio:source-7aac2a2c5b7c >/dev/null
fi
"${compose[@]}" up -d --wait postgres redis minio
mkdir -p "$root/backups"
previous=$(readlink -f "$root/current" 2>/dev/null || true)
backend_changed=true
running_api=$(docker inspect inspiration-production-api-1 --format '{{.Config.Image}}' 2>/dev/null || true)
if [[ "$running_api" == "${API_IMAGE:-inspiration-api:$release}" ]]; then backend_changed=false; fi
schema_changed=true
if [[ -f release.json && -f "$previous/release.json" ]]; then
    if python3 - "$previous/release.json" release.json <<'PY'
import json, sys
old, new = (json.load(open(p)) for p in sys.argv[1:])
sys.exit(0 if old['schema'] == new['schema'] else 1)
PY
    then schema_changed=false; fi
fi
# A failed update without schema changes can safely restart the previous application.
rollback() {
    status=$?
    trap - ERR
    if [[ "$schema_changed" == false && -n "$previous" && -f "$previous/compose.yml" && "$previous" != "$release_dir" ]]; then
        echo 'Health/update failed; restoring previous application images (database unchanged).'
        (
            unset API_IMAGE WEB_IMAGE
            export RELEASE=$(basename "$previous")
            if [[ -f "$previous/release-images.env" ]]; then set -a; source "$previous/release-images.env"; set +a; fi
            docker compose --env-file "$root/runtime.env" -f "$previous/compose.yml" up -d --wait --wait-timeout 240
        ) || echo 'Automatic application rollback failed; inspect services.' >&2
    fi
    exit "$status"
}
trap rollback ERR
if [[ -n "$previous" && -f "$previous/compose.yml" && ( "$backend_changed" == true || "$schema_changed" == true ) ]]; then
    # Frontend-only updates leave API and long-running workers alone.
    if [[ "$schema_changed" == true ]]; then
        "${compose[@]}" stop -t 120 api worker web
    else
        "${compose[@]}" stop -t 120 worker
    fi
    "${compose[@]}" exec -T postgres pg_dump -U inspiration -d inspiration -Fc > "$root/backups/before-$(date -u +%Y%m%dT%H%M%SZ)-$release.dump"
fi
if [[ "$schema_changed" == true ]]; then
    if ! "${compose[@]}" run --rm --no-deps api alembic upgrade head; then
        echo 'Database migration failed. No automatic database restore was attempted.'
        echo 'Services remain stopped for inspection; backups are in /opt/inspiration/backups.'
        exit 1
    fi
    "${compose[@]}" run --rm --no-deps api python -m app.maintenance migrate-fs-to-minio
fi
"${compose[@]}" up -d --wait --wait-timeout 240
"${compose[@]}" exec -T api python -c "from app.core.crypto import encrypt,decrypt; assert decrypt(encrypt('deployment-check')) == 'deployment-check'"
ln -sfn "$release_dir" "$root/current"
printf '%s\n' "$release" > "$root/current-release"
"${compose[@]}" ps
echo "Deployment healthy: $release"
trap - ERR
# Keep the two most recent application image versions; never prune data volumes.
for repo in inspiration-api inspiration-web; do
    mapfile -t old < <(docker image ls "$repo" --format '{{.Repository}}:{{.Tag}}' | tail -n +3)
    for image in "${old[@]}"; do docker image rm "$image" || true; done
done
find "$root/backups" -maxdepth 1 -type f -name 'before-*.dump' -mtime +7 -delete
