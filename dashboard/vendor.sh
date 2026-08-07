#!/bin/bash
# Scarica in locale gli asset front-end della dashboard (Tailwind, Lucide, font).
#
# Va eseguito UNA VOLTA da una macchina con accesso a internet. Dopodiche' la
# dashboard non ha piu' bisogno di raggiungere alcun CDN: utile sui controller
# on-prem senza uscita verso internet.
#
# Se il controller non ha internet, esegui questo script altrove e copia la
# cartella static/vendor/ risultante.
set -euo pipefail

cd "$(dirname "$(realpath "$0")")"
DEST="static/vendor"
mkdir -p "$DEST"

fetch() {
  local url="$1" out="$2"
  echo "  -> $out"
  if ! curl -fsSL "$url" -o "$DEST/$out"; then
    echo "     FALLITO: $url" >&2
    return 1
  fi
}

echo "Scarico gli asset in $DEST"
fetch "https://cdn.tailwindcss.com/3.4.17"                              "tailwind.js"
fetch "https://unpkg.com/lucide@latest/dist/umd/lucide.js"              "lucide.js"

echo
echo "Fatto. Riavvia la dashboard: gli asset locali vengono usati in automatico."
echo "I font Space Grotesk / JetBrains Mono non vengono scaricati: senza internet"
echo "il browser usa i font di sistema, il resto della grafica resta invariato."
