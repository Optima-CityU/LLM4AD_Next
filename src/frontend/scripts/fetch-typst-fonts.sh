#!/usr/bin/env bash
#
# Fetch the font files the browser-side Typst preview compiler needs.
#
# The preview compiles in the browser through `@myriaddreamin/typst.ts` (a WASM
# build of Typst), so it never reads system fonts - it only sees what the font
# loader is handed. `typst.ts` defaults to fetching those from a CDN, which means
# the fetch happens in the user's browser: on an intranet or air-gapped
# deployment every request fails and the templates silently fall back to the
# wrong faces. Serving the files from the frontend itself is the only
# arrangement that renders without outbound internet access.
#
# They are not committed to the repository: 16 MB of binaries would live in git
# history forever, and every file is already pinned to an upstream tag. The
# download happens at build time instead, and each file is verified against the
# checksum recorded below so a changed or corrupted asset fails the build rather
# than shipping a preview that renders wrong glyphs.
#
# The script is idempotent - files already present with the expected checksum are
# left alone - so it is cheap to run on every build.
#
# Usage: bash scripts/fetch-typst-fonts.sh   (also available as `bun run fonts`)
#
set -euo pipefail

DEST="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/public/typst-fonts"

TYPST_ASSETS="https://cdn.jsdelivr.net/gh/typst/typst-assets@v0.13.1/files/fonts"
TYPST_DEV_ASSETS="https://cdn.jsdelivr.net/gh/typst/typst-dev-assets@v0.13.1/files/fonts"

# Entries are <file name>:<assets|dev>:<sha256>. `assets` and `dev` are the two
# upstream repositories, which do not hold the same files, so the source of each
# file matters.
FONTS=(
  "DejaVuSansMono-Bold.ttf:assets:bce60f1b4421acd9ea51ba6623d7024ecbe6817a953e3654df62a5e6bdf8f769"
  "DejaVuSansMono-BoldOblique.ttf:assets:91713a71d550bba22c2a6b2bb2a9ad8f9a159e12e4e9f0a5b2677998ba21213e"
  "DejaVuSansMono-Oblique.ttf:assets:742097840c541870e8d6dc5c9b37bb1ceeea6c0dedd1d475faf903ef9df734b0"
  "DejaVuSansMono.ttf:assets:b4a6c3e4faab8773f4ff761d56451646409f29abedd68f05d38c2df667d3c582"
  "LibertinusSerif-Bold.otf:assets:0264914210ed51b3231ebc92ce529e9f2e166ba9eebf0cd4a579558690a27b64"
  "LibertinusSerif-BoldItalic.otf:assets:47a665259f09f554f5d133d7718cdad43ff462c6a6b2328f38023465e62d57ce"
  "LibertinusSerif-Italic.otf:assets:9a393d63d6e05f620d3dc0190dfd35a8ede58c0808cf0fc9de7fcb9c723e4c24"
  "LibertinusSerif-Regular.otf:assets:fcf06307a77367394fcb0ccb241e59eea70dba3d732be309647611224679c733"
  "LibertinusSerif-Semibold.otf:assets:a4b3f28e85881db34695c1f005e4c79233a6caf3a2bd286c9b418c025fb99308"
  "LibertinusSerif-SemiboldItalic.otf:assets:397f0d7aba35ae6a988948ae046c14c6b1e0d270fcc6e292b8bc29fd625c6101"
  "NewCM10-Bold.otf:assets:947931c42ca3a87c369b81d6de98533e0d5e4b2d713950781664644fb7d59724"
  "NewCM10-BoldItalic.otf:assets:0ddd9bab5b7d524602a27604f43e3be5f7c0968f7956b73733f7095f2036f1d2"
  "NewCM10-Italic.otf:assets:70c9c811bb0e4bcb36d1f18e364cd770cc5b781f36f127506e2bd46be19376d1"
  "NewCM10-Regular.otf:assets:2f751e3082cee78652f6f07f1614e653d2b395b33a0834b324990e354e31d077"
  "NewCMMath-Bold.otf:assets:8956f7ef6c212ea647fe689abb66a3f3359126abcbb673fb58f3ecf033c7d1a7"
  "NewCMMath-Book.otf:assets:b2e655d5cae5ab9569fa6f5d94e929156fe70bd3548d916b4b23d1a1c1a55693"
  "NewCMMath-Regular.otf:assets:bfd2f9b22caacd41b8b29cfe3f6d72f976f221a65362e07eda35cbae863721b7"
  "IBMPlexSans-Regular.ttf:dev:852def7e24f7b71bab6e8a5c9b02b203e45b0ef59697feaf116e7e8091ad7a2a"
  "IBMPlexSerif-Regular.ttf:dev:29f1b4fbda490747553b47780feb1b73832c48581894ebf1caa98959a024fdc9"
  "InriaSerif-Bold.ttf:dev:ac09cfde8d529b2cd63bd7f417556aa2c42ca17f5df6a022faf91cff43570609"
  "InriaSerif-BoldItalic.ttf:dev:d7c140cdb61a4c1edca17abed1d9d57fe5b345d0195d58129c98e61e8cc30f9b"
  "InriaSerif-Italic.ttf:dev:98422fd7b83cdfe3f9d23dabd60defa56f37e34cd39d7fceaf129bb200d1fb53"
  "InriaSerif-Regular.ttf:dev:907c200e38899bad5668d81b4c0fd3e880d7da051142405c3ccd7b1c2f45edb5"
  "NotoSerifCJKsc-Bold.otf:dev:0feeb1eab90b813be5fb493324b3bebbe6ef8d8e35ba562601587e5f7a26d052"
  "NotoSerifCJKsc-Regular.otf:dev:9ccb922287f4b4a941ace82321ee6e1ff6340eb53f1c13b0e6285a117d1b0057"
  "NotoSerifCJKtc-Bold.otf:dev:4eaeac4ff55e0a23e70a04154870d07729dfea07f8ef195fdd6cf023fe184361"
  "NotoSerifCJKtc-Regular.otf:dev:9bfeda3cf71ff41ebdcfe0b61ca8284564b8d23fe458ff151eb8739236d5dd7a"
  "Roboto-Regular.ttf:dev:797e35f7f5d6020a5c6ea13b42ecd668bcfb3bbc4baa0e74773527e5b6cb3174"
  "Ubuntu-Regular.ttf:dev:66fea9c00091f25eb8a526548023b6154785876a900af2d8f472922689698163"
)

mkdir -p "$DEST"

# The build image (oven/bun) ships neither curl nor wget, so fall back to bun's
# own fetch when neither is around. Everything else the script needs -
# `sha256sum` for the checksum and `bash` for the script itself - is present in
# both that image and a normal developer machine.
download() {
  local url="$1" out="$2" attempt

  for attempt in 1 2 3; do
    if command -v curl >/dev/null 2>&1; then
      curl -fsSL --max-time 180 -o "$out" "$url" && return 0
    elif command -v wget >/dev/null 2>&1; then
      wget -q -T 180 -O "$out" "$url" && return 0
    else
      DF_FONT_URL="$url" DF_FONT_OUT="$out" bun -e \
        'const r = await fetch(process.env.DF_FONT_URL);
         if (!r.ok) throw new Error(`HTTP ${r.status} for ${process.env.DF_FONT_URL}`);
         await Bun.write(process.env.DF_FONT_OUT, await r.arrayBuffer());' && return 0
    fi
    echo "retrying $url ($attempt/3)" >&2
    sleep 2
  done

  return 1
}

for entry in "${FONTS[@]}"; do
  IFS=":" read -r name repo want <<<"$entry"

  base="$TYPST_ASSETS"
  if [ "$repo" = "dev" ]; then
    base="$TYPST_DEV_ASSETS"
  fi

  target="$DEST/$name"
  if [ -f "$target" ] && [ "$(sha256sum "$target" | cut -d" " -f1)" = "$want" ]; then
    continue
  fi

  echo "fetching $name"
  download "$base/$name" "$target.partial"

  got="$(sha256sum "$target.partial" | cut -d" " -f1)"
  if [ "$got" != "$want" ]; then
    rm -f "$target.partial"
    echo "checksum mismatch for $name: expected $want, got $got" >&2
    exit 1
  fi
  mv "$target.partial" "$target"
done

echo "typst fonts ready in $DEST"
