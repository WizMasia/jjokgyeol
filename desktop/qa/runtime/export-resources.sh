#!/bin/sh
# Run from the repository root on a Docker host; output is a portable resource tar.
set -eu
output=${1:?usage: export-resources.sh OUTPUT.tar}
docker build --platform linux/amd64 --target engine -t jjokgyeol-proof-resources . >&2
docker run --rm --platform linux/amd64 --entrypoint sh jjokgyeol-proof-resources -c '
  set -eu
  mkdir -p /tmp/resources/texmf-dist /tmp/resources/nanum
  for dir in tex fonts web2c scripts; do cp -aL /usr/share/texlive/texmf-dist/$dir /tmp/resources/texmf-dist/; done
  for dir in tex fonts; do cp -aL /usr/share/texmf/$dir/. /tmp/resources/texmf-dist/$dir/; done
  cp -aL /var/lib/texmf/tex/. /tmp/resources/texmf-dist/tex/
  cp -aL /usr/share/fonts/truetype/nanum/. /tmp/resources/nanum/
  notices=/tmp/resources/licenses/debian
  mkdir -p "$notices"
  cp -RL /usr/share/common-licenses "$notices/"
  dpkg-query -W > "$notices/packages.tsv"
  for doc in /usr/share/doc/*; do
    if [ -f "$doc/copyright" ]; then cp -L "$doc/copyright" "$notices/${doc##*/}.copyright"; fi
  done
  tar -C /tmp/resources -cf - texmf-dist nanum licenses
' > "$output"
