#!/usr/bin/env bash
# Forced SSH command: accepts a release archive on stdin, never an arbitrary shell.
set -Eeuo pipefail
umask 077
if [[ "${SSH_ORIGINAL_COMMAND:-}" =~ ^deploy\ ([0-9a-f]{40})$ ]]; then
    release=${BASH_REMATCH[1]}
else
    echo 'Only deploy <40-character commit SHA> is accepted' >&2
    exit 2
fi
root=/opt/inspiration
mkdir -p "$root/releases"
exec 9> "$root/deploy.lock"
flock -n 9 || { echo 'Another deployment is running'; exit 1; }
work=$(mktemp -d "$root/releases/.incoming-XXXXXX")
trap 'rm -rf -- "$work"' EXIT
cat > "$work/bundle.tgz"
# Validate exact members and pinned repositories before touching a running release.
python3 "$root/release_manifest.py" unpack "$work/bundle.tgz" "$work" "$release"
rm "$work/bundle.tgz"
if [[ -f "$work/release-images.env" ]]; then
    set -a
    source "$work/release-images.env"
    set +a
    export DOCKER_CONFIG="$work/docker-auth"
    # Pull while the previous version still serves traffic. Retries only download layers.
    for image in "$API_IMAGE" "$WEB_IMAGE"; do
        pulled=false
        for attempt in 1 2 3; do
            if docker pull "$image"; then pulled=true; break; fi
            sleep "$((attempt * 2))"
        done
        "$pulled" || exit 1
    done
    rm -rf -- "$work/docker-auth"
    unset DOCKER_CONFIG
fi
target="$root/releases/$release"
mkdir -p "$target"
for file in compose.yml nginx.conf deploy.sh images.tar.gz release.json release-images.env; do
    if [[ -f "$work/$file" ]]; then mv "$work/$file" "$target/$file"; fi
done
bash "$target/deploy.sh" "$release"
