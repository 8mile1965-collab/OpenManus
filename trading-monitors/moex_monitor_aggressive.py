#!/usr/bin/env python3
"""
Paper-trade монитор — АГРЕССИВНЫЙ ПЛАН (Cade).
Депозит: 10 000₽, без плеча, контроль 10 000₽
Цель: +5-10% за день = +500-1000₽
"""
import urllib.request, json, sys, os, time, csv
from datetime import datetime
from pathlib import Path

TRADES_FILE = Path(__file__).parent / "trades_log_aggressive.csv"
STATE_FILE = Path(__file__).parent / "state_aggressive.json"

# Агрессивный план: 1-2 сильные идеи, БЕЗ плеча
# Концентрация вместо диверсификации
WATCHLIST = [
    # Главная идея — PIKK (перепродан, объём x5.5)
    # 18 лотов × 553₽ = 9 954₽ (весь депо)
    {"ticker": "PIKK",  "side": "long", "qty": 18,  "entry_lo": 552,  "entry_hi": 554, "stop": 545,   "take": 580,   "take_aggr": 590},
    # Запасная — IRAO (дешёвая, ликвидная)
    # 1000 × 2.39₽ = 2 390₽
    {"ticker": "IRAO",  "side": "long", "qty": 1000,"entry_lo": 2.37,  "entry_hi": 2.39, "stop": 2.32,  "take": 2.50,  "take_aggr": 2.54},
]

DAILY_LOSS_LIMIT = -400

PLAN_NAME = "АГРЕССИВНЫЙ (без плеча)"

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"positions": {}, "history": [], "started_at": datetime.now().isoformat()}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))

def log_trade(action, ticker, qty, price, pnl=None, notes=""):
    new = not TRADES_FILE.exists() or TRADES_FILE.stat().st_size == 0
    with open(TRADES_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date","time","ticker","action","qty","price","pnl","notes"])
        dt = datetime.now()
        w.writerow([dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S"), ticker, action, qty, f"{price:.4f}", f"{pnl:.2f}" if pnl else "", notes])

def send_telegram(text):
    try:
        token = os.popen("assistant credentials reveal --service telegram --field bot_token 2>/dev/null").read().strip()
        chat_id = "8764520644"
        if not token or len(token) < 20:
            print(f"  [TG-SKIP] {text[:80]}")
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()
        print(f"  [TG-OK] {text[:80]}")
    except Exception as e:
        print(f"  [TG-ERR] {e}")

def get_price(ticker):
    url = f"https://iss.moex.com/iss/engines/stock/markets/shares/securities/{ticker}.json?iss.meta=off"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read().decode('utf-8-sig'))
        md = data.get('marketdata', {}).get('data', [])
        cols = data.get('marketdata', {}).get('columns', [])
        ci = {c: i for i, c in enumerate(cols)}
        for row in md:
            if row[ci.get('BOARDID', 0)] == 'TQBR':
                return {
                    'last': row[ci.get('LAST', 4)] or 0,
                    'open': row[ci.get('OPEN', 1)] or 0,
                    'high': row[ci.get('HIGH', 5)] or 0,
                    'low':  row[ci.get('LOW', 6)] or 0,
                }
    except Exception as e:
        return {'error': str(e)}
    return None

def scan_once():
    state = load_state()
    now = datetime.now().strftime("%H:%M:%S")
    print(f"\n[{now}] [AGR] {PLAN_NAME} — {len(WATCHLIST)} бумаг, {len(state['positions'])} позиций")
    print("="*70)

    daily_pnl = sum(h['pnl'] for h in state['history'])
    print(f"  Дневной P&L: {daily_pnl:+.0f}₽ (лимит {DAILY_LOSS_LIMIT})")

    if daily_pnl <= DAILY_LOSS_LIMIT:
        print(f"  STOP TRADING: дневной убыток превысил лимит")
        return

    for ticker, pos in list(state['positions'].items()):
        p = get_price(ticker)
        if not p or 'error' in p:
            continue
        last = p['last']
        entry = pos['entry_price']
        unrealized = (last - entry) * pos['qty']
        pct = ((last/entry)-1)*100
        print(f"  {ticker} [OPEN]: last={last}, entry={entry}, P&L={unrealized:+.0f}₽ ({pct:+.2f}%)")

        if last <= pos['stop']:
            pnl = (last - entry) * pos['qty']
            log_trade("CLOSE", ticker, pos['qty'], last, pnl, "STOP")
            send_telegram(f"[AGR] STOP {ticker}\nЦена: {last}\nP&L: {pnl:+.0f}₽")
            del state['positions'][ticker]
            state['history'].append({"ticker": ticker, "pnl": pnl, "reason": "STOP", "at": datetime.now().isoformat()})
        elif last >= pos.get('take_aggr', pos['take']):
            pnl = (last - entry) * pos['qty']
            log_trade("CLOSE", ticker, pos['qty'], last, pnl, "TAKE_AGGR")
            send_telegram(f"[AGR] TAKE++ {ticker}\nЦена: {last}\nP&L: {pnl:+.0f}₽")
            del state['positions'][ticker]
            state['history'].append({"ticker": ticker, "pnl": pnl, "reason": "TAKE_AGGR", "at": datetime.now().isoformat()})
        elif last >= pos['take']:
            pnl = (last - entry) * pos['qty']
            log_trade("CLOSE", ticker, pos['qty'], last, pnl, "TAKE")
            send_telegram(f"[AGR] TAKE {ticker}\nЦена: {last}\nP&L: {pnl:+.0f}₽")
            del state['positions'][ticker]
            state['history'].append({"ticker": ticker, "pnl": pnl, "reason": "TAKE", "at": datetime.now().isoformat()})

    for w in WATCHLIST:
        ticker = w['ticker']
        if ticker in state['positions']:
            continue
        p = get_price(ticker)
        if not p or 'error' in p:
            print(f"  {ticker}: ERR нет данных")
            continue
        last = p['last']
        if w['entry_lo'] <= last <= w['entry_hi']:
            pos = {
                'side': w['side'],
                'qty': w['qty'],
                'entry_price': last,
                'stop': w['stop'],
                'take': w['take'],
                'take_aggr': w['take_aggr'],
                'opened_at': datetime.now().isoformat(),
            }
            state['positions'][ticker] = pos
            log_trade("OPEN", ticker, w['qty'], last, notes="AGR")
            send_telegram(f"[AGR] ENTER {ticker}\nЦена: {last}\nКол-во: {w['qty']}\nСтоп: {w['stop']}, Тейк: {w['take']}")
            print(f"    >> ENTER {ticker} @ {last}")
        else:
            print(f"  {ticker} [WAIT]: last={last}, диапазон {w['entry_lo']}-{w['entry_hi']}")

    save_state(state)

def show_status():
    state = load_state()
    print(f"\nSTATUS [{PLAN_NAME}] ПОЗИЦИИ:")
    if not state['positions']:
        print("  Нет открытых позиций")
    for ticker, pos in state['positions'].items():
        print(f"  {ticker}: {pos['side']} {pos['qty']}@{pos['entry_price']}, stop={pos['stop']}")
    pnl = sum(h['pnl'] for h in state['history'])
    print(f"\n  P&L: {pnl:+.0f}₽, сделок: {len(state['history'])}")

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'scan'
    if cmd == 'scan':
        scan_once()
    elif cmd == 'loop':
        while True:
            try:
                scan_once()
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"ERR: {e}")
            time.sleep(60)
    elif cmd == 'status':
        show_status()
