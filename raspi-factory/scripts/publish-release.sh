#!/bin/bash
# publish-release.sh — cut a GitHub release for the Sudo factory bundle.
#
# Releases are how devices update themselves: sudo-update.sh on the Pi asks
# GitHub for the latest release, downloads the bundle asset, checks it against
# the .sha256 asset, and applies it. So this script's whole job is to make that
# pair of assets exist, attached to a correctly-tagged release.
#
# Run it from the repo (or pass the repo root). It is deliberately small and
# dependency-free -- git, tar and curl only -- so it works on any dev machine.
#
#   scripts/publish-release.sh                 # tag raspi-factory-v$VERSION
#   scripts/publish-release.sh --repo owner/name
#   scripts/publish-release.sh --dry-run       # build + checksum, publish nothing
#
# The tag is prefixed (raspi-factory-v) so this one public repo can later host
# releases for more than one product without their "latest" colliding. The
# updater strips the prefix when comparing versions.
set -euo pipefail

TAG_PREFIX="${SUDO_UPDATE_TAG_PREFIX:-raspi-factory-v}"
ASSET="sudo-factory.tar.gz"
SHA_ASSET="sudo-factory.tar.gz.sha256"

REPO_SLUG="${SUDO_UPDATE_REPO:-evydeveloper-rgb/sudospacesourcecode}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO_SLUG="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "usage: publish-release.sh [--repo owner/name] [--dry-run]" >&2; exit 2 ;;
  esac
done

[ -f "$ROOT/VERSION" ] || { echo "ERROR: no VERSION file at $ROOT" >&2; exit 1; }
VERSION="$(tr -d ' \t\r\n' < "$ROOT/VERSION")"
case "$VERSION" in
  [0-9]*.[0-9]*.[0-9]*) ;;
  *) echo "ERROR: VERSION '$VERSION' is not x.y.z" >&2; exit 1 ;;
esac
TAG="${TAG_PREFIX}${VERSION}"

# Token: prefer an explicit env var, else reuse the one the git remote already
# carries (the same credential that can push, so it can also release).
token() {
  if [ -n "${GITHUB_TOKEN:-}" ]; then printf '%s' "$GITHUB_TOKEN"; return; fi
  git -C "$ROOT" remote get-url origin 2>/dev/null \
    | sed -n 's#https://[^:]*:\([^@]*\)@github.com/.*#\1#p'
}
TOKEN="$(token || true)"

echo "Repo:    $REPO_SLUG"
echo "Version: $VERSION  (tag $TAG)"
echo "Bundle:  $ROOT"

# ── Build the bundle ────────────────────────────────────────────────────────
# Root the tarball at a single directory named after this bundle
# (raspi-factory/). sudo-update.sh finds that directory whatever it is called,
# but keeping it stable makes the artifacts predictable.
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
BUNDLE_PARENT="$WORK/bundle"
mkdir -p "$BUNDLE_PARENT"
BUNDLE_NAME="$(basename "$ROOT")"

echo "Building $ASSET…"
tar -czf "$WORK/$ASSET" \
  --exclude='.git' \
  --exclude='node_modules' \
  --exclude='*.tar.gz' \
  --exclude='.DS_Store' \
  -C "$(dirname "$ROOT")" "$BUNDLE_NAME"

( cd "$WORK" && sha256sum "$ASSET" > "$SHA_ASSET" )
echo "SHA256:  $(awk '{print $1}' "$WORK/$SHA_ASSET")"

if [ "$DRY_RUN" = "1" ]; then
  echo "Dry run — wrote $WORK/$ASSET (not publishing)."
  cp "$WORK/$ASSET" "$WORK/$SHA_ASSET" "$ROOT/" 2>/dev/null || true
  trap - EXIT
  echo "Kept copies at $ROOT/$ASSET"
  exit 0
fi

[ -n "$TOKEN" ] || { echo "ERROR: no GitHub token (set GITHUB_TOKEN, or configure the origin remote)" >&2; exit 1; }

# ── Create the release (or reuse it) ────────────────────────────────────────
api() { curl -fsSL -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json" "$@"; }

release_id="$(api "https://api.github.com/repos/$REPO_SLUG/releases/tags/$TAG" 2>/dev/null \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))' 2>/dev/null || true)"

if [ -z "$release_id" ]; then
  echo "Creating release $TAG…"
  release_id="$(api -X POST "https://api.github.com/repos/$REPO_SLUG/releases" \
    -d "$(python3 -c 'import json,sys; print(json.dumps({"tag_name": sys.argv[1], "name": sys.argv[1], "target_commitish": "main", "generate_release_notes": True}))' "$TAG")" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))')"
  [ -n "$release_id" ] || { echo "ERROR: release creation failed" >&2; exit 1; }
else
  echo "Release $TAG already exists (id $release_id) — replacing assets."
fi

# ── Attach the assets ───────────────────────────────────────────────────────
upload() {
  local file="$1" name="$2"
  local url="https://uploads.github.com/repos/$REPO_SLUG/releases/$release_id/assets?name=$name"
  # Remove a same-named asset first so re-publishing is idempotent.
  local existing
  existing="$(api "https://api.github.com/repos/$REPO_SLUG/releases/$release_id/assets" \
    | python3 -c 'import json,sys; n=sys.argv[1]; [print(a["id"]) for a in json.load(sys.stdin) if a.get("name")==n]' "$name" 2>/dev/null || true)"
  for id in $existing; do
    curl -fsSL -X DELETE -H "Authorization: Bearer $TOKEN" \
      "https://api.github.com/repos/$REPO_SLUG/releases/assets/$id" >/dev/null || true
  done
  curl -fsSL -X POST -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/gzip" --data-binary "@$file" "$url" >/dev/null
}

echo "Uploading $ASSET…"
upload "$WORK/$ASSET" "$ASSET"
echo "Uploading $SHA_ASSET…"
upload "$WORK/$SHA_ASSET" "$SHA_ASSET"

echo
echo "Published $TAG."
echo "  https://github.com/$REPO_SLUG/releases/tag/$TAG"
