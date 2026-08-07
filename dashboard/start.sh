#!/bin/bash
# Avvia la dashboard operativa di collaudo.
# Ascolta solo su 127.0.0.1: per l'accesso da un'altra macchina usa un tunnel SSH.
#
#   ./start.sh                 porta 8080
#   FARO_PORT=8090 ./start.sh  porta alternativa (utile se la 8080 e' occupata)
set -e

cd "$(dirname "$(realpath "$0")")"

PORT="${FARO_PORT:-${PORT:-8080}}"
export FARO_PORT="$PORT"

# Attiva il virtualenv Ansible se presente (sovrascrivibile con ANSIBLE_VENV).
VENV="${ANSIBLE_VENV:-$HOME/ansible-env}"
if [ -f "$VENV/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  export ANSIBLE_VENV="$VENV"
fi

python3 -c 'import flask' 2>/dev/null || pip install --quiet -r requirements.txt

mkdir -p state

echo ""
echo "  Faro - Collaudo Brindisi"
echo "  ---------------------------------------------------------"
echo "  URL locale:  http://127.0.0.1:${PORT}"
echo ""
echo "  Accesso da un'altra macchina (tunnel SSH):"
echo "    ssh -L ${PORT}:localhost:${PORT} root@<controller>"
echo "    poi apri http://localhost:${PORT}"
echo "  ---------------------------------------------------------"
echo ""

exec python3 app.py
