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
# Reject extra paths, links and duplicate members before extraction.
python3 - "$work/bundle.tgz" <<'PY'
import sys, tarfile
with tarfile.open(sys.argv[1], 'r:gz') as archive:
    members = archive.getmembers()
    expected = {'compose.yml', 'nginx.conf', 'deploy.sh', 'images.tar.gz'}
    if len(members) != 4 or {m.name for m in members} != expected or not all(m.isfile() for m in members):
        raise SystemExit('Invalid release archive')
PY
tar -xzf "$work/bundle.tgz" -C "$work" --no-same-owner --no-same-permissions
rm "$work/bundle.tgz"
target="$root/releases/$release"
mkdir -p "$target"
for file in compose.yml nginx.conf deploy.sh images.tar.gz; do
    mv "$work/$file" "$target/$file"
done
bash "$target/deploy.sh" "$release"
