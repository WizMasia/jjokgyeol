#!/usr/bin/env bash
set -euo pipefail
[[ $(uname -s) == Darwin && $(uname -m) == arm64 ]]
resources=$(cd "${1:?expected extracted resources}" && pwd)
out=$(cd "${2:?expected output directory}" && pwd)
cd "$(dirname "$0")/../.."
# macOS resource lookup rejects launch paths containing symlinks such as /var.
work=$(cd "$(mktemp -d)" && pwd -P)
mount="$work/mounted"
pid=
app='쪽결.app'
cleanup() {
  if [[ -n $pid ]]; then kill "$pid" 2>/dev/null || true; fi
  if [[ -d "$mount/$app" ]]; then hdiutil detach "$mount"; fi
  rm -rf "$work"
}
trap cleanup EXIT
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$work/target}"
python desktop/release/stage_unix.py "$resources" "$work/runtime"
python -m PyInstaller --clean --noconfirm --distpath "$work/dist" --workpath "$work/build" desktop/worker/worker.spec
ditto "$work/dist/jjokgyeol-worker" "$work/runtime/worker"
: "${JJOKGYEOL_RELEASE_TAG:?expected release tag}"
python desktop/gui/bundle.py "$work/runtime"

mkdir -p "$work/image" "$mount" "$work/installed"
ditto "$CARGO_TARGET_DIR/release/bundle/macos/$app" "$work/image/$app"
codesign --force --deep --sign - "$work/image/$app"
ln -s /Applications "$work/image/Applications"
dmg=jjokgyeol-macos-arm64.dmg
hdiutil create -fs HFS+ -volname '쪽결' -srcfolder "$work/image" -format UDZO "$work/$dmg"
hdiutil verify "$work/$dmg"
hdiutil attach -readonly -nobrowse -mountpoint "$mount" "$work/$dmg"
installed="$work/installed/$app"
ditto "$mount/$app" "$installed"
hdiutil detach "$mount"
codesign --verify --deep --strict "$installed"
plist="$installed/Contents/Info.plist"
version=${JJOKGYEOL_RELEASE_TAG#v}
[[ $(plutil -extract CFBundleIdentifier raw "$plist") == kr.jjokgyeol.app ]]
[[ $(plutil -extract CFBundleShortVersionString raw "$plist") == "${version%-preview.*}" ]]
[[ $(plutil -extract CFBundleVersion raw "$plist") == "${version##*.}" ]]
unset JJOKGYEOL_NATIVE_RUNTIME
"$installed/Contents/MacOS/jjokgyeol-desktop" &
pid=$!
sleep 5
kill -0 "$pid"
kill "$pid"
wait "$pid" || true
pid=
cargo run --locked --release --manifest-path desktop/Cargo.toml -- \
  --runtime "$installed/Contents/Resources/runtime" --source proofs/numbering/manuscript.md \
  --style proofs/numbering/odd-even.json --output "$work/odd-even.pdf"
python desktop/qa/compare_pdfs.py "$work/odd-even.pdf" proofs/numbering/odd-even.pdf
python desktop/qa/verify_paragraph_spacing.py "$installed/Contents/Resources/runtime"
mv "$work/$dmg" "$out/$dmg"
