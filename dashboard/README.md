# Faro — dashboard operativa

GUI locale per il repository `ansible-collaudo-brindisi`.

## Avvio

```bash
cd dashboard
./start.sh
```

Ascolta su `127.0.0.1:8080`. **Non ha autenticazione**: non esporla in rete.
Per usarla da un'altra macchina apri un tunnel SSH:

```bash
ssh -L 8080:localhost:8080 root@<controller>
```

Se Ansible è in un virtualenv diverso da `~/ansible-env`:

```bash
ANSIBLE_VENV=/percorso/al/venv ./start.sh
```

## Cosa fa

| Pagina | Tocca i server? | Comando eseguito |
|---|---|---|
| Simulazione | **No**, nessuna connessione SSH | `site.yml --list-tasks` / `--list-hosts` |
| Inventario | No (lettura di `inventory.ini`) | — |
| Versioni | No per i controlli statici | `check_versions.yml` per la validazione online |
| Dischi | Sì, in sola lettura | `check_disk.yml` (solo `lsblk` e `stat`) |
| Dry-Run | Sì, senza applicare | `site.yml --tags <tag> --check --diff` |
| Deploy | **Sì, modifiche reali** | `site.yml --tags <tag>` |

Il Deploy richiede di digitare `ESEGUI` in una modale di conferma.

## Struttura

```
dashboard/
├── app.py              backend Flask
├── templates/
│   └── index.html      GUI
├── requirements.txt
├── start.sh
└── state/
    └── disks.json      report prodotto da check_disk.yml (non versionato)
```

## Controller senza internet

La GUI usa Tailwind e Lucide da CDN. Se il controller non raggiunge internet,
scarica gli asset una volta sola da una macchina che ci arriva:

```bash
cd dashboard && ./vendor.sh
```

e copia la cartella `static/vendor/` sul controller. `app.py` la rileva da solo
e smette di puntare ai CDN. Senza asset locali né internet la dashboard resta
comunque funzionante, con un banner di avviso e una grafica ridotta.
