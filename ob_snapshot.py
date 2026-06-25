"""One-shot order-book + positioning snapshot (REST only). Runs ONCE and exits -- designed for a
scheduler (GitHub Actions / cron / Task Scheduler) so you don't run a 24/7 process. Appends one row
to ob_cloud.csv. Captures the order-book + positioning features we're testing (no continuous-tape CVD).
    python ob_snapshot.py
"""
import requests, csv, os, statistics
from datetime import datetime, timezone

SYM = "BTCUSDT"
B = "https://fapi.binance.com"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ob_cloud.csv")
FIELDS = ["ts", "mid", "book_imb", "depth_imb25", "book_liq", "spread_bp", "wall_up_bp", "wall_dn_bp",
          "micro_bp", "ls_global", "ls_top", "oi", "oi_chg", "funding", "cvd_proxy", "trend_bp"]


def get(path, **p):
    return requests.get(B + path, params=p, timeout=20).json()


d = get("/fapi/v1/depth", symbol=SYM, limit=1000)
bids = [(float(p), float(q)) for p, q in d["bids"]]
asks = [(float(p), float(q)) for p, q in d["asks"]]
mid = (bids[0][0] + asks[0][0]) / 2
spread_bp = round((asks[0][0] - bids[0][0]) / mid * 1e4, 2)
b0p, b0s = bids[0]; a0p, a0s = asks[0]
micro_bp = round(((b0p*a0s + a0p*b0s)/(b0s+a0s) - mid)/mid*1e4, 2) if b0s+a0s > 0 else 0.0
lo, hi = mid*(1-25/1e4), mid*(1+25/1e4)
bd = sum(s for p, s in bids if p >= lo); ad = sum(s for p, s in asks if p <= hi)
depth_imb25 = round((bd-ad)/(bd+ad), 3) if bd+ad > 0 else 0.0
book_liq = round(bd + ad, 1)
sizes = [s for _, s in bids[:200]] + [s for _, s in asks[:200]]
med = statistics.median(sizes) if sizes else 1.0


def wall(levels, up):
    for p, s in levels:
        dbp = (p-mid)/mid*1e4 if up else (mid-p)/mid*1e4
        if dbp >= 3 and s > 2*med:
            return round(dbp, 1)
    return 0.0


wall_up_bp = wall(asks, True); wall_dn_bp = wall(bids, False)
try:
    ls_global = float(get("/futures/data/globalLongShortAccountRatio", symbol=SYM, period="5m", limit=1)[-1]["longShortRatio"])
except Exception:
    ls_global = 0.0
try:
    ls_top = float(get("/futures/data/topLongShortPositionRatio", symbol=SYM, period="5m", limit=1)[-1]["longShortRatio"])
except Exception:
    ls_top = 0.0
try:
    oi = float(get("/fapi/v1/openInterest", symbol=SYM)["openInterest"])
except Exception:
    oi = 0.0
try:
    funding = float(get("/fapi/v1/premiumIndex", symbol=SYM)["lastFundingRate"])
except Exception:
    funding = 0.0
try:
    tr = get("/fapi/v1/aggTrades", symbol=SYM, limit=1000)
    cvd_proxy = round(sum((float(t["q"]) if not t["m"] else -float(t["q"])) for t in tr), 1)
except Exception:
    cvd_proxy = 0.0

oi_chg = 0.0; trend_bp = 0.0
if os.path.exists(OUT):
    import pandas as pd
    h = pd.read_csv(OUT).tail(20)
    if len(h):
        if h.oi.iloc[-1] > 0:
            oi_chg = round((oi - h.oi.iloc[-1]) / h.oi.iloc[-1] * 100, 3)
        ema = float(h["mid"].iloc[0])
        for m in list(h["mid"])[1:] + [mid]:
            ema = ema + 0.3*(m - ema)
        trend_bp = round((mid - ema)/mid*1e4, 2)

row = dict(ts=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), mid=round(mid, 1),
           book_imb=depth_imb25, depth_imb25=depth_imb25, book_liq=book_liq, spread_bp=spread_bp,
           wall_up_bp=wall_up_bp, wall_dn_bp=wall_dn_bp, micro_bp=micro_bp, ls_global=ls_global,
           ls_top=ls_top, oi=round(oi), oi_chg=oi_chg, funding=funding, cvd_proxy=cvd_proxy, trend_bp=trend_bp)
new = not os.path.exists(OUT)
with open(OUT, "a", newline="") as f:
    w = csv.DictWriter(f, fieldnames=FIELDS)
    if new:
        w.writeheader()
    w.writerow(row)
print("logged", row["ts"], "| mid", row["mid"], "| book_imb", depth_imb25, "| ls_global", ls_global)
