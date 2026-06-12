#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════╗
║   AURUM AI — Gold & BTC Signals Bot v5       ║
║   Gold : OKX API  (XAU-USDT)                ║
║   BTC  : Kraken API (XXBTZUSD) ✅            ║
║   XAUUSD : SL 50 pts | TP 80 pts             ║
║   BTCUSD : SL 400 pts | TP 600 pts           ║
║   Signal : score > 7                         ║
╚══════════════════════════════════════════════╝
"""

import os, sys, time, logging
import numpy as np
import pandas as pd
import requests
from datetime import datetime

# ═══════════════════════════════════════════════════════════
#  CREDENTIALS
# ═══════════════════════════════════════════════════════════
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID   = os.environ.get("CHAT_ID",   "")
if not BOT_TOKEN or not CHAT_ID:
    print("ERROR: Set BOT_TOKEN and CHAT_ID as GitHub Secrets")
    sys.exit(1)

RUN_DURATION_SEC = int(os.environ.get("RUN_DURATION", "270"))
SCAN_EVERY_SEC   = 30
SIGNAL_MIN_SCORE = 8

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
log = logging.getLogger("AURUM")

# ═══════════════════════════════════════════════════════════
#  ASSETS
# ═══════════════════════════════════════════════════════════
ASSETS = {
    "XAUUSD": {"name":"Gold",    "emoji":"🥇","sl_pts":50, "tp_pts":80, "dec":2},
    "BTCUSD": {"name":"Bitcoin", "emoji":"₿", "sl_pts":400,"tp_pts":600,"dec":1},
}
_last_sig_id = {"XAUUSD":None,"BTCUSD":None}
_last_candle = {"XAUUSD":None,"BTCUSD":None}

# ═══════════════════════════════════════════════════════════
#  TELEGRAM
# ═══════════════════════════════════════════════════════════
def tg(text, silent=False):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for attempt in range(3):
        try:
            r = requests.post(url, json={"chat_id":CHAT_ID,"text":text,
                "parse_mode":"HTML","disable_notification":silent}, timeout=20)
            if r.status_code == 200: return True
            log.warning(f"TG {r.status_code}: {r.text[:80]}")
            return False
        except Exception as e:
            log.warning(f"TG attempt {attempt+1}: {e}"); time.sleep(5)
    return False

# ═══════════════════════════════════════════════════════════
#  DATA FETCH
# ═══════════════════════════════════════════════════════════

def _to_df(rows, col_map):
    """Convert list-of-lists to OHLCV DataFrame."""
    df = pd.DataFrame(rows)
    df = df[list(col_map.keys())].copy()
    df.columns = list(col_map.values())
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(float), unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    for c in ["Open","High","Low","Close","Volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df.dropna(inplace=True)
    return df.sort_index()


def fetch_gold_okx(limit=200) -> pd.DataFrame:
    """OKX public API — XAU-USDT spot, no key needed."""
    # OKX returns newest first, so we reverse
    url = "https://www.okx.com/api/v5/market/candles"
    r = requests.get(url,
        params={"instId":"XAU-USDT","bar":"5m","limit":str(limit)},
        timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != "0":
        raise Exception(f"OKX error: {data.get('msg')}")
    # columns: ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm
    rows = data["data"]
    df = pd.DataFrame(rows, columns=[
        "timestamp","Open","High","Low","Close","Volume","volCcy","volCcyQuote","confirm"])
    df = df[["timestamp","Open","High","Low","Close","Volume"]].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(float), unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    for c in ["Open","High","Low","Close","Volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df.dropna(inplace=True)
    df.sort_index(inplace=True)   # OKX returns newest first — sort ascending
    return df


def fetch_gold_bitget(limit=200) -> pd.DataFrame:
    """Bitget public API — XAUUSDT fallback."""
    url = "https://api.bitget.com/api/v2/spot/market/candles"
    r = requests.get(url,
        params={"symbol":"XAUUSDT","granularity":"5min","limit":str(limit)},
        timeout=15)
    r.raise_for_status()
    data = r.json()
    if str(data.get("code")) != "00000":
        raise Exception(f"Bitget error: {data.get('msg')}")
    rows = data["data"]
    df = pd.DataFrame(rows, columns=[
        "timestamp","Open","High","Low","Close","Volume","quoteVol"])
    df = df[["timestamp","Open","High","Low","Close","Volume"]].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(float), unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    for c in ["Open","High","Low","Close","Volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df.dropna(inplace=True)
    df.sort_index(inplace=True)
    return df


def fetch_btc_kraken(limit=200) -> pd.DataFrame:
    """Kraken OHLC — XXBTZUSD (already confirmed working)."""
    r = requests.get("https://api.kraken.com/0/public/OHLC",
        params={"pair":"XXBTZUSD","interval":5}, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("error"): raise Exception(data["error"])
    result = data["result"]
    key = next(k for k in result if k != "last")
    df = pd.DataFrame(result[key], columns=[
        "timestamp","Open","High","Low","Close","vwap","Volume","count"])
    df = df[["timestamp","Open","High","Low","Close","Volume"]].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="s", utc=True)
    df.set_index("timestamp", inplace=True)
    for c in ["Open","High","Low","Close","Volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df.dropna(inplace=True)
    return df.tail(limit)


def fetch(asset_key: str) -> pd.DataFrame:
    """Route to correct data source with fallback."""
    if asset_key == "BTCUSD":
        # Kraken confirmed working
        for attempt in range(3):
            try:
                df = fetch_btc_kraken()
                log.info(f"[BTC] Kraken: {len(df)} candles, close={df['Close'].iloc[-1]:.1f}")
                return df
            except Exception as e:
                log.warning(f"[BTC] Kraken attempt {attempt+1}: {e}"); time.sleep(3)

    elif asset_key == "XAUUSD":
        # Try OKX first, then Bitget
        sources = [
            ("OKX",    fetch_gold_okx),
            ("Bitget", fetch_gold_bitget),
        ]
        for name, fn in sources:
            for attempt in range(2):
                try:
                    df = fn()
                    if df.empty: continue
                    log.info(f"[GOLD] {name}: {len(df)} candles, close={df['Close'].iloc[-1]:.2f}")
                    return df
                except Exception as e:
                    log.warning(f"[GOLD] {name} attempt {attempt+1}: {e}"); time.sleep(3)

    log.error(f"fetch({asset_key}): all sources failed")
    return pd.DataFrame()


def is_new_candle(key, df):
    ts = str(df.index[-1])
    if ts != _last_candle[key]: _last_candle[key]=ts; return True
    return False

# ═══════════════════════════════════════════════════════════
#  INDICATORS
# ═══════════════════════════════════════════════════════════
def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def calc_rsi(s, p=14):
    d=s.diff(); g=d.clip(lower=0).rolling(p).mean()
    l=(-d.clip(upper=0)).rolling(p).mean()
    return float((100-100/(1+g/l.replace(0,np.nan))).iloc[-1])

def calc_vwap(df):
    tp=(df["High"]+df["Low"]+df["Close"])/3
    v=df["Volume"].replace(0,np.nan).fillna(1)
    return float((tp*v).cumsum().iloc[-1]/v.cumsum().iloc[-1])

def calc_fib(df, w=80):
    s=df.tail(w); hi,lo=float(s["High"].max()),float(s["Low"].min()); d=hi-lo
    return {"high":hi,"low":lo,"diff":d,
            "0.236":hi-0.236*d,"0.382":hi-0.382*d,"0.5":hi-0.5*d,
            "0.618":hi-0.618*d,"0.705":hi-0.705*d,"0.786":hi-0.786*d}

# ═══════════════════════════════════════════════════════════
#  SMC / ICT
# ═══════════════════════════════════════════════════════════
def order_blocks(df, w=50):
    s=df.tail(w).reset_index(drop=True)
    o,h,l,c=s["Open"].values,s["High"].values,s["Low"].values,s["Close"].values
    bull=bear=None
    for i in range(len(s)-2,0,-1):
        if c[i]<o[i] and bull is None:
            for j in range(i+1,min(i+7,len(s))):
                if c[j]>h[i]: bull={"high":h[i],"low":l[i]}; break
        if c[i]>o[i] and bear is None:
            for j in range(i+1,min(i+7,len(s))):
                if c[j]<l[i]: bear={"high":h[i],"low":l[i]}; break
        if bull and bear: break
    return bull, bear

def fair_value_gaps(df, w=50):
    s=df.tail(w).reset_index(drop=True); h,l=s["High"].values,s["Low"].values
    bf=brf=None
    for i in range(1,len(s)-1):
        if l[i+1]>h[i-1]: bf={"top":l[i+1],"bot":h[i-1]}
        if h[i+1]<l[i-1]: brf={"top":l[i-1],"bot":h[i+1]}
    return bf, brf

def bos_detect(df, w=40):
    s=df.tail(w); half=w//2
    if float(s["Close"].iloc[-1])>float(s.iloc[:half]["High"].max()): return "BULL_BOS"
    if float(s["Close"].iloc[-1])<float(s.iloc[:half]["Low"].min()):  return "BEAR_BOS"
    return "NONE"

def structure(df, w=60):
    s=df.tail(w); mid=w//2; h,l=s["High"].values,s["Low"].values
    if h[mid:].max()>h[:mid].max() and l[mid:].min()>l[:mid].min(): return "UPTREND"
    if h[mid:].max()<h[:mid].max() and l[mid:].min()<l[:mid].min(): return "DOWNTREND"
    return "RANGING"

def engulf(df):
    if len(df)<2: return "NONE"
    o,c=df["Open"].values,df["Close"].values
    b1,b2=abs(c[-2]-o[-2]),abs(c[-1]-o[-1])
    if c[-2]<o[-2] and c[-1]>o[-1] and b2>b1 and c[-1]>o[-2] and o[-1]<c[-2]: return "BULL"
    if c[-2]>o[-2] and c[-1]<o[-1] and b2>b1 and c[-1]<o[-2] and o[-1]>c[-2]: return "BEAR"
    return "NONE"

def liq_sweep(df, w=30):
    s=df.tail(w); p=s.iloc[:-1]; la=s.iloc[-1]
    if float(la["Low"])<float(p["Low"].min()) and float(la["Close"])>float(p["Low"].min()): return "BULL"
    if float(la["High"])>float(p["High"].max()) and float(la["Close"])<float(p["High"].max()): return "BEAR"
    return "NONE"

# ═══════════════════════════════════════════════════════════
#  SIGNAL ENGINE
# ═══════════════════════════════════════════════════════════
def score_signal(asset_key, df):
    if len(df)<80: return None
    asset=ASSETS[asset_key]; dec=asset["dec"]
    close=float(df["Close"].iloc[-1])
    e9=float(ema(df["Close"],9).iloc[-1]); e21=float(ema(df["Close"],21).iloc[-1])
    vwap=calc_vwap(df); rsi=calc_rsi(df["Close"]); fib=calc_fib(df)
    bob,rob=order_blocks(df); bfvg,rfvg=fair_value_gaps(df)
    bos=bos_detect(df); ms=structure(df)
    zone="PREMIUM" if close>fib["0.5"] else "DISCOUNT"
    cp=engulf(df); liq=liq_sweep(df); tol=fib["diff"]*0.012

    bs=0; rs=0; bw=[]; rw=[]
    if close>e9:  bs+=1;bw.append(f"Price above 9 EMA ({e9:.{dec}f})")
    else:         rs+=1;rw.append(f"Price below 9 EMA ({e9:.{dec}f})")
    if e9>e21:    bs+=1;bw.append("9 EMA > 21 EMA bullish alignment")
    else:         rs+=1;rw.append("9 EMA < 21 EMA bearish alignment")
    if close>vwap:bs+=1;bw.append(f"Price above VWAP ({vwap:.{dec}f})")
    else:         rs+=1;rw.append(f"Price below VWAP ({vwap:.{dec}f})")
    if rsi<45:    bs+=1;bw.append(f"RSI oversold ({rsi:.1f})")
    elif rsi>55:  rs+=1;rw.append(f"RSI overbought ({rsi:.1f})")
    if ms=="UPTREND":    bs+=1;bw.append("HH+HL structure uptrend")
    elif ms=="DOWNTREND":rs+=1;rw.append("LH+LL structure downtrend")
    for n,v in [("61.8%",fib["0.618"]),("70.5%",fib["0.705"]),("78.6%",fib["0.786"])]:
        if abs(close-v)<=tol and zone=="DISCOUNT": bs+=2;bw.append(f"Fib {n} support @ {v:.{dec}f}");break
    for n,v in [("23.6%",fib["0.236"]),("38.2%",fib["0.382"]),("50.0%",fib["0.5"])]:
        if abs(close-v)<=tol and zone=="PREMIUM":  rs+=2;rw.append(f"Fib {n} resistance @ {v:.{dec}f}");break
    if bob:
        ext=bob["high"]+(bob["high"]-bob["low"])*0.15
        if bob["low"]<=close<=ext: bs+=2;bw.append(f"Bullish OB ({bob['low']:.{dec}f}–{bob['high']:.{dec}f})")
    if rob:
        ext=rob["low"]-(rob["high"]-rob["low"])*0.15
        if ext<=close<=rob["high"]: rs+=2;rw.append(f"Bearish OB ({rob['low']:.{dec}f}–{rob['high']:.{dec}f})")
    if bfvg and bfvg["bot"]<=close<=bfvg["top"]: bs+=1;bw.append(f"Bullish FVG ({bfvg['bot']:.{dec}f}–{bfvg['top']:.{dec}f})")
    if rfvg and rfvg["bot"]<=close<=rfvg["top"]: rs+=1;rw.append(f"Bearish FVG ({rfvg['bot']:.{dec}f}–{rfvg['top']:.{dec}f})")
    if bos=="BULL_BOS":  bs+=2;bw.append("Bullish BOS confirmed")
    elif bos=="BEAR_BOS":rs+=2;rw.append("Bearish BOS confirmed")
    if cp=="BULL":  bs+=1;bw.append("Bullish engulfing candle")
    elif cp=="BEAR":rs+=1;rw.append("Bearish engulfing candle")
    if liq=="BULL": bs+=1;bw.append("ICT liquidity sweep buy-side grab")
    elif liq=="BEAR":rs+=1;rw.append("ICT liquidity sweep sell-side grab")

    direction=None; reasons=[]; score=0
    if bs>7 and bs>rs: direction="BUY";  reasons=bw; score=bs
    elif rs>7 and rs>bs:direction="SELL"; reasons=rw; score=rs
    if not direction: return None

    sl_p=asset["sl_pts"]; tp_p=asset["tp_pts"]
    entry=round(close,dec)
    sl=round(close-sl_p,dec) if direction=="BUY" else round(close+sl_p,dec)
    tp=round(close+tp_p,dec) if direction=="BUY" else round(close-tp_p,dec)
    return {"direction":direction,"entry":entry,"sl":sl,"tp":tp,
            "ema9":round(e9,dec),"ema21":round(e21,dec),"vwap":round(vwap,dec),"rsi":round(rsi,1),
            "fib_236":round(fib["0.236"],dec),"fib_382":round(fib["0.382"],dec),
            "fib_50":round(fib["0.5"],dec),"fib_618":round(fib["0.618"],dec),
            "fib_786":round(fib["0.786"],dec),"fib_hi":round(fib["high"],dec),"fib_lo":round(fib["low"],dec),
            "ms":ms,"zone":zone,"bos":bos,"bs":bs,"rs":rs,"score":score,"reasons":reasons}

# ═══════════════════════════════════════════════════════════
#  MESSAGE
# ═══════════════════════════════════════════════════════════
def fmt_msg(asset_key, sig):
    asset=ASSETS[asset_key]; dec=asset["dec"]
    now=datetime.utcnow().strftime("%d %b %Y  %H:%M UTC")
    rr=round(asset["tp_pts"]/asset["sl_pts"],2)
    di="🟢 <b>BUY  ▲</b>" if sig["direction"]=="BUY" else "🔴 <b>SELL ▼</b>"
    zi="🟦 Discount (buy zone)" if sig["zone"]=="DISCOUNT" else "🟥 Premium (sell zone)"
    sm={"UPTREND":"📈 Uptrend","DOWNTREND":"📉 Downtrend","RANGING":"↔️ Ranging"}
    bm={"BULL_BOS":"⚡ Bullish BOS","BEAR_BOS":"⚡ Bearish BOS","NONE":"—"}
    cf="🔥 ULTRA HIGH" if sig["score"]>=11 else "✅ HIGH"
    st="⭐"*min(sig["score"],12)
    rs="\n".join(f"   ✅ {r}" for r in sig["reasons"])
    return (
        f"{asset['emoji']} <b>AURUM AI — {asset['name']} ({asset_key})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{di}\n"
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
        f"🧠 <b>SMC / ICT Confluence</b>\n{rs}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏗 Structure : {sm.get(sig['ms'],sig['ms'])}\n"
        f"🗺 ICT Zone  : {zi}\n"
        f"⚡ BOS       : {bm.get(sig['bos'],sig['bos'])}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔥 Confidence: {cf}\n"
        f"⭐ Score     : {st} ({sig['score']}/14)\n"
        f"🕐 {now}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <i>Trade responsibly. Not financial advice.</i>"
    )

# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════
def main():
    log.info(f"AURUM AI v5 — OKX(Gold) + Kraken(BTC) — {RUN_DURATION_SEC}s")
    start = time.time()
    while (time.time()-start) < RUN_DURATION_SEC:
        for asset_key in ASSETS:
            try:
                df = fetch(asset_key)
                if df.empty: continue
                if not is_new_candle(asset_key, df): continue
                sig = score_signal(asset_key, df)
                if sig is None: log.info(f"  {asset_key}: score<=7, no signal"); continue
                sid = f"{sig['direction']}_{sig['entry']}"
                if sid == _last_sig_id[asset_key]: log.info(f"  {asset_key}: duplicate"); continue
                _last_sig_id[asset_key] = sid
                if tg(fmt_msg(asset_key, sig)):
                    log.info(f"  ✅ SENT {asset_key} {sig['direction']} @ {sig['entry']} score={sig['score']}/14")
            except Exception as e:
                log.exception(f"Error {asset_key}: {e}")
        elapsed=int(time.time()-start)
        log.info(f"Elapsed {elapsed}s — next scan in {SCAN_EVERY_SEC}s")
        time.sleep(min(SCAN_EVERY_SEC, max(0, RUN_DURATION_SEC-elapsed)))
    log.info("Done — GitHub Actions restarts in 5 min")

if __name__ == "__main__":
    main()