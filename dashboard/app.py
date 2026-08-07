"""
Dashboard operativa per ansible-collaudo-brindisi.

Espone una GUI locale che permette di:
  - SIMULARE il playbook senza collegarsi ai server (--list-tasks / --list-hosts)
  - VERIFICARE i dischi in sola lettura (check_disk.yml)
  - VERIFICARE le versioni Rancher/RKE2/Helm (check_versions.yml)
  - eseguire un DRY-RUN --check --diff
  - eseguire il DEPLOY reale

Il server ascolta solo su 127.0.0.1: l'accesso da altre macchine va fatto via
tunnel SSH. Non c'e' autenticazione, quindi non va esposto in rete.
"""

from flask import Flask, request, jsonify, render_template
from datetime import datetime, timezone
import subprocess
import threading
import configparser
import shutil
import json
import os
import re

app = Flask(__name__)

# Radice del repository Ansible (la dashboard vive in <repo>/dashboard).
ANSIBLE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
STATE_DIR = os.path.join(os.path.dirname(__file__), 'state')

# Virtualenv opzionale. Se ANSIBLE_VENV punta a un venv valido viene attivato,
# altrimenti si usa l'ansible presente nel PATH.
VENV = os.environ.get('ANSIBLE_VENV', os.path.expanduser('~/ansible-env'))

# Tetto al buffer di output per non far crescere la RAM su run molto lunghi.
MAX_OUTPUT_LINES = 20000

_job_lock = threading.Lock()
_job_state = {
    'status': 'idle',        # idle | running | success | failed
    'action': None,
    'started_at': None,
    'output': [],
    'truncated': False,
}

# ---------------------------------------------------------------------------
# Azioni eseguibili dalla GUI.
#
#   safe=True   non modifica nulla sui server
#   danger=True applica modifiche reali (richiede conferma esplicita)
# ---------------------------------------------------------------------------
COMMANDS = {
    # --- Simulazione: nessuna connessione SSH, nessuna modifica -------------
    'plan_tasks': {
        'exe': 'ansible-playbook',
        'args': ['site.yml', '--list-tasks'],
        'safe': True,
    },
    'plan_hosts': {
        'exe': 'ansible-playbook',
        'args': ['site.yml', '--list-hosts'],
        'safe': True,
    },

    # --- Verifiche in sola lettura sui server -------------------------------
    'check_disk': {
        'exe': 'ansible-playbook',
        'args': ['check_disk.yml'],
        'safe': True,
    },
    'check_versions': {
        'exe': 'ansible-playbook',
        'args': ['check_versions.yml'],
        'safe': True,
    },
    'ping': {
        'exe': 'ansible',
        'args': ['all', '-m', 'ping'],
        'safe': True,
    },

    # --- Dry-run --check ----------------------------------------------------
    'dryrun_all': {
        'exe': 'ansible-playbook',
        'args': ['site.yml', '--check', '--diff'],
        'safe': True,
    },
    'dryrun_disk': {
        'exe': 'ansible-playbook',
        'args': ['site.yml', '--tags', 'disk', '--check', '--diff'],
        'safe': True,
    },
    'dryrun_hosts': {
        'exe': 'ansible-playbook',
        'args': ['site.yml', '--tags', 'hosts', '--check', '--diff'],
        'safe': True,
    },
    'dryrun_nginx': {
        'exe': 'ansible-playbook',
        'args': ['site.yml', '--tags', 'nginx', '--check', '--diff'],
        'safe': True,
    },

    # --- Esecuzione reale ---------------------------------------------------
    'deploy_all': {
        'exe': 'ansible-playbook',
        'args': ['site.yml'],
        'danger': True,
    },
    'deploy_disk': {
        'exe': 'ansible-playbook',
        'args': ['site.yml', '--tags', 'disk'],
        'danger': True,
    },
    'deploy_hosts': {
        'exe': 'ansible-playbook',
        'args': ['site.yml', '--tags', 'hosts'],
        'danger': True,
    },
    'deploy_rancher': {
        'exe': 'ansible-playbook',
        'args': ['site.yml', '--tags', 'rancher'],
        'danger': True,
    },
    'deploy_cluster': {
        'exe': 'ansible-playbook',
        'args': ['site.yml', '--tags', 'cluster'],
        'danger': True,
    },
    'deploy_kubectl': {
        'exe': 'ansible-playbook',
        'args': ['site.yml', '--tags', 'kubectl'],
        'danger': True,
    },
    'deploy_nginx': {
        'exe': 'ansible-playbook',
        'args': ['site.yml', '--tags', 'nginx'],
        'danger': True,
    },
}

# Un --limit passato dalla GUI deve essere un pattern host innocuo: nomi
# inventario, gruppi, virgole, ':' e '!' del pattern language di Ansible.
_LIMIT_RE = re.compile(r'^[A-Za-z0-9_.\-,:!*\[\]]{1,200}$')
# Un device deve essere un percorso /dev/<nome> senza metacaratteri.
_DEVICE_RE = re.compile(r'^/dev/[a-zA-Z0-9/_-]{1,60}$')


def _build_env():
    env = os.environ.copy()
    env['ANSIBLE_FORCE_COLOR'] = '1'
    env['PYTHONUNBUFFERED'] = '1'
    if os.path.isdir(os.path.join(VENV, 'bin')):
        env['VIRTUAL_ENV'] = VENV
        env['PATH'] = os.path.join(VENV, 'bin') + os.pathsep + env.get('PATH', '')
        env.pop('PYTHONHOME', None)
    return env


def _append(line):
    if len(_job_state['output']) >= MAX_OUTPUT_LINES:
        _job_state['truncated'] = True
        return
    _job_state['output'].append(line)


def _run(cmd_parts):
    """Esegue il comando streammando l'output riga per riga in _job_state."""
    try:
        proc = subprocess.Popen(
            cmd_parts,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=_build_env(),
            cwd=ANSIBLE_DIR,
            bufsize=1,
            universal_newlines=True,
        )
        for line in iter(proc.stdout.readline, ''):
            _append(line.rstrip('\n'))
        proc.wait()
        _job_state['status'] = 'success' if proc.returncode == 0 else 'failed'
        _append('')
        _append(f'[exit code {proc.returncode}]')
    except FileNotFoundError:
        _append(f'[ERRORE] eseguibile non trovato: {cmd_parts[0]}')
        _append('Attiva il virtualenv Ansible oppure imposta ANSIBLE_VENV.')
        _job_state['status'] = 'failed'
    except Exception as exc:
        _append(f'[ERRORE INTERNO] {exc}')
        _job_state['status'] = 'failed'
    finally:
        _job_lock.release()


VENDOR_DIR = os.path.join(os.path.dirname(__file__), 'static', 'vendor')


def _vendored():
    """True se gli asset front-end sono disponibili in locale (./vendor.sh),
    cosi' la dashboard funziona anche su un controller senza internet."""
    return all(os.path.isfile(os.path.join(VENDOR_DIR, f))
               for f in ('tailwind.js', 'lucide.js'))


@app.route('/')
def index():
    return render_template('index.html', vendored=_vendored())


@app.route('/api/output')
def api_output():
    since = request.args.get('since', '0')
    try:
        since = max(0, int(since))
    except ValueError:
        since = 0
    return jsonify({
        'lines': _job_state['output'][since:],
        'total': len(_job_state['output']),
        'status': _job_state['status'],
        'action': _job_state['action'],
        'startedAt': _job_state['started_at'],
        'truncated': _job_state['truncated'],
    })


@app.route('/api/run', methods=['POST'])
def api_run():
    data = request.get_json(force=True, silent=True) or {}
    action = data.get('action', '')
    limit = (data.get('limit') or '').strip()
    device = (data.get('device') or '').strip()

    cmd_info = COMMANDS.get(action)
    if not cmd_info:
        return jsonify({'error': 'Azione non valida'}), 400

    cmd_parts = [cmd_info['exe']] + list(cmd_info['args'])

    if limit:
        if not _LIMIT_RE.match(limit):
            return jsonify({'error': 'Valore --limit non valido'}), 400
        cmd_parts += ['--limit', limit]

    if device:
        if not _DEVICE_RE.match(device):
            return jsonify({'error': 'Device non valido (atteso /dev/<nome>)'}), 400
        cmd_parts += ['-e', f'rancher_disk_device={device}']

    if not _job_lock.acquire(blocking=False):
        return jsonify({'error': 'Un job e\' gia\' in esecuzione. Attendi il completamento.'}), 409

    _job_state['output'] = []
    _job_state['truncated'] = False
    _job_state['status'] = 'running'
    _job_state['action'] = action
    _job_state['started_at'] = datetime.now(timezone.utc).isoformat()
    _append('$ ' + ' '.join(cmd_parts))
    _append('')

    threading.Thread(target=_run, args=(cmd_parts,), daemon=True).start()
    return jsonify({'status': 'started', 'command': ' '.join(cmd_parts)})


# ---------------------------------------------------------------------------
# SIMULAZIONE: piano di esecuzione ricavato da --list-tasks / --list-hosts.
# Entrambi i comandi risolvono solo l'inventario in locale: non aprono alcuna
# connessione SSH e non toccano i server.
# ---------------------------------------------------------------------------
_PLAY_RE = re.compile(r'^\s*play #(\d+) \((.*)\):\s*(.*?)\s*TAGS:\s*\[(.*)\]\s*$')
_TASK_RE = re.compile(r'^\s{6,}(\S.*?)\s*TAGS:\s*\[(.*)\]\s*$')
_HOSTS_RE = re.compile(r'^\s*hosts \((\d+)\):\s*$')


def _ansible_exe(name):
    venv_bin = os.path.join(VENV, 'bin', name)
    return venv_bin if os.path.isfile(venv_bin) else (shutil.which(name) or name)


def _run_sync(args, timeout=60):
    try:
        r = subprocess.run(
            args, capture_output=True, text=True,
            env=_build_env(), cwd=ANSIBLE_DIR, timeout=timeout,
        )
        return r.returncode, (r.stdout or '') + (r.stderr or '')
    except subprocess.TimeoutExpired:
        return -1, f'(timeout > {timeout}s)'
    except FileNotFoundError:
        return -2, f'eseguibile non trovato: {args[0]}'
    except Exception as exc:
        return -3, str(exc)


def _parse_plan(tasks_out, hosts_out):
    """Unisce l'output di --list-tasks e --list-hosts in una lista di play."""
    plays, current = [], None
    for raw in tasks_out.splitlines():
        m = _PLAY_RE.match(raw)
        if m:
            current = {
                'index': int(m.group(1)),
                'pattern': m.group(2),
                'tags': [t.strip() for t in m.group(4).split(',') if t.strip()],
                'tasks': [],
                'hosts': [],
            }
            plays.append(current)
            continue
        if current is None:
            continue
        m = _TASK_RE.match(raw)
        if m and not raw.strip().startswith('play #'):
            label = m.group(1).strip()
            role, _, name = label.partition(' : ')
            current['tasks'].append({
                'role': role.strip() if name else '',
                'name': (name or role).strip(),
                'tags': [t.strip() for t in m.group(2).split(',') if t.strip()],
            })

    # --list-hosts: associa gli host al play corrispondente per indice.
    by_index = {p['index']: p for p in plays}
    current, collecting = None, False
    for raw in hosts_out.splitlines():
        m = _PLAY_RE.match(raw)
        if m:
            current = by_index.get(int(m.group(1)))
            collecting = False
            continue
        if _HOSTS_RE.match(raw):
            collecting = True
            continue
        if collecting and current is not None:
            host = raw.strip()
            if host and not raw.startswith('  play') and ':' not in host:
                current['hosts'].append(host)
            elif not host:
                collecting = False
    return plays


@app.route('/api/plan')
def api_plan():
    exe = _ansible_exe('ansible-playbook')
    rc1, tasks_out = _run_sync([exe, 'site.yml', '--list-tasks'])
    rc2, hosts_out = _run_sync([exe, 'site.yml', '--list-hosts'])
    if rc1 != 0 or rc2 != 0:
        return jsonify({
            'plays': [],
            'error': (tasks_out if rc1 != 0 else hosts_out).strip()[:600],
        })
    plays = _parse_plan(tasks_out, hosts_out)
    return jsonify({
        'plays': plays,
        'totalTasks': sum(len(p['tasks']) for p in plays),
        'error': None,
    })


# ---------------------------------------------------------------------------
# Inventario e versioni: letti dai file del repo, nessuna esecuzione.
# ---------------------------------------------------------------------------
@app.route('/api/inventory')
def api_inventory():
    path = os.path.join(ANSIBLE_DIR, 'inventory.ini')
    parser = configparser.ConfigParser(allow_no_value=True, delimiters=('=',))
    parser.optionxform = str
    groups = []
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            parser.read_string(fh.read())
    except FileNotFoundError:
        return jsonify({'groups': [], 'error': 'inventory.ini non trovato'})
    except configparser.Error as exc:
        return jsonify({'groups': [], 'error': f'inventory.ini non parsabile: {exc}'})

    for section in parser.sections():
        hosts = []
        for raw_key in parser.options(section):
            # configparser separa su '=': la riga "h ansible_host=1.2.3.4 x=y"
            # diventa chiave "h ansible_host" e valore "1.2.3.4 x=y".
            line = raw_key
            value = parser.get(section, raw_key)
            if value is not None:
                line = f'{raw_key}={value}'
            parts = line.split()
            name = parts[0]
            attrs = {}
            for p in parts[1:]:
                if '=' in p:
                    k, v = p.split('=', 1)
                    attrs[k] = v
            hosts.append({
                'name': name,
                'ip': attrs.get('ansible_host', ''),
                'user': attrs.get('ansible_user', ''),
                'connection': attrs.get('ansible_connection', 'ssh'),
            })
        groups.append({'name': section, 'hosts': hosts})
    return jsonify({'groups': groups, 'error': None})


# Matrice Rancher -> minor Kubernetes supportate (allineata a check_versions.yml).
RANCHER_K8S_MATRIX = {
    '2.14': ['1.33', '1.34', '1.35'],
    '2.13': ['1.32', '1.33', '1.34'],
    '2.12': ['1.31', '1.32', '1.33'],
    '2.11': ['1.30', '1.31', '1.32'],
}

_VERSION_KEYS = [
    'rancher_version', 'rke2_version', 'cluster_k8s_version', 'helm_version',
    'rancher_domain', 'cluster_name', 'cluster_cni',
    'vg_name', 'lv_name', 'rancher_mount_point', 'filesystem_type',
    'rancher_disk_device',
]


def _minor(version):
    m = re.search(r'(\d+)\.(\d+)', version or '')
    return f'{m.group(1)}.{m.group(2)}' if m else ''


@app.route('/api/versions')
def api_versions():
    path = os.path.join(ANSIBLE_DIR, 'group_vars', 'all.yml')
    values = {}
    try:
        import yaml
        with open(path, 'r', encoding='utf-8') as fh:
            data = yaml.safe_load(fh) or {}
        values = {k: data.get(k, '') for k in _VERSION_KEYS}
    except ImportError:
        # Fallback senza PyYAML: estrazione riga per riga delle chiavi semplici.
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                for line in fh:
                    m = re.match(r'^([a-z_]+):\s*"?([^"#\n]*?)"?\s*(?:#.*)?$', line)
                    if m and m.group(1) in _VERSION_KEYS:
                        values[m.group(1)] = m.group(2).strip()
        except OSError as exc:
            return jsonify({'values': {}, 'error': str(exc)})
    except Exception as exc:
        return jsonify({'values': {}, 'error': str(exc)})

    rancher_minor = _minor(str(values.get('rancher_version', '')))
    supported = RANCHER_K8S_MATRIX.get(rancher_minor, [])
    k8s_minor = _minor(str(values.get('cluster_k8s_version', '')))
    rke2_minor = _minor(str(values.get('rke2_version', '')))
    helm = str(values.get('helm_version', ''))

    checks = [{
        'label': 'cluster_k8s_version nella matrice Rancher',
        'ok': bool(supported) and k8s_minor in supported,
        'detail': f'Kubernetes {k8s_minor or "?"} vs Rancher {rancher_minor or "?"} '
                  f'({", ".join(supported) if supported else "matrice sconosciuta"})',
    }, {
        'label': 'rke2_version nella matrice Rancher',
        'ok': bool(supported) and rke2_minor in supported,
        'detail': f'Kubernetes {rke2_minor or "?"} vs Rancher {rancher_minor or "?"} '
                  f'({", ".join(supported) if supported else "matrice sconosciuta"})',
    }, {
        'label': 'Helm sulla linea 3.x',
        'ok': helm.startswith('v3.'),
        'detail': f'helm_version = {helm or "?"} (i chart Rancher sono validati su Helm 3)',
    }, {
        'label': 'cluster locale e downstream sulla stessa minor',
        'ok': bool(rke2_minor) and rke2_minor == k8s_minor,
        'detail': f'rke2_version {rke2_minor or "?"} / cluster_k8s_version {k8s_minor or "?"}',
    }]

    return jsonify({
        'values': values,
        'rancherMinor': rancher_minor,
        'supportedK8s': supported,
        'checks': checks,
        'error': None,
    })


@app.route('/api/disks')
def api_disks():
    """Ultimo report prodotto da check_disk.yml (dashboard/state/disks.json)."""
    path = os.path.join(STATE_DIR, 'disks.json')
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            return jsonify(json.load(fh))
    except FileNotFoundError:
        return jsonify({'hosts': [], 'generated_at': None,
                        'error': 'Nessun report disponibile: esegui prima "Verifica dischi".'})
    except json.JSONDecodeError as exc:
        return jsonify({'hosts': [], 'generated_at': None,
                        'error': f'disks.json non valido: {exc}'})


@app.route('/api/file')
def api_file():
    """Visualizzatore di file del repo, confinato alla directory del progetto."""
    rel = request.args.get('path', '')
    full = os.path.normpath(os.path.join(ANSIBLE_DIR, rel))
    root = os.path.normpath(ANSIBLE_DIR)
    if full != root and not full.startswith(root + os.sep):
        return jsonify({'error': 'Path non consentito'}), 403
    if os.path.basename(full).endswith('.pem'):
        return jsonify({'error': 'I certificati non sono visualizzabili dalla dashboard'}), 403
    try:
        with open(full, 'r', encoding='utf-8') as fh:
            return jsonify({'content': fh.read(), 'path': rel})
    except FileNotFoundError:
        return jsonify({'error': 'File non trovato'}), 404
    except (OSError, UnicodeDecodeError) as exc:
        return jsonify({'error': str(exc)}), 500


@app.route('/api/env')
def api_env():
    """Stato dell'ambiente: presenza di ansible, venv, repo."""
    exe = _ansible_exe('ansible-playbook')
    rc, out = _run_sync([exe, '--version'], timeout=20)
    version = out.splitlines()[0].strip() if rc == 0 and out.strip() else None
    return jsonify({
        'ansible': version,
        'ansibleOk': rc == 0,
        'ansibleError': None if rc == 0 else out.strip()[:300],
        'repo': ANSIBLE_DIR,
        'venv': VENV if os.path.isdir(os.path.join(VENV, 'bin')) else None,
        'vendored': _vendored(),
    })


if __name__ == '__main__':
    os.makedirs(STATE_DIR, exist_ok=True)
    app.run(host='127.0.0.1', port=8080, debug=False, threaded=True)
