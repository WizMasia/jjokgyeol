#!/usr/bin/env bash
set -euo pipefail

if [[ ${#} -ne 2 ]]; then
  echo 'usage: bash desktop/release/linux.sh EXTRACTED_RESOURCES OUTPUT_DIR' >&2
  exit 2
fi
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || {
  echo 'Linux release packages must be built on x86_64.' >&2
  exit 1
}

resources=$(cd "$1" && pwd)
mkdir -p "$2"
output=$(cd "$2" && pwd)
root=$(cd "$(dirname "$0")/../.." && pwd)
cd "$root"

for command in cargo python dpkg-deb dpkg-shlibdeps readelf apt-get dpkg-query xvfb-run dbus-run-session; do
  command -v "$command" >/dev/null || { echo "required command is missing: $command" >&2; exit 1; }
done
[[ -f desktop/gui/frontend/dist/index.html ]] || {
  echo 'build desktop/gui/frontend/dist before packaging.' >&2
  exit 1
}

if [[ -z ${CARGO_TARGET_DIR:-} ]]; then
  export CARGO_TARGET_DIR="$root/desktop/gui/target"
elif [[ $CARGO_TARGET_DIR != /* ]]; then
  export CARGO_TARGET_DIR="$root/$CARGO_TARGET_DIR"
else
  export CARGO_TARGET_DIR
fi
version=$(python -c 'import json; print(json.load(open("desktop/gui/tauri.conf.json"))["version"])')
product_name=$(python -c 'import json; print(json.load(open("desktop/gui/tauri.conf.json"))["productName"])')
artifact="$output/jjokgyeol-linux-x86_64.deb"
work=$(mktemp -d)
install_started=0
cleanup() {
  if [[ $install_started == 1 ]] && dpkg-query -W -f='${db:Status-Status}' jjokgyeol 2>/dev/null | grep -qx installed; then
    if [[ $EUID -eq 0 ]]; then
      apt-get purge -y jjokgyeol >/dev/null || echo 'warning: could not remove test installation.' >&2
    else
      sudo apt-get purge -y jjokgyeol >/dev/null || echo 'warning: could not remove test installation.' >&2
    fi
  fi
  rm -rf "$work"
}
trap cleanup EXIT

cargo build --locked --release --features tauri/custom-protocol --manifest-path desktop/gui/Cargo.toml
gui="$CARGO_TARGET_DIR/release/jjokgyeol-desktop"
[[ -x $gui ]] || { echo "release GUI binary is missing: $gui" >&2; exit 1; }
readelf -h "$gui" | grep -Eq 'Machine:.*X86-64' || {
  echo "release GUI binary is not x86_64: $gui" >&2
  exit 1
}
cargo build --locked --release --manifest-path desktop/Cargo.toml
cli="$CARGO_TARGET_DIR/release/jjokgyeol-proof"
[[ -x $cli ]] || { echo "release proof CLI is missing: $cli" >&2; exit 1; }

runtime="$work/runtime"
python desktop/release/stage_unix.py "$resources" "$runtime"
python -m PyInstaller --clean --noconfirm \
  --distpath "$work/dist" --workpath "$work/pyinstaller" desktop/worker/worker.spec
[[ -x $work/dist/jjokgyeol-worker/jjokgyeol-worker ]] || {
  echo 'PyInstaller worker was not created.' >&2
  exit 1
}
mkdir -p "$runtime/worker"
cp -a "$work/dist/jjokgyeol-worker/." "$runtime/worker/"

package="$work/package"
app_dir="$package/usr/lib/$product_name"
mkdir -p "$app_dir" "$package/usr/bin" \
  "$package/usr/share/applications" "$package/usr/share/icons/hicolor/32x32/apps" \
  "$package/DEBIAN"
install -m 0755 "$gui" "$app_dir/jjokgyeol-desktop"
cp -a "$runtime" "$app_dir/runtime"
ln -s "../lib/$product_name/jjokgyeol-desktop" "$package/usr/bin/jjokgyeol"
install -m 0644 desktop/gui/icons/icon.png "$package/usr/share/icons/hicolor/32x32/apps/jjokgyeol.png"
cat > "$package/usr/share/applications/jjokgyeol.desktop" <<'DESKTOP'
[Desktop Entry]
Name=쪽결
Comment=한글 출판 조판기
Exec=jjokgyeol
Icon=jjokgyeol
Terminal=false
Type=Application
Categories=Office;WordProcessor;
DESKTOP

engine="$app_dir/runtime/texlive/bin/x86_64-linux/luahbtex"
mkdir -p "$work/debian"
cat > "$work/debian/control" <<'SOURCE_CONTROL'
Source: jjokgyeol
Section: office
Priority: optional
Maintainer: JJokgyeol maintainers
Standards-Version: 4.6.2

Package: jjokgyeol
Architecture: any
Description: Korean publication typesetting editor
SOURCE_CONTROL
shlib_output=$(cd "$work" && dpkg-shlibdeps -O -e "$app_dir/jjokgyeol-desktop" -e "$engine")
dependencies=$(printf '%s\n' "$shlib_output" | sed -n 's/^shlibs:Depends=//p')
[[ -n $dependencies ]] || { echo 'dpkg-shlibdeps returned no runtime dependencies.' >&2; exit 1; }
for required in libgtk-3-0t64 libwebkit2gtk-4.1-0 libayatana-appindicator3-1 librsvg2-2; do
  case ",$dependencies," in
    *,"$required",*) ;;
    *) dependencies="$dependencies, $required" ;;
  esac
done
cat > "$package/DEBIAN/control" <<CONTROL
Package: jjokgyeol
Version: $version
Section: office
Priority: optional
Architecture: amd64
Maintainer: JJokgyeol maintainers
Depends: $dependencies
Description: Korean publication typesetting editor
 쪽결 is an offline desktop editor for Korean publication typesetting.
CONTROL

dpkg-deb --build --root-owner-group "$package" "$work/jjokgyeol-linux-x86_64.deb"
[[ $(dpkg-deb -f "$work/jjokgyeol-linux-x86_64.deb" Architecture) == amd64 ]]

if dpkg-query -W -f='${db:Status-Status}' jjokgyeol 2>/dev/null | grep -qx installed; then
  echo 'jjokgyeol is already installed; use a clean Ubuntu 24.04 runner.' >&2
  exit 1
fi
install_started=1
if [[ $EUID -eq 0 ]]; then
  apt-get install -y "$work/jjokgyeol-linux-x86_64.deb"
else
  sudo apt-get install -y "$work/jjokgyeol-linux-x86_64.deb"
fi
[[ -x /usr/bin/jjokgyeol && -f "/usr/lib/$product_name/runtime/runtime.json" ]]
smoke="$work/smoke"
mkdir -p "$smoke"
export WEBKIT_DISABLE_COMPOSITING_MODE=1 GDK_BACKEND=x11 XDG_SESSION_TYPE=x11 XDG_CURRENT_DESKTOP=Openbox
xvfb-run -a dbus-run-session -- python desktop/qa/linux/smoke.py /usr/bin/jjokgyeol "$smoke"
"$cli" --runtime "/usr/lib/$product_name/runtime" \
  --source proofs/numbering/manuscript.md --style proofs/numbering/odd-even.json \
  --output "$work/odd-even.pdf"
python desktop/qa/compare_pdfs.py "$work/odd-even.pdf" proofs/numbering/odd-even.pdf
python desktop/qa/verify_paragraph_spacing.py "/usr/lib/$product_name/runtime"
if [[ $EUID -eq 0 ]]; then
  apt-get purge -y jjokgyeol >/dev/null
else
  sudo apt-get purge -y jjokgyeol >/dev/null
fi
install_started=0
mv "$work/jjokgyeol-linux-x86_64.deb" "$artifact"
printf 'Created %s\n' "$artifact"
