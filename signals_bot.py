#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════╗
║   AURUM AI — Gold & BTC Signals Bot v4       ║
║   Data Source : Kraken API (no key needed)   ║
║   XAUUSD : SL 50 pts | TP 80 pts             ║
║   BTCUSD : SL 400 pts | TP 600 pts           ║
║   Signal : score > 7  (11 confluences)       ║
║   Hosted : GitHub Actions  (24×7 free)       ║
╚══════════════════════════════════════════════╝
"""

import os, sys, time, logging
import numpy as np
import pandas as pd
import requests
from datetime import datetime

# ═══════════════════════════════════════════════════════════
#  CREDENTIALS — from GitHub Secrets
# ═══════════════════════════════════════════════════════════

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID   = os.environ.get("CHAT_ID",   "")

if not BOT_TOKEN or not CHAT_ID:
    print("ERROR: Set BOT_TOKEN and CHAT_ID as GitHub Secrets")
    sys.exit(1)

# ═══════════════════════════════════════════════════════════
#  SETTINGS
# ═══════════════════════════════════════════════════════════

RUN_DURATION_SEC = int(os.environ.get("RUN_DURATION", "270"))
SCAN_EVERY_SEC   = 30
SIGNAL_MIN_SCORE = 8          # fires when score > 7

# Kraken public REST API — no key, no geo-block, works on GitHub Actions
KRAKEN_OHLC = "https://api.kraken.com/0/public/OHLC"
KRAKEN_TICK = "https://api.kraken.com/0/public/Ticker"

# ═══════════════════════════════════════════════════════════
#  ASSETS  — Kraken pair names
# ═══════════════════════════════════════════════════════════

ASSETS = {
    "XAUUSD": {
        "pair":   "XAUUSD",       # Spot Gold / USD on Kraken
        "name":   "Gold",
        "emoji":  "🥇",
        "sl_pts": 50,
        "tp_pts": 80,
        "dec":    2,
    },
    "BTCUSD": {
        "pair":   "XXBTZUSD",     # BTC / USD on Kraken
        "name":   "Bitcoin",
        "emoji":  "₿",
        "sl_pts": 400,
        "tp_pts": 600,
        "dec":    1,
    },
}

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
    url     = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text,
               "parse_mode": "HTML", "disable_notification": silent}
    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, timeout=20)
            if r.status_code == 200:
                return True
            log.warning(f"Telegram {r.status_code}: {r.text[:100]}")
            return False
        except Exception as e:
            log.warning(f"Telegram attempt {attempt+1}: {e}")
            time.sleep(5)
    return False

# ═══════════════════════════════════════════════════════════
#  DATA FETCH — Kraken OHLC API
# ═══════════════════════════════════════════════════════════

def fetch(pair: str, interval: int = 5, limit: int = 200) -> pd.DataFrame:
    """
    Fetch OHLCV candles from Kraken public API.
    interval: minutes — 1,5,15,30,60,240,1440
    Returns DataFrame with columns: Open, High, Low, Close, Volume
    """
    for attempt in range(3):
        try:
            r = requests.get(
                KRAKEN_OHLC,
                params={"pair": pair, "interval": interval},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()

            if data.get("error"):
                log.warning(f"Kraken error for {pair}: {data['error']}")
                time.sleep(5)
                continue

            result = data.get("result", {})
            # Kraken may return a slightly different key name (e.g. XXBTZUSD → XBTUSD)
            key = next((k for k in result if k != "last"), None)
            if not key:
                log.warning(f"fetch({pair}): no result key found")
                continue

            candles = result[key]
            # Kraken OHLC: [time, open, high, low, close, vwap, volume, count]
            df = pd.DataFrame(candles, columns=[
                "timestamp","Open","High","Low","Close","vwap","Volume","count"
            ])
            df = df[["timestamp","Open","High","Low","Close","Volume"]].copy()
            df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="s", utc=True)
            df.set_index("timestamp", inplace=True)
            for col in ["Open","High","Low","Close","Volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df.dropna(inplace=True)
            df = df.tail(limit)
            log.info(f"fetch({pair}): {len(df)} candles, last close={df['Close'].iloc[-1]:.2f}")
            return df

        except Exception as e:
            log.warning(f"fetch({pair}) attempt {attempt+1}: {e}")
            time.sleep(5)

    log.error(f"fetch({pair}): all attempts failed")
    return pd.DataFrame()

def is_new_candle(key: str, df: pd.DataFrame) -> bool:
    ts = str(df.index[-1])
    if ts != _last_candle[key]:
        _last_candle[key] = ts
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
        "high": hi, "low": lo, "diff": d,
        "0.236": hi - 0.236*d, "0.382": hi - 0.382*d,
        "0.5":   hi - 0.500*d, "0.618": hi - 0.618*d,
        "0.705": hi - 0.705*d, "0.786": hi - 0.786*d,
    }

# ═══════════════════════════════════════════════════════════
#  SMC / ICT
# ═══════════════════════════════════════════════════════════

def order_blocks(df: pd.DataFrame, window: int = 50):
    w = df.tail(window).reset_index(drop=True)
    o, h, l, c = w["Open"].values, w["High"].values, w["Low"].values, w["Close"].values
    bull_ob = bear_ob = None
    for i in range(len(w)-2, 0, -1):
        if c[i]<o[i] and bull_ob is None:
            for j in range(i+1, min(i+7, len(w))):
                if c[j]>h[i]: bull_ob={"high":h[i],"low":l[i]}; break
        if c[i]>o[i] and bear_ob is None:
            for j in range(i+1, min(i+7, len(w))):
                if c[j]<l[i]: bear_ob={"high":h[i],"low":l[i]}; break
        if bull_ob and bear_ob: break
    return bull_ob, bear_ob

def fair_value_gaps(df: pd.DataFrame, window: int = 50):
    w = df.tail(window).reset_index(drop=True)
    h, l = w["High"].values, w["Low"].values
    bull_fvg = bear_fvg = None
    for i in range(1, len(w)-1):
        if l[i+1]>h[i-1]: bull_fvg={"top":l[i+1],"bot":h[i-1]}
        if h[i+1]<l[i-1]: bear_fvg={"top":l[i-1],"bot":h[i+1]}
    return bull_fvg, bear_fvg

def bos_choch(df: pd.DataFrame, window: int = 40) -> str:
    w=df.tail(window); half=window//2
    p_hi=float(w.iloc[:half]["High"].max())
    p_lo=float(w.iloc[:half]["Low"].min())
    last=float(w["Close"].iloc[-1])
    if last>p_hi: return "BULL_BOS"
    if last<p_lo: return "BEAR_BOS"
    return "NONE"

def market_structure(df: pd.DataFrame, window: int = 60) -> str:
    w=df.tail(window); mid=window//2
    h,l=w["High"].values,w["Low"].values
    if h[mid:].max()>h[:mid].max() and l[mid:].min()>l[:mid].min(): return "UPTREND"
    if h[mid:].max()<h[:mid].max() and l[mid:].min()<l[:mid].min(): return "DOWNTREND"
    return "RANGING"

def ict_zone(fib: dict, price: float) -> str:
    return "PREMIUM" if price>fib["0.5"] else "DISCOUNT"

def engulfing(df: pd.DataFrame) -> str:
    if len(df)<2: return "NONE"
    o,c=df["Open"].values,df["Close"].values
    b1,b2=abs(c[-2]-o[-2]),abs(c[-1]-o[-1])
    if c[-2]<o[-2] and c[-1]>o[-1] and b2>b1 and c[-1]>o[-2] and o[-1]<c[-2]: return "BULL_ENGULF"
    if c[-2]>o[-2] and c[-1]<o[-1] and b2>b1 and c[-1]<o[-2] and o[-1]>c[-2]: return "BEAR_ENGULF"
    return "NONE"

def liquidity_sweep(df: pd.DataFrame, window: int = 30) -> str:
    w=df.tail(window); prev=w.iloc[:-1]; last=w.iloc[-1]
    if float(last["Low"])<float(prev["Low"].min()) and float(last["Close"])>float(prev["Low"].min()): return "BULL_SWEEP"
    if float(last["High"])>float(prev["High"].max()) and float(last["Close"])<float(prev["High"].max()): return "BEAR_SWEEP"
    return "NONE"

# ═══════════════════════════════════════════════════════════
#  SIGNAL ENGINE  (max score = 14)
# ═══════════════════════════════════════════════════════════

def score_and_signal(asset_key: str, df: pd.DataFrame):
    if len(df)<80: return None
    asset=ASSETS[asset_key]; dec=asset["dec"]
    close=float(df["Close"].iloc[-1])

    ema9  = float(ema(df["Close"],  9).iloc[-1])
    ema21 = float(ema(df["Close"], 21).iloc[-1])
    vwap  = calc_vwap(df)
    rsi   = calc_rsi(df["Close"])
    fib   = calc_fib(df)
    b_ob,r_ob     = order_blocks(df)
    b_fvg,r_fvg   = fair_value_gaps(df)
    bos           = bos_choch(df)
    structure     = market_structure(df)
    zone          = ict_zone(fib, close)
    candle_pat    = engulfing(df)
    liq           = liquidity_sweep(df)
    tol           = fib["diff"]*0.012

    bs=0; rs=0; bw=[]; rw=[]

    if close>ema9:  bs+=1; bw.append(f"Price above 9 EMA ({ema9:.{dec}f})")
    else:           rs+=1; rw.append(f"Price below 9 EMA ({ema9:.{dec}f})")
    if ema9>ema21:  bs+=1; bw.append("9 EMA > 21 EMA — bullish alignment")
    else:           rs+=1; rw.append("9 EMA < 21 EMA — bearish alignment")
    if close>vwap:  bs+=1; bw.append(f"Price above VWAP ({vwap:.{dec}f})")
    else:           rs+=1; rw.append(f"Price below VWAP ({vwap:.{dec}f})")
    if rsi<45:      bs+=1; bw.append(f"RSI oversold ({rsi:.1f})")
    elif rsi>55:    rs+=1; rw.append(f"RSI overbought ({rsi:.1f})")
    if structure=="UPTREND":    bs+=1; bw.append("HH+HL structure — uptrend")
    elif structure=="DOWNTREND":rs+=1; rw.append("LH+LL structure — downtrend")

    for name,val in [("61.8%",fib["0.618"]),("70.5%",fib["0.705"]),("78.6%",fib["0.786"])]:
        if abs(close-val)<=tol and zone=="DISCOUNT": bs+=2; bw.append(f"Fib {name} support @ {val:.{dec}f}"); break
    for name,val in [("23.6%",fib["0.236"]),("38.2%",fib["0.382"]),("50.0%",fib["0.5"])]:
        if abs(close-val)<=tol and zone=="PREMIUM":  rs+=2; rw.append(f"Fib {name} resistance @ {val:.{dec}f}"); break

    if b_ob:
        ext=b_ob["high"]+(b_ob["high"]-b_ob["low"])*0.15
        if b_ob["low"]<=close<=ext: bs+=2; bw.append(f"Bullish OB ({b_ob['low']:.{dec}f}–{b_ob['high']:.{dec}f})")
    if r_ob:
        ext=r_ob["low"]-(r_ob["high"]-r_ob["low"])*0.15
        if ext<=close<=r_ob["high"]: rs+=2; rw.append(f"Bearish OB ({r_ob['low']:.{dec}f}–{r_ob['high']:.{dec}f})")

    if b_fvg and b_fvg["bot"]<=close<=b_fvg["top"]: bs+=1; bw.append(f"Bullish FVG ({b_fvg['bot']:.{dec}f}–{b_fvg['top']:.{dec}f})")
    if r_fvg and r_fvg["bot"]<=close<=r_fvg["top"]: rs+=1; rw.append(f"Bearish FVG ({r_fvg['bot']:.{dec}f}–{r_fvg['top']:.{dec}f})")

    if bos=="BULL_BOS":   bs+=2; bw.append("Bullish BOS confirmed")
    elif bos=="BEAR_BOS": rs+=2; rw.append("Bearish BOS confirmed")

    if candle_pat=="BULL_ENGULF":  bs+=1; bw.append("Bullish engulfing candle")
    elif candle_pat=="BEAR_ENGULF":rs+=1; rw.append("Bearish engulfing candle")

    if liq=="BULL_SWEEP":  bs+=1; bw.append("ICT liquidity sweep — buy-side grab")
    elif liq=="BEAR_SWEEP":rs+=1; rw.append("ICT liquidity sweep — sell-side grab")

    direction=None; reasons=[]; score=0
    if bs>7 and bs>rs: direction="BUY";  reasons=bw; score=bs
    elif rs>7 and rs>bs: direction="SELL"; reasons=rw; score=rs
    if not direction: return None

    sl_p=asset["sl_pts"]; tp_p=asset["tp_pts"]
    if direction=="BUY":
        entry=round(close,dec); sl=round(close-sl_p,dec); tp=round(close+tp_p,dec)
    else:
        entry=round(close,dec); sl=round(close+sl_p,dec); tp=round(close-tp_p,dec)

    return {
        "direction":direction,"entry":entry,"sl":sl,"tp":tp,
        "ema9":round(ema9,dec),"ema21":round(ema21,dec),
        "vwap":round(vwap,dec),"rsi":round(rsi,1),
        "fib_236":round(fib["0.236"],dec),"fib_382":round(fib["0.382"],dec),
        "fib_50":round(fib["0.5"],dec),"fib_618":round(fib["0.618"],dec),
        "fib_786":round(fib["0.786"],dec),
        "fib_hi":round(fib["high"],dec),"fib_lo":round(fib["low"],dec),
        "structure":structure,"zone":zone,"bos":bos,
        "bull_s":bs,"bear_s":rs,"score":score,"reasons":reasons,
    }

# ═══════════════════════════════════════════════════════════
#  MESSAGE
# ═══════════════════════════════════════════════════════════

def fmt_msg(asset_key: str, sig: dict) -> str:
    asset=ASSETS[asset_key]; dec=asset["dec"]
    now=datetime.utcnow().strftime("%d %b %Y  %H:%M UTC")
    rr=round(asset["tp_pts"]/asset["sl_pts"],2)
    d_icon="🟢 <b>BUY  ▲</b>" if sig["direction"]=="BUY" else "🔴 <b>SELL ▼</b>"
    z_icon="🟦 Discount (buy zone)" if sig["zone"]=="DISCOUNT" else "🟥 Premium (sell zone)"
    s_map={"UPTREND":"📈 Uptrend (HH·HL)","DOWNTREND":"📉 Downtrend (LH·LL)","RANGING":"↔️ Ranging"}
    b_map={"BULL_BOS":"⚡ Bullish BOS","BEAR_BOS":"⚡ Bearish BOS","NONE":"—"}
    conf="🔥 ULTRA HIGH" if sig["score"]>=11 else "✅ HIGH"
    stars="⭐"*min(sig["score"],12)
    reasons="\n".join(f"   ✅ {r}" for r in sig["reasons"])
    return (
        f"{asset['emoji']} <b>AURUM AI — {asset['name']} ({asset_key})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{d_icon}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 <b>Entry :</b> <code>{sig['entry']:.{dec}f}</code>\n"
        f"🛑 <b>SL    :</b> <code>{sig['sl']:.{dec}f}</code>  ({asset['sl_pts']} pts)\n"
        f"🎯 <b>TP    :</b> <code>{sig['tp']:.{dec}f}</code>  ({asset['tp_pts']} pts)\n"
        f"📊 <b>R:R   :</b> 1 : {rr}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 9 EMA  : {sig['ema9']:.{dec}f}\n"
        f"📉 21 EMA : {sig['ema21']:.{dec}f}\n"
        f"💧 VWAP   : {sig['vwap']:.{dec}f}\n"
        f"📊 RSI(14): {sig['rsi']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📐 <b>Fibonacci</b>\n"
        f"   🔝 High  : {sig['fib_hi']:.{dec}f}\n"
        f"   · 23.6%  : {sig['fib_236']:.{dec}f}\n"
        f"   · 38.2%  : {sig['fib_382']:.{dec}f}\n"
        f"   ➡️ 50.0%  : {sig['fib_50']:.{dec}f}\n"
        f"   · 61.8%  : {sig['fib_618']:.{dec}f}\n"
        f"   · 78.6%  : {sig['fib_786']:.{dec}f}\n"
        f"   🔻 Low   : {sig['fib_lo']:.{dec}f}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🧠 <b>SMC / ICT Confluence</b>\n{reasons}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏗 Structure : {s_map.get(sig['structure'],sig['structure'])}\n"
        f"🗺 ICT Zone  : {z_icon}\n"
        f"⚡ BOS       : {b_map.get(sig['bos'],sig['bos'])}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔥 Confidence: {conf}\n"
        f"⭐ Score     : {stars} ({sig['score']}/14)\n"
        f"🕐 {now}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <i>Trade responsibly. Not financial advice.</i>"
    )

# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════

def main():
    log.info(f"AURUM AI v4 — Kraken API — running for {RUN_DURATION_SEC}s")
    start = time.time()

    while (time.time()-start) < RUN_DURATION_SEC:
        for asset_key, asset in ASSETS.items():
            try:
                df = fetch(asset["pair"])
                if df.empty:
                    log.warning(f"{asset_key}: no data"); continue

                if not is_new_candle(asset_key, df):
                    continue

                sig = score_and_signal(asset_key, df)
                if sig is None:
                    log.info(f"  {asset_key}: score<=7, no signal"); continue

                sig_id = f"{sig['direction']}_{sig['entry']}"
                if sig_id == _last_sig_id[asset_key]:
                    log.info(f"  {asset_key}: duplicate, skip"); continue

                _last_sig_id[asset_key] = sig_id
                if tg(fmt_msg(asset_key, sig)):
                    log.info(f"  ✅ SENT {asset_key} {sig['direction']} @ {sig['entry']} score={sig['score']}/14")

            except Exception as e:
                log.exception(f"Error {asset_key}: {e}")

        elapsed   = int(time.time()-start)
        remaining = RUN_DURATION_SEC - elapsed
        log.info(f"Elapsed {elapsed}s — next scan in {SCAN_EVERY_SEC}s")
        time.sleep(min(SCAN_EVERY_SEC, max(0, remaining)))

    log.info("Done — GitHub Actions restarts in 5 min")

if __name__ == "__main__":
    main()