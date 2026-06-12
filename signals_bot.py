#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════╗
║   AURUM AI — Gold & BTC Signals Bot v2       ║
║   Fibonacci + VWAP + 9EMA + SMC + ICT        ║
║   XAUUSD : SL 50 pts | TP 80 pts             ║
║   BTCUSD : SL 400 pts | TP 600 pts           ║
║   Signal : score > 7  (11 confluences)       ║
║   Hosted : GitHub Actions  (24×7 free)       ║
╚══════════════════════════════════════════════╝

Credentials come from GitHub Secrets — never hard-code them here.
Set BOT_TOKEN and CHAT_ID in:
  GitHub repo → Settings → Secrets → Actions
"""

import os
import sys
import time
import logging
import numpy as np
import pandas as pd
import requests
from datetime import datetime

# ═══════════════════════════════════════════════════════════
#  CREDENTIALS — read from GitHub Secrets (env vars)
# ═══════════════════════════════════════════════════════════

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID   = os.environ.get("CHAT_ID",   "")

if not BOT_TOKEN or not CHAT_ID:
    print("ERROR: BOT_TOKEN and CHAT_ID must be set as GitHub Secrets.")
    print("  Repo → Settings → Secrets and variables → Actions → New secret")
    sys.exit(1)

# ═══════════════════════════════════════════════════════════
#  SETTINGS
# ═══════════════════════════════════════════════════════════

# How long (seconds) this script runs before GitHub Actions restarts it.
# Workflow cron = every 5 min → run for 4.5 min → no gap in coverage.
RUN_DURATION_SEC = int(os.environ.get("RUN_DURATION", "270"))  # 4.5 min
SCAN_EVERY_SEC   = 30          # scan every 30 seconds inside loop
CANDLE_TF        = "5m"        # candle timeframe for analysis
DATA_PERIOD      = "2d"        # yfinance lookback
SIGNAL_MIN_SCORE = 8           # fires only when score > 7  (max 14)

# ═══════════════════════════════════════════════════════════
#  ASSETS
# ═══════════════════════════════════════════════════════════

ASSETS = {
    "XAUUSD": {
        "ticker": "GC=F",
        "name":   "Gold",
        "emoji":  "🥇",
        "sl_pts": 50,
        "tp_pts": 80,
        "dec":    2,
    },
    "BTCUSD": {
        "ticker": "BTC-USD",
        "name":   "Bitcoin",
        "emoji":  "₿",
        "sl_pts": 400,
        "tp_pts": 600,
        "dec":    1,
    },
}

# ── Runtime state (within one GitHub Actions job) ───────────
_last_sig_id = {"XAUUSD": None, "BTCUSD": None}
_last_candle = {"XAUUSD": None, "BTCUSD": None}

# ═══════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("AURUM")

# ═══════════════════════════════════════════════════════════
#  TELEGRAM
# ═══════════════════════════════════════════════════════════

def tg(text: str, silent: bool = False) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id":              CHAT_ID,
        "text":                 text,
        "parse_mode":           "HTML",
        "disable_notification": silent,
    }
    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, timeout=20)
            if r.status_code == 200:
                return True
            log.warning(f"Telegram {r.status_code}: {r.text[:120]}")
            return False
        except requests.exceptions.ConnectionError:
            log.warning(f"Telegram attempt {attempt+1} failed, retrying…")
            time.sleep(5)
        except Exception as e:
            log.error(f"Telegram error: {e}")
            return False
    return False

# ═══════════════════════════════════════════════════════════
#  DATA FETCH
# ═══════════════════════════════════════════════════════════

def fetch(ticker: str) -> pd.DataFrame:
    try:
        df = yf.download(ticker, period=DATA_PERIOD, interval=CANDLE_TF,
                         progress=False, auto_adjust=True)
        if df.empty:
            return df
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.dropna(inplace=True)
        return df
    except Exception as e:
        log.error(f"fetch({ticker}): {e}")
        return pd.DataFrame()

def is_new_candle(asset_key: str, df: pd.DataFrame) -> bool:
    ts = str(df.index[-1])
    if ts != _last_candle[asset_key]:
        _last_candle[asset_key] = ts
        return True
    return False

# ═══════════════════════════════════════════════════════════
#  INDICATORS
# ═══════════════════════════════════════════════════════════

def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def calc_rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return float((100 - 100 / (1 + rs)).iloc[-1])

def calc_vwap(df: pd.DataFrame) -> float:
    tp  = (df["High"] + df["Low"] + df["Close"]) / 3
    vol = df["Volume"].replace(0, np.nan).fillna(1)
    return float((tp * vol).cumsum().iloc[-1] / vol.cumsum().iloc[-1])

def calc_fib(df: pd.DataFrame, window: int = 80) -> dict:
    w = df.tail(window)
    hi, lo = float(w["High"].max()), float(w["Low"].min())
    d = hi - lo
    return {
        "high":  hi,  "low":   lo,  "diff": d,
        "0.236": hi - 0.236 * d,    "0.382": hi - 0.382 * d,
        "0.5":   hi - 0.500 * d,    "0.618": hi - 0.618 * d,
        "0.705": hi - 0.705 * d,    "0.786": hi - 0.786 * d,
    }

# ═══════════════════════════════════════════════════════════
#  SMC / ICT CONCEPTS
# ═══════════════════════════════════════════════════════════

def order_blocks(df: pd.DataFrame, window: int = 50):
    w = df.tail(window).reset_index(drop=True)
    o, h, l, c = w["Open"].values, w["High"].values, w["Low"].values, w["Close"].values
    bull_ob = bear_ob = None
    for i in range(len(w) - 2, 0, -1):
        if c[i] < o[i] and bull_ob is None:
            for j in range(i + 1, min(i + 7, len(w))):
                if c[j] > h[i]:
                    bull_ob = {"high": h[i], "low": l[i]}
                    break
        if c[i] > o[i] and bear_ob is None:
            for j in range(i + 1, min(i + 7, len(w))):
                if c[j] < l[i]:
                    bear_ob = {"high": h[i], "low": l[i]}
                    break
        if bull_ob and bear_ob:
            break
    return bull_ob, bear_ob

def fair_value_gaps(df: pd.DataFrame, window: int = 50):
    w = df.tail(window).reset_index(drop=True)
    h, l = w["High"].values, w["Low"].values
    bull_fvg = bear_fvg = None
    for i in range(1, len(w) - 1):
        if l[i + 1] > h[i - 1]:
            bull_fvg = {"top": l[i + 1], "bot": h[i - 1]}
        if h[i + 1] < l[i - 1]:
            bear_fvg = {"top": l[i - 1], "bot": h[i + 1]}
    return bull_fvg, bear_fvg

def bos_choch(df: pd.DataFrame, window: int = 40) -> str:
    w    = df.tail(window)
    half = window // 2
    p_hi = float(w.iloc[:half]["High"].max())
    p_lo = float(w.iloc[:half]["Low"].min())
    last = float(w["Close"].iloc[-1])
    if last > p_hi: return "BULL_BOS"
    if last < p_lo: return "BEAR_BOS"
    return "NONE"

def market_structure(df: pd.DataFrame, window: int = 60) -> str:
    w   = df.tail(window)
    mid = window // 2
    h, l = w["High"].values, w["Low"].values
    if h[mid:].max() > h[:mid].max() and l[mid:].min() > l[:mid].min():
        return "UPTREND"
    if h[mid:].max() < h[:mid].max() and l[mid:].min() < l[:mid].min():
        return "DOWNTREND"
    return "RANGING"

def ict_zone(fib: dict, price: float) -> str:
    return "PREMIUM" if price > fib["0.5"] else "DISCOUNT"

def engulfing(df: pd.DataFrame) -> str:
    if len(df) < 2:
        return "NONE"
    o, c = df["Open"].values, df["Close"].values
    b1 = abs(c[-2] - o[-2]); b2 = abs(c[-1] - o[-1])
    if c[-2] < o[-2] and c[-1] > o[-1] and b2 > b1 and c[-1] > o[-2] and o[-1] < c[-2]:
        return "BULL_ENGULF"
    if c[-2] > o[-2] and c[-1] < o[-1] and b2 > b1 and c[-1] < o[-2] and o[-1] > c[-2]:
        return "BEAR_ENGULF"
    return "NONE"

def liquidity_sweep(df: pd.DataFrame, window: int = 30) -> str:
    w    = df.tail(window)
    prev = w.iloc[:-1]
    last = w.iloc[-1]
    if float(last["Low"])  < float(prev["Low"].min())  and float(last["Close"]) > float(prev["Low"].min()):
        return "BULL_SWEEP"
    if float(last["High"]) > float(prev["High"].max()) and float(last["Close"]) < float(prev["High"].max()):
        return "BEAR_SWEEP"
    return "NONE"

# ═══════════════════════════════════════════════════════════
#  SIGNAL ENGINE  (max score = 14)
# ═══════════════════════════════════════════════════════════

def score_and_signal(asset_key: str, df: pd.DataFrame) -> dict | None:
    if len(df) < 80:
        return None

    asset = ASSETS[asset_key]
    dec   = asset["dec"]
    close = float(df["Close"].iloc[-1])

    ema9  = float(ema(df["Close"],  9).iloc[-1])
    ema21 = float(ema(df["Close"], 21).iloc[-1])
    vwap  = calc_vwap(df)
    rsi   = calc_rsi(df["Close"])
    fib   = calc_fib(df)
    b_ob, r_ob     = order_blocks(df)
    b_fvg, r_fvg   = fair_value_gaps(df)
    bos            = bos_choch(df)
    structure      = market_structure(df)
    zone           = ict_zone(fib, close)
    candle_pat     = engulfing(df)
    liq            = liquidity_sweep(df)
    tol            = fib["diff"] * 0.012

    bs = 0; rs = 0
    bw = []; rw = []

    # 1. 9 EMA
    if close > ema9:   bs += 1; bw.append(f"Price above 9 EMA ({ema9:.{dec}f})")
    else:              rs += 1; rw.append(f"Price below 9 EMA ({ema9:.{dec}f})")

    # 2. EMA alignment
    if ema9 > ema21:   bs += 1; bw.append(f"9 EMA > 21 EMA — bullish alignment")
    else:              rs += 1; rw.append(f"9 EMA < 21 EMA — bearish alignment")

    # 3. VWAP
    if close > vwap:   bs += 1; bw.append(f"Price above VWAP ({vwap:.{dec}f})")
    else:              rs += 1; rw.append(f"Price below VWAP ({vwap:.{dec}f})")

    # 4. RSI
    if rsi < 45:       bs += 1; bw.append(f"RSI oversold ({rsi:.1f}) — buy zone")
    elif rsi > 55:     rs += 1; rw.append(f"RSI overbought ({rsi:.1f}) — sell zone")

    # 5. Market structure
    if structure == "UPTREND":    bs += 1; bw.append("HH + HL structure (uptrend)")
    elif structure == "DOWNTREND":rs += 1; rw.append("LH + LL structure (downtrend)")

    # 6. Fibonacci (+2)
    for name, val in [("61.8%", fib["0.618"]), ("70.5%", fib["0.705"]), ("78.6%", fib["0.786"])]:
        if abs(close - val) <= tol and zone == "DISCOUNT":
            bs += 2; bw.append(f"Fib {name} support @ {val:.{dec}f} (Discount zone)"); break
    for name, val in [("23.6%", fib["0.236"]), ("38.2%", fib["0.382"]), ("50.0%", fib["0.5"])]:
        if abs(close - val) <= tol and zone == "PREMIUM":
            rs += 2; rw.append(f"Fib {name} resistance @ {val:.{dec}f} (Premium zone)"); break

    # 7. Order Block (+2)
    if b_ob:
        ext = b_ob["high"] + (b_ob["high"] - b_ob["low"]) * 0.15
        if b_ob["low"] <= close <= ext:
            bs += 2; bw.append(f"Bullish OB ({b_ob['low']:.{dec}f}–{b_ob['high']:.{dec}f})")
    if r_ob:
        ext = r_ob["low"] - (r_ob["high"] - r_ob["low"]) * 0.15
        if ext <= close <= r_ob["high"]:
            rs += 2; rw.append(f"Bearish OB ({r_ob['low']:.{dec}f}–{r_ob['high']:.{dec}f})")

    # 8. Fair Value Gap (+1)
    if b_fvg and b_fvg["bot"] <= close <= b_fvg["top"]:
        bs += 1; bw.append(f"Inside Bullish FVG ({b_fvg['bot']:.{dec}f}–{b_fvg['top']:.{dec}f})")
    if r_fvg and r_fvg["bot"] <= close <= r_fvg["top"]:
        rs += 1; rw.append(f"Inside Bearish FVG ({r_fvg['bot']:.{dec}f}–{r_fvg['top']:.{dec}f})")

    # 9. BOS / CHoCH (+2)
    if bos == "BULL_BOS":  bs += 2; bw.append("Bullish Break of Structure confirmed")
    elif bos == "BEAR_BOS":rs += 2; rw.append("Bearish Break of Structure confirmed")

    # 10. Engulfing pattern (+1)
    if candle_pat == "BULL_ENGULF": bs += 1; bw.append("Bullish engulfing candle")
    elif candle_pat == "BEAR_ENGULF":rs+= 1; rw.append("Bearish engulfing candle")

    # 11. ICT Liquidity Sweep (+1)
    if liq == "BULL_SWEEP": bs += 1; bw.append("ICT liquidity sweep — buy-side grab")
    elif liq == "BEAR_SWEEP":rs+= 1; rw.append("ICT liquidity sweep — sell-side grab")

    # ── Decision: score MUST be > 7 ─────────────────────
    direction = None; reasons = []; score = 0
    if bs > 7 and bs > rs: direction = "BUY";  reasons = bw; score = bs
    elif rs > 7 and rs > bs: direction = "SELL"; reasons = rw; score = rs
    if not direction:
        return None

    sl_p = asset["sl_pts"]; tp_p = asset["tp_pts"]
    if direction == "BUY":
        entry = round(close, dec); sl = round(close - sl_p, dec); tp = round(close + tp_p, dec)
    else:
        entry = round(close, dec); sl = round(close + sl_p, dec); tp = round(close - tp_p, dec)

    return {
        "direction": direction, "entry": entry, "sl": sl, "tp": tp,
        "ema9":  round(ema9,  dec), "ema21": round(ema21, dec),
        "vwap":  round(vwap,  dec), "rsi":   round(rsi,   1),
        "fib_236": round(fib["0.236"], dec), "fib_382": round(fib["0.382"], dec),
        "fib_50":  round(fib["0.5"],   dec), "fib_618": round(fib["0.618"], dec),
        "fib_786": round(fib["0.786"], dec),
        "fib_hi":  round(fib["high"],  dec), "fib_lo":  round(fib["low"],   dec),
        "structure": structure, "zone": zone, "bos": bos,
        "bull_s": bs, "bear_s": rs, "score": score, "reasons": reasons,
    }

# ═══════════════════════════════════════════════════════════
#  MESSAGE FORMATTER
# ═══════════════════════════════════════════════════════════

def fmt_msg(asset_key: str, sig: dict) -> str:
    asset = ASSETS[asset_key]
    dec   = asset["dec"]
    now   = datetime.utcnow().strftime("%d %b %Y  %H:%M UTC")
    rr    = round(asset["tp_pts"] / asset["sl_pts"], 2)

    d_icon = "🟢 <b>BUY  ▲</b>" if sig["direction"] == "BUY" else "🔴 <b>SELL ▼</b>"
    z_icon = "🟦 Discount (buy zone)" if sig["zone"] == "DISCOUNT" else "🟥 Premium (sell zone)"
    s_map  = {"UPTREND": "📈 Uptrend (HH·HL)", "DOWNTREND": "📉 Downtrend (LH·LL)", "RANGING": "↔️ Ranging"}
    b_map  = {"BULL_BOS": "⚡ Bullish BOS", "BEAR_BOS": "⚡ Bearish BOS", "NONE": "—"}
    conf   = "🔥 ULTRA HIGH" if sig["score"] >= 11 else "✅ HIGH" if sig["score"] >= 8 else "—"
    stars  = "⭐" * min(sig["score"], 12)
    reasons = "\n".join(f"   ✅ {r}" for r in sig["reasons"])

    return (
        f"{asset['emoji']} <b>AURUM AI  —  {asset['name']} ({asset_key})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{d_icon}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 <b>Entry  :</b>  <code>{sig['entry']:.{dec}f}</code>\n"
        f"🛑 <b>SL     :</b>  <code>{sig['sl']:.{dec}f}</code>  ({asset['sl_pts']} pts)\n"
        f"🎯 <b>TP     :</b>  <code>{sig['tp']:.{dec}f}</code>  ({asset['tp_pts']} pts)\n"
        f"📊 <b>R : R  :</b>  1 : {rr}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 9 EMA   : {sig['ema9']:.{dec}f}\n"
        f"📉 21 EMA  : {sig['ema21']:.{dec}f}\n"
        f"💧 VWAP    : {sig['vwap']:.{dec}f}\n"
        f"📊 RSI(14) : {sig['rsi']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📐 <b>Fibonacci</b>\n"
        f"   🔝 Swing High : {sig['fib_hi']:.{dec}f}\n"
        f"   ·  23.6%      : {sig['fib_236']:.{dec}f}\n"
        f"   ·  38.2%      : {sig['fib_382']:.{dec}f}\n"
        f"   ➡️ 50.0%       : {sig['fib_50']:.{dec}f}\n"
        f"   ·  61.8%      : {sig['fib_618']:.{dec}f}\n"
        f"   ·  78.6%      : {sig['fib_786']:.{dec}f}\n"
        f"   🔻 Swing Low  : {sig['fib_lo']:.{dec}f}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🧠 <b>SMC / ICT Confluence</b>\n"
        f"{reasons}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏗 Structure : {s_map.get(sig['structure'], sig['structure'])}\n"
        f"🗺 ICT Zone  : {z_icon}\n"
        f"⚡ BOS       : {b_map.get(sig['bos'], sig['bos'])}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔥 Confidence: {conf}\n"
        f"⭐ Score     : {stars}  ({sig['score']}/14)\n"
        f"🕐 {now}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <i>Trade responsibly. Not financial advice.</i>"
    )

# ═══════════════════════════════════════════════════════════
#  MAIN — loop for RUN_DURATION_SEC then exit cleanly
# ═══════════════════════════════════════════════════════════

def main():
    log.info(f"AURUM AI starting — will run for {RUN_DURATION_SEC}s then exit")
    start = time.time()

    while (time.time() - start) < RUN_DURATION_SEC:
        for asset_key, asset in ASSETS.items():
            try:
                df = fetch(asset["ticker"])
                if df.empty:
                    log.warning(f"{asset_key}: no data")
                    continue

                close = float(df["Close"].iloc[-1])
                log.info(f"[{asset_key}]  close={close:.{asset['dec']}f}  candles={len(df)}")

                if not is_new_candle(asset_key, df):
                    continue

                sig = score_and_signal(asset_key, df)
                if sig is None:
                    log.info(f"  {asset_key}: score ≤ 7, no signal")
                    continue

                sig_id = f"{sig['direction']}_{sig['entry']}"
                if sig_id == _last_sig_id[asset_key]:
                    log.info(f"  {asset_key}: same signal already sent")
                    continue

                _last_sig_id[asset_key] = sig_id
                msg = fmt_msg(asset_key, sig)
                if tg(msg):
                    log.info(f"  ✅ SIGNAL SENT  {asset_key} {sig['direction']} "
                             f"@ {sig['entry']}  score={sig['score']}/14")

            except Exception as e:
                log.exception(f"Error scanning {asset_key}: {e}")

        elapsed = int(time.time() - start)
        remaining = RUN_DURATION_SEC - elapsed
        log.info(f"Elapsed {elapsed}s / {RUN_DURATION_SEC}s  — next scan in {SCAN_EVERY_SEC}s")
        time.sleep(min(SCAN_EVERY_SEC, max(0, remaining)))

    log.info("Run duration complete — exiting cleanly for GitHub Actions restart")


if __name__ == "__main__":
    main()