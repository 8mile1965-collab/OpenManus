#!/usr/bin/env python3
"""
Paper-trade монитор MOEX.
Опрашивает ISS в реальном времени, проверяет условия входа/выхода/стопа,
записывает сделки в trades_log.csv, шлёт уведомления в Telegram.

Использование:
  moex_monitor.py scan      — однократное сканирование (для теста)
  moex_monitor.py loop      — цикл каждые 60 сек в торговые часы
  moex_monitor.py status    — текущие открытые позиции
  moex_monitor.py report    — отчёт за день
"""
import urllib.request, json, sys, os, time, csv
from datetime import datetime, timezone, timedelta
from pathlib import Path

TRADES_FILE = Path(__file__).parent / "trades_log.csv"
STATE_FILE = Path(__file__).parent / "state.json"

# Стратегия V2 — цель +3-5% без плеча
# Депозит 10 000₽, контроль 10 000₽
WATCHLIST = [
    {"ticker": "PIKK",  "side": "long", "qty": 18,   "entry_lo": 552,  "entry_hi": 554, "stop": 548,   "take": 568,   "take_aggr": 572},
    {"ticker": "IRAO",  "side": "long", "qty": 1000, "entry_lo": 2.37, "entry_hi": 2.39, "stop": 2.34,  "take": 2.46,  "take_aggr": 2.48},
    {"ticker": "SOFL",  "side": "long", "qty": 50,   "entry_lo": 51.5, "entry_hi": 52.0, "stop": 51.0,  "take": 54.0,  "take_aggr": 55.0},
]

DAILY_LOSS_LIMIT = -500  # Максимум -500₽ в день, потом стоп торговли

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"positions": {}, "history": [], "started_at": datetime.now().isoformat()}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))

def log_trade(action, ticker, qty, price, pnl=None, notes=""):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new = not TRADES_FILE.exists() or TRADES_FILE.stat().st_size == 0
    with open(TRADES_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date","time","ticker","action","qty","price","pnl","notes"])
        # extract date+time
        dt = datetime.now()
        w.writerow([dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S"), ticker, action, qty, f"{price:.4f}", f"{pnl:.2f}" if pnl else "", notes])

def send_telegram(text):
    """Шлёт уведомление в Telegram."""
    try:
        token = os.popen("assistant credentials reveal --service telegram --field bot_token 2>/dev/null").read().strip()
        chat_id = "8764520644"  # Zubr
        if not token or len(token) < 20:
            print(f"  [TG-SKIP] (no token) {text}")
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()
        print(f"  [TG-OK] {text[:60]}")
    except Exception as e:
        print(f"  [TG-ERR] {e}: {text[:60]}")

def get_price(ticker):
    """Текущая цена через ISS."""
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
                    'vol':  row[ci.get('VOLTODAY', 7)] or 0,
                    'bid':  row[ci.get('BID', 11)] or 0,
                    'offer':row[ci.get('OFFER', 12)] or 0,
                }
    except Exception as e:
        return {'error': str(e)}
    return None

def check_position(pos, price):
    """Проверяет условия для открытой позиции. Возвращает ('exit', reason, pnl) или None."""
    entry = pos['entry_price']
    qty = pos['qty']
    side = pos['side']
    
    if side == 'long':
        if price <= pos['stop']:
            pnl = (price - entry) * qty
            return 'exit', 'STOP', pnl
        if price >= pos['take']:
            pnl = (price - entry) * qty
            return 'exit', 'TAKE', pnl
        if price >= pos.get('take_aggr', pos['take']*1.1):
            pnl = (price - entry) * qty
            return 'exit', 'TAKE_AGGR', pnl
    return None

def check_entry(w, price):
    """Проверяет условия для входа. Возвращает True если входим."""
    if w['side'] == 'long':
        return w['entry_lo'] <= price <= w['entry_hi']
    return None

def scan_once():
    """Однократное сканирование watchlist + позиций."""
    state = load_state()
    now = datetime.now().strftime("%H:%M:%S")
    
    print(f"\n[{now}] СКАН MOEX — {len(WATCHLIST)} бумаг, {len(state['positions'])} открытых позиций")
    print("="*70)
    
    # Проверяем открытые позиции
    for ticker, pos in list(state['positions'].items()):
        p = get_price(ticker)
        if not p or 'error' in p:
            print(f"  {ticker}: ❌ нет данных")
            continue
        last = p['last']
        entry = pos['entry_price']
        unrealized = (last - entry) * pos['qty']
        pct = ((last/entry)-1)*100
        print(f"  {ticker} [OPEN]: last={last}, entry={entry}, unrealized={unrealized:+.0f}₽ ({pct:+.2f}%)")
        
        # Проверка выхода
        result = check_position(pos, last)
        if result:
            action, reason, pnl = result
            log_trade("CLOSE", ticker, pos['qty'], last, pnl, reason)
            send_telegram(f"🔴 <b>ЗАКРЫТИЕ {ticker}</b>\nЦена: {last}\nПричина: {reason}\nP&L: <b>{pnl:+.0f}₽</b>")
            del state['positions'][ticker]
            state['history'].append({"ticker": ticker, "pnl": pnl, "reason": reason, "at": datetime.now().isoformat()})
    
    # Проверяем новые входы
    for w in WATCHLIST:
        ticker = w['ticker']
        if ticker in state['positions']:
            continue
        p = get_price(ticker)
        if not p or 'error' in p:
            print(f"  {ticker}: ❌ нет данных (вход невозможен)")
            continue
        last = p['last']
        print(f"  {ticker} [WAIT]: last={last}, диапазон входа {w['entry_lo']}-{w['entry_hi']}")
        
        if check_entry(w, last):
            entry_price = last
            pos = {
                'side': w['side'],
                'qty': w['qty'],
                'entry_price': entry_price,
                'stop': w['stop'],
                'take': w['take'],
                'take_aggr': w['take_aggr'],
                'opened_at': datetime.now().isoformat(),
            }
            state['positions'][ticker] = pos
            log_trade("OPEN", ticker, w['qty'], entry_price, notes=f"watchlist entry")
            send_telegram(f"🟢 <b>ВХОД {ticker}</b>\nЦена: {entry_price}\nКол-во: {w['qty']} лотов\nСтоп: {w['stop']}, Тейк: {w['take']}")
            print(f"    ✅ ВХОД по {entry_price}, qty={w['qty']}")
    
    save_state(state)

def show_status():
    state = load_state()
    print("\n📊 ТЕКУЩИЕ ПОЗИЦИИ:")
    if not state['positions']:
        print("  Нет открытых позиций")
    for ticker, pos in state['positions'].items():
        print(f"  {ticker}: {pos['side']} {pos['qty']}@{pos['entry_price']}, stop={pos['stop']}, take={pos['take']}")
    print(f"\nИстория: {len(state['history'])} закрытых сделок")
    if state['history']:
        total_pnl = sum(h['pnl'] for h in state['history'])
        print(f"Суммарный P&L: {total_pnl:+.2f}₽")

def report():
    """Отчёт за день."""
    if not TRADES_FILE.exists():
        print("Нет сделок")
        return
    print(f"\n📋 ОТЧЁТ — {TRADES_FILE}")
    print("="*70)
    total = 0
    wins = 0
    losses = 0
    with open(TRADES_FILE, 'r', encoding='utf-8') as f:
        r = csv.DictReader(f)
        for row in r:
            pnl = float(row['pnl']) if row['pnl'] else 0
            total += pnl
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1
            if row['action'] == 'CLOSE':
                print(f"  {row['date']} {row['time']} {row['ticker']} CLOSE {row['qty']}@{row['price']} → {pnl:+.2f}₽ ({row['notes']})")
    print(f"\n💰 P&L: {total:+.2f}₽")
    print(f"   Побед: {wins}, Проигрышей: {losses}")
    print(f"   Винрейт: {(wins/(wins+losses)*100 if wins+losses else 0):.1f}%")

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'scan'
    if cmd == 'scan':
        scan_once()
    elif cmd == 'loop':
        print("🔄 Цикл мониторинга (каждые 60 сек, в торговые часы)")
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
    elif cmd == 'report':
        report()
    else:
        print(f"Unknown cmd: {cmd}")
