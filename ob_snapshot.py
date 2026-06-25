"""One-shot order-book snapshot from HYPERLIQUID (not geo-blocked from cloud runners; it's also the
user's actual venue). Runs ONCE and exits -- for a scheduler (GitHub Actions / cron). Appends one row
to ob_cloud.csv. Captures the order-book features we're testing + funding (positioning proxy) + OI.
    python ob_snapshot.py
"""
import requests, csv, os, statistics
from datetime import datetime, timezone

API = "https://api.hyperliquid.xyz/info"
COIN = "BTC"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ob_cloud.csv")
FIELDS = ["ts", "mid", "book_imb", "depth_imb25", "book_liq", "spread_bp", "wall_up_bp", "wall_dn_bp",
          "micro_bp", "oi", "oi_chg", "funding", "trend_bp"]


def post(body):
    return requests.post(API, json=body, timeout=20).json()


book = post({"type": "l2Book", "coin": COIN})
levels = book["levels"]
bids = [(float(l["px"]), float(l["sz"])) for l in levels[0]]
asks = [(float(l["px"]), float(l["sz"])) for l in levels[1]]
mid = (bids[0][0] + asks[0][0]) / 2
spread_bp = round((asks[0][0] - bids[0][0]) / mid * 1e4, 2)
b0p, b0s = bids[0]; a0p, a0s = asks[0]
micro_bp = round(((b0p*a0s + a0p*b0s)/(b0s+a0s) - mid)/mid*1e4, 2) if b0s+a0s > 0 else 0.0
lo, hi = mid*(1-25/1e4), mid*(1+25/1e4)
bd = sum(s for p, s in bids if p >= lo); ad = sum(s for p, s in asks if p <= hi)
depth_imb25 = round((bd-ad)/(bd+ad), 3) if bd+ad > 0 else 0.0
book_liq = round(bd + ad, 1)
sizes = [s for _, s in bids] + [s for _, s in asks]
med = statistics.median(sizes) if sizes else 1.0


def wall(levs, up):
    for p, s in levs:
        dbp = (p-mid)/mid*1e4 if up else (mid-p)/mid*1e4
        if dbp >= 3 and s > 2*med:
            return round(dbp, 1)
    return 0.0


wall_up_bp = wall(asks, True); wall_dn_bp = wall(bids, False)
oi = 0.0; funding = 0.0
try:
    meta, ctxs = post({"type": "metaAndAssetCtxs"})
    idx = [u["name"] for u in meta["universe"]].index(COIN)
    c = ctxs[idx]; oi = float(c.get("openInterest", 0)); funding = float(c.get("funding", 0))
except Exception as e:
    print("ctx err", e)

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
           wall_up_bp=wall_up_bp, wall_dn_bp=wall_dn_bp, micro_bp=micro_bp, oi=round(oi, 2),
           oi_chg=oi_chg, funding=funding, trend_bp=trend_bp)
new = not os.path.exists(OUT)
with open(OUT, "a", newline="") as f:
    w = csv.DictWriter(f, fieldnames=FIELDS)
    if new:
        w.writeheader()
    w.writerow(row)
print("logged", row["ts"], "| mid", row["mid"], "| book_imb", depth_imb25, "| funding", funding, "| oi", round(oi, 1))
