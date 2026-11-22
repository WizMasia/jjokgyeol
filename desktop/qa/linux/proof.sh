#!/bin/sh
# Run inside the existing linux/amd64 Dockerfile engine stage, with /src and /results mounted.
set -eu
cd /src
[ "$(uname -m)" = x86_64 ] || { echo 'Expected x86_64' >&2; exit 1; }
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends curl ca-certificates gcc libc6-dev
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
export PATH="/root/.cargo/bin:$PATH" CARGO_TARGET_DIR=/tmp/cargo-target
python -m pip install --require-hashes --only-binary=:all: -r desktop/worker/requirements.lock -r desktop/worker/build-requirements.lock
python -m pip install --require-hashes --only-binary=:all: -r desktop/qa/requirements-compare.lock
python -m unittest discover -s desktop/qa -p 'test_*.py'
python -m PyInstaller --clean --noconfirm --distpath /tmp/dist --workpath /tmp/pyinstaller desktop/worker/worker.spec
mkdir -p /tmp/runtime/bin /tmp/runtime/fonts
cp -L /usr/bin/lualatex /tmp/runtime/bin/lualatex
cp -aL /usr/share/fonts/truetype/nanum/. /tmp/runtime/fonts/
cp -a /tmp/dist/jjokgyeol-worker /tmp/runtime/worker
printf '%s\n' '{"engine":"bin/lualatex","font_dir":"fonts"}' > /tmp/runtime/runtime.json
cargo test --locked --manifest-path desktop/Cargo.toml
cargo run --locked --manifest-path desktop/Cargo.toml -- --runtime /tmp/runtime --source proofs/numbering/manuscript.md --style proofs/numbering/odd-even.json --output /results/odd-even.pdf
python desktop/qa/compare_pdfs.py /results/odd-even.pdf proofs/numbering/odd-even.pdf
