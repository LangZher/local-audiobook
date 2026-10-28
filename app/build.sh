#!/bin/bash
# Build "Local Audiobook.app" into ~/Applications so Spotlight finds it (⌘Space, type "Local Audiobook").
# Re-run after moving the repo or changing python. Needs Xcode command line tools (swiftc).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
PY="${PYTHON:-$(command -v python3)}"
DEST="${DEST:-$HOME/Applications}/Local Audiobook.app"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "repo:   $REPO"
echo "python: $PY"
mkdir -p "$TMP/app/Contents/MacOS" "$TMP/app/Contents/Resources"
swiftc -O -o "$TMP/app/Contents/MacOS/LocalAudiobook" "$HERE/main.swift" -framework AppKit -framework WebKit
swift "$HERE/icon.swift" "$TMP/AppIcon.iconset"
iconutil -c icns "$TMP/AppIcon.iconset" -o "$TMP/app/Contents/Resources/AppIcon.icns"

esc() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g'; }
cat > "$TMP/app/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Local Audiobook</string>
  <key>CFBundleDisplayName</key><string>Local Audiobook</string>
  <key>CFBundleIdentifier</key><string>io.github.langzher.local-audiobook</string>
  <key>CFBundleExecutable</key><string>LocalAudiobook</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
  <key>AudiobookRepo</key><string>$(esc "$REPO")</string>
  <key>AudiobookPython</key><string>$(esc "$PY")</string>
  <key>AudiobookPath</key><string>$(esc "$PATH")</string>
</dict></plist>
EOF
codesign --force --sign - "$TMP/app" >/dev/null
mkdir -p "$(dirname "$DEST")"
rm -rf "$DEST"
mv "$TMP/app" "$DEST"
mdimport "$DEST" 2>/dev/null || true
echo "built: $DEST"
