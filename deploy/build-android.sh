#!/bin/bash
# Pull the latest muqeet1982/dms-android, build the arm64 release APK on this host and
# put it on the release channel (POST /api/v1/platform/app-releases).
#
# NOT YET RUN END-TO-END: a first build needs roughly 4 GB free (node_modules + Gradle
# caches) and this disk sat at 94% on 26 Oct 2026, so the attempt was deferred. The disk
# guard below aborts rather than fill the disk the live Postgres lives on.
#
# Signing: the repo ships no keystore, so Gradle signs with the stock React Native debug
# keystore — the SAME key every release so far was signed with (cert SHA-256
# fac61745dc0903786fb9ede62a962b399f7348f0bb6f899b8332667591033b9c), which the fingerprint
# check below enforces. A mismatch would make every installed phone refuse the update.
#
# Usage: ADMIN_PASSWORD=... ./deploy/build-android.sh ["release notes"]
#   NOTES_FILE=path          notes from a file instead of the argument
#   SKIP_UPLOAD=1            build + verify only
set -euo pipefail

SRC=${APP_SRC:-/var/www/dms-android}
BASE=${BASE_URL:-https://dms.invoiceflow.pk}
ADMIN_USER=${ADMIN_USER:-admin}
MIN_FREE_MB=${MIN_FREE_MB:-800}
EXPECTED_CERT=fac61745dc0903786fb9ede62a962b399f7348f0bb6f899b8332667591033b9c
export ANDROID_HOME=${ANDROID_HOME:-/opt/android-sdk}

guard() {
  local free
  free=$(df -m / | awk 'NR==2{print $4}')
  if [ "$free" -lt "$MIN_FREE_MB" ]; then
    echo "ABORT at '$1': ${free} MB free is under ${MIN_FREE_MB} MB — the live DB shares this disk." >&2
    exit 1
  fi
}

guard start
cd "$SRC"
git pull --ff-only
VERSION=$(python3 -c "import json; print(json.load(open('app.json'))['expo']['version'])")
BUILD=$(python3 -c "import json; print(json.load(open('app.json'))['expo']['android']['versionCode'])")
echo "== Building $VERSION (build $BUILD) from $(git rev-parse --short HEAD): $(git log -1 --format=%s)"

npm ci --no-audit --no-fund
guard "after npm ci"
npx expo prebuild --platform android --no-install
guard "after prebuild"
(cd android && ./gradlew assembleRelease -PreactNativeArchitectures=arm64-v8a --max-workers=1)

APK=android/app/build/outputs/apk/release/app-release.apk
[ -f "$APK" ] || { echo "No APK at $APK" >&2; exit 1; }

BT=$(ls -d "$ANDROID_HOME"/build-tools/*/ | sort -V | tail -1)
CERT=$("$BT/apksigner" verify --print-certs "$APK" | awk '/SHA-256 digest/{print $NF; exit}')
if [ "$CERT" != "$EXPECTED_CERT" ]; then
  echo "ABORT: APK signed with $CERT, expected $EXPECTED_CERT — installed phones could not update." >&2
  exit 1
fi
OUT="$SRC/dms-$VERSION-arm64.apk"
cp "$APK" "$OUT"
echo "== Built and signature-verified: $OUT ($(du -h "$OUT" | cut -f1))"

[ "${SKIP_UPLOAD:-}" = "1" ] && exit 0
: "${ADMIN_PASSWORD:?set ADMIN_PASSWORD for the platform admin login}"
NOTES=${1:-$( [ -n "${NOTES_FILE:-}" ] && cat "$NOTES_FILE" || git log -1 --format=%s )}

TOK=$(curl -sf "$BASE/api/v1/auth/login" -H 'Content-Type: application/json' \
  -d "{\"identifier\":\"$ADMIN_USER\",\"password\":\"$ADMIN_PASSWORD\",\"client\":\"web\"}" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['access'])")
curl -sf -X POST "$BASE/api/v1/platform/app-releases" -H "Authorization: Bearer $TOK" \
  -F platform=android -F "version=$VERSION" -F "build_number=$BUILD" -F "notes=$NOTES" \
  -F "file=@$OUT;type=application/vnd.android.package-archive" \
  | python3 -m json.tool | head -12
echo "== On the channel. Check: $BASE/api/v1/app/android"
