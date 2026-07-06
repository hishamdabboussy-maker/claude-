"""BURST order-book collector from HYPERLIQUID. Each run loops for ~20 min taking a snapshot every
20s (~60 dense rows/run) -- works around GitHub Actions throttling frequent crons. Appends to
ob_cloud.csv. Captures the order-book features we're testing + funding + OI.
    python ob_snapshot.py
"""
import requests, csv, os, statistics, time
from datetime import datetime, timezone

API = "https://api.hyperliquid.xyz/info"
COIN = "BTC"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ob_cloud.csv")
FIELDS = ["ts", "mid", "book_imb", "depth_imb25", "book_liq", "spread_bp", "wall_up_bp", "wall_dn_bp",
          "micro_bp", "oi", "oi_chg", "funding", "trend_bp"]
HDR = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
RUN_SECONDS = 1200   # ~20 min burst per GitHub run
INTERVAL = 20        # snapshot every 20s -> ~60 rows/run


def post(body):
    r = requests.post(API, json=body, headers=HDR, timeout=20)
    return r.json()


def snapshot(recent_mids, last_oi):
    book = post({"type": "l2Book", "coin": COIN})
    if "levels" not in book:
        raise RuntimeError(f"bad l2Book: {str(book)[:200]}")
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

    oi = last_oi or 0.0; funding = 0.0
    try:
        meta, ctxs = post({"type": "metaAndAssetCtxs"})
        idx = [u["name"] for u in meta["universe"]].index(COIN)
        c = ctxs[idx]; oi = float(c.get("openInterest", 0)); funding = float(c.get("funding", 0))
    except Exception:
        pass
    oi_chg = round((oi - last_oi)/last_oi*100, 4) if last_oi else 0.0
    trend_bp = 0.0
    if recent_mids:
        ema = recent_mids[0]
        for m in recent_mids[1:] + [mid]:
            ema = ema + 0.3*(m - ema)
        trend_bp = round((mid - ema)/mid*1e4, 2)
    return dict(ts=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), mid=round(mid, 1),
                book_imb=depth_imb25, depth_imb25=depth_imb25, book_liq=book_liq, spread_bp=spread_bp,
                wall_up_bp=wall(asks, True), wall_dn_bp=wall(bids, False), micro_bp=micro_bp,
                oi=round(oi, 2), oi_chg=oi_chg, funding=funding, trend_bp=trend_bp)


recent_mids, last_oi = [], None
if os.path.exists(OUT):
    import pandas as pd
    h = pd.read_csv(OUT).tail(20)
    if len(h):
        recent_mids = [float(x) for x in h["mid"]]
        if h.oi.iloc[-1] > 0:
            last_oi = float(h.oi.iloc[-1])

rows, t0 = [], time.time()
while time.time() - t0 < RUN_SECONDS:
    try:
        row = snapshot(recent_mids, last_oi)
        recent_mids = (recent_mids + [row["mid"]])[-20:]
        last_oi = row["oi"]
        rows.append(row)
    except Exception as e:
        print("snap err:", e)
    time.sleep(INTERVAL)

new = not os.path.exists(OUT)
with open(OUT, "a", newline="") as f:
    w = csv.DictWriter(f, fieldnames=FIELDS)
    if new:
        w.writeheader()
    for r in rows:
        w.writerow(r)
print(f"burst done: logged {len(rows)} rows over {RUN_SECONDS//60} min")
