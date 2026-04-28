import sys
sys.path.insert(0, "src")
from chaogu_alert.db import get_connection, get_backtest_signals, get_backtest_trades
from chaogu_alert.config import MysqlSettings
from collections import Counter, defaultdict
from datetime import date

s = MysqlSettings(host="192.168.1.137", port=3306, user="root", password="cochain#123", database="claude", enabled=True)
conn = get_connection(s)

signals = get_backtest_signals(conn, 7)
trades = get_backtest_trades(conn, 7)

print("=== Signal Analysis ===")
print(f"Total signals: {len(signals)}")

exec_scores = [float(s['score']) for s in signals if s['executed']]
skip_scores = [float(s['score']) for s in signals if not s['executed']]
if exec_scores:
    print(f"Executed avg score: {sum(exec_scores)/len(exec_scores):.1f}")
if skip_scores:
    print(f"Skipped avg score: {sum(skip_scores)/len(skip_scores):.1f}")

print("\n=== Yearly Trade Breakdown ===")
yearly = defaultdict(lambda: {'trades': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0})
for t in trades:
    entry = t['entry_date']
    yr = entry.year if hasattr(entry, 'year') else date.fromisoformat(str(entry)).year
    yearly[yr]['trades'] += 1
    pnl = float(t['pnl_amount'])
    if pnl > 0:
        yearly[yr]['wins'] += 1
    else:
        yearly[yr]['losses'] += 1
    yearly[yr]['pnl'] += pnl

for yr in sorted(yearly.keys()):
    y = yearly[yr]
    wr = y['wins'] / y['trades'] * 100 if y['trades'] else 0
    print(f"  {yr}: {y['trades']} trades  WR={wr:.0f}%  PnL={y['pnl']:+.0f}")

print("\n=== Exit Reason Distribution ===")
reasons = Counter(t['exit_reason'] for t in trades)
for r, c in reasons.most_common():
    pnl_r = sum(float(t['pnl_amount']) for t in trades if t['exit_reason'] == r)
    print(f"  {r}: {c} ({c/len(trades)*100:.1f}%)  PnL={pnl_r:+.0f}")

eop = sum(1 for t in trades if t['exit_reason'] == 'end_of_period')
print(f"\nEnd-of-period close: {eop} trades")

# Multi-position check
print("\n=== Multi-position Check ===")
by_day_sym = defaultdict(list)
for t in trades:
    entry_date = str(t['entry_date'])
    by_day_sym[(entry_date, t['symbol'])].append(t)
multi = {k: v for k, v in by_day_sym.items() if len(v) > 1}
if multi:
    print(f"WARNING: {len(multi)} duplicate entries on same day")
    for k, v in list(multi.items())[:5]:
        print(f"  {k[0]} {k[1]}: {len(v)} positions")
else:
    print("OK: no duplicate symbol entries")

# Check holding periods
print("\n=== Holding Period Analysis ===")
periods = []
for t in trades:
    entry = t['entry_date']
    exit_d = t['exit_date']
    if exit_d:
        if hasattr(entry, 'year'):
            days = (exit_d - entry).days
        else:
            days = (date.fromisoformat(str(exit_d)) - date.fromisoformat(str(entry))).days
        periods.append(days)

if periods:
    print(f"Avg holding days: {sum(periods)/len(periods):.1f}")
    print(f"Min: {min(periods)}, Max: {max(periods)}")
    # Distribution
    ranges = [(0,5), (6,10), (11,20), (21,40), (41,60), (61,120), (121,999)]
    for lo, hi in ranges:
        cnt = sum(1 for p in periods if lo <= p <= hi)
        if cnt:
            print(f"  {lo}-{hi} days: {cnt} ({cnt/len(periods)*100:.1f}%)")

conn.close()
