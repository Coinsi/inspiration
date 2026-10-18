#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
release=${1:?commit SHA required}
[[ "$release" =~ ^[0-9a-f]{40}$ ]] || exit 2
root=/opt/inspiration
release_dir="$root/releases/$release"
cd "$release_dir"
export RELEASE="$release"
compose=(docker compose --env-file "$root/runtime.env" -f "$release_dir/compose.yml")
"${compose[@]}" config --quiet
# Leave enough headroom for database backup and uploaded media.
available=$(df -Pk "$root" | awk 'NR==2 {print $4}')
(( available >= 3145728 )) || { echo 'Less than 3 GiB free; deployment stopped'; exit 1; }
docker load -i images.tar.gz
rm images.tar.gz
"${compose[@]}" up -d --wait postgres redis minio
mkdir -p "$root/backups"
previous=$(readlink -f "$root/current" 2>/dev/null || true)
if [[ -n "$previous" && -f "$previous/compose.yml" ]]; then
    "${compose[@]}" stop -t 120 api worker web
    "${compose[@]}" exec -T postgres pg_dump -U inspiration -d inspiration -Fc > "$root/backups/before-$release.dump"
fi
if ! "${compose[@]}" run --rm --no-deps api alembic upgrade head; then
    echo 'Database migration failed. No automatic database restore was attempted.'
    echo 'Services remain stopped for inspection; backups are in /opt/inspiration/backups.'
    exit 1
fi
"${compose[@]}" up -d --wait --wait-timeout 240
"${compose[@]}" exec -T api python -c "from app.core.crypto import encrypt,decrypt; assert decrypt(encrypt('deployment-check')) == 'deployment-check'"
ln -sfn "$release_dir" "$root/current"
printf '%s\n' "$release" > "$root/current-release"
"${compose[@]}" ps
echo "Deployment healthy: $release"
# Keep the two most recent application image versions; never prune data volumes.
for repo in inspiration-api inspiration-web; do
    mapfile -t old < <(docker image ls "$repo" --format '{{.Repository}}:{{.Tag}}' | tail -n +3)
    for image in "${old[@]}"; do docker image rm "$image" || true; done
done
find "$root/backups" -maxdepth 1 -type f -name 'before-*.dump' -mtime +7 -delete
