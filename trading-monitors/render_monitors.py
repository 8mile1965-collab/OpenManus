#!/usr/bin/env python3
"""
Render-обёртка для 4 paper-trade мониторов.
Запускает все 4 плана в отдельных threads + FastAPI для health/status/control.
"""
import os, json, threading, time, subprocess, sys
from pathlib import Path
from datetime import datetime

# Render даёт порт через env
PORT = int(os.environ.get('PORT', 10001))
TRADING_DIR = Path('/opt/render/project/src/data/trading/2026-09-07') if Path('/opt/render/project/src').exists() else Path(__file__).parent

try:
    from fastapi import FastAPI, HTTPException
    import uvicorn
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

PLANS = ['conservative', 'aggressive', 'risky', 'flex']
procs = {}  # plan -> {subprocess, started_at, log_tail}

def start_monitors():
    """Запускает все 4 монитора как подпроцессы."""
    for plan in PLANS:
        script = TRADING_DIR / f'moex_monitor_{plan}.py'
        if not script.exists():
            print(f'[WARN] {script} not found', flush=True)
            continue
        log_path = TRADING_DIR / f'logs_{plan}.log'
        f = open(log_path, 'a')
        p = subprocess.Popen(
            ['python3', '-u', str(script), 'loop'],
            stdout=f, stderr=subprocess.STDOUT,
            cwd=str(TRADING_DIR)
        )
        procs[plan] = {'proc': p, 'pid': p.pid, 'started_at': datetime.now().isoformat(), 'log': str(log_path)}
        print(f'[START] {plan} pid={p.pid}', flush=True)

def monitor_watcher():
    """Следит чтобы мониторы не падали, перезапускает."""
    while True:
        for plan, info in list(procs.items()):
            p = info['proc']
            if p.poll() is not None:
                print(f'[RESTART] {plan} died, restarting...', flush=True)
                log_path = Path(info['log'])
                f = open(log_path, 'a')
                np = subprocess.Popen(
                    ['python3', '-u', str(TRADING_DIR / f'moex_monitor_{plan}.py'), 'loop'],
                    stdout=f, stderr=subprocess.STDOUT,
                    cwd=str(TRADING_DIR)
                )
                procs[plan] = {'proc': np, 'pid': np.pid, 'started_at': datetime.now().isoformat(), 'log': str(log_path)}
        time.sleep(30)

if HAS_FASTAPI:
    app = FastAPI(title="Trading Monitors")

    @app.get('/healthz')
    def healthz():
        alive = sum(1 for info in procs.values() if info['proc'].poll() is None)
        return {
            'status': 'ok' if alive == len(PLANS) else 'degraded',
            'alive': alive, 'total': len(PLANS),
            'plans': {p: {'alive': info['proc'].poll() is None, 'pid': info['pid']} for p, info in procs.items()},
            'time': datetime.now().isoformat()
        }

    @app.get('/positions/{plan}')
    def get_positions(plan: str):
        if plan not in PLANS:
            raise HTTPException(404, f'Unknown plan: {plan}')
        state_file = TRADING_DIR / f'state_{plan}.json'
        if not state_file.exists():
            return {'positions': {}, 'history': []}
        return json.loads(state_file.read_text())

    @app.get('/all')
    def get_all():
        result = {}
        for plan in PLANS:
            sf = TRADING_DIR / f'state_{plan}.json'
            if sf.exists():
                result[plan] = json.loads(sf.read_text())
        return result

    @app.get('/logs/{plan}')
    def get_logs(plan: str, lines: int = 50):
        if plan not in PLANS:
            raise HTTPException(404, f'Unknown plan: {plan}')
        log_file = TRADING_DIR / f'logs_{plan}.log'
        if not log_file.exists():
            return {'lines': []}
        try:
            content = log_file.read_text(errors='ignore').splitlines()
            return {'lines': content[-lines:]}
        except Exception as e:
            return {'error': str(e)}

# Запуск
if __name__ == '__main__':
    print(f'[BOOT] Trading Monitors Wrapper on port {PORT}', flush=True)
    print(f'[BOOT] Trading dir: {TRADING_DIR}', flush=True)
    print(f'[BOOT] FastAPI available: {HAS_FASTAPI}', flush=True)
    start_monitors()
    watcher = threading.Thread(target=monitor_watcher, daemon=True)
    watcher.start()
    if HAS_FASTAPI:
        uvicorn.run(app, host='0.0.0.0', port=PORT, log_level='info')
    else:
        # без FastAPI — держим процесс живым
        while True:
            time.sleep(60)
