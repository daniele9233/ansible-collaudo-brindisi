#!/bin/bash
# Avvia la dashboard operativa di collaudo.
# Ascolta solo su 127.0.0.1: per l'accesso da un'altra macchina usa un tunnel SSH.
set -e

cd "$(dirname "$(realpath "$0")")"

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
echo "  URL locale:  http://127.0.0.1:8080"
echo ""
echo "  Accesso da un'altra macchina (tunnel SSH):"
echo "    ssh -L 8080:localhost:8080 root@<controller>"
echo "    poi apri http://localhost:8080"
echo "  ---------------------------------------------------------"
echo ""

exec python3 app.py
