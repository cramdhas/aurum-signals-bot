#!/usr/bin/env python3
# AURUM AI — Gold & BTC Signals Bot v7
# Gold: Kraken auto-detect + Yahoo v8 + Stooq fallback
# BTC : Kraken XXBTZUSD (confirmed working)
# NO yfinance — pure requests only

import os, sys, time, logging
import numpy as np
import pandas as pd
import requests
from io import StringIO
from datetime import datetime

# ── Credentials ─────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID   = os.environ.get("CHAT_ID",   "")
if not BOT_TOKEN or not CHAT_ID:
    print("ERROR: Set BOT_TOKEN and CHAT_ID as GitHub Secrets")
    sys.exit(1)

RUN_DURATION = int(os.environ.get("RUN_DURATION", "270"))
SCAN_SEC     = 30

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("AURUM")

ASSETS = {
    "XAUUSD": {"name":"Gold",    "emoji":"🥇","sl":50, "tp":80, "dec":2},
    "BTCUSD": {"name":"Bitcoin", "emoji":"₿", "sl":400,"tp":600,"dec":1},
}
_last_sig    = {"XAUUSD":None,"BTCUSD":None}
_last_candle = {"XAUUSD":None,"BTCUSD":None}
_gold_pair   = None

# ── Telegram ─────────────────────────────────────────────
def tg(msg, silent=False):
    for _ in range(3):
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id":CHAT_ID,"text":msg,"parse_mode":"HTML",
                      "disable_notification":silent}, timeout=20)
            if r.status_code == 200: return True
        except: pass
        time.sleep(4)
    return False

# ── Kraken helper ─────────────────────────────────────────
def kraken_ohlc(pair, interval=5, limit=200):
    r = requests.get("https://api.kraken.com/0/public/OHLC",
        params={"pair":pair,"interval":interval}, timeout=15)
    r.raise_for_status()
    js = r.json()
    if js.get("error"): raise Exception(js["error"])
    key = next(k for k in js["result"] if k != "last")
    df  = pd.DataFrame(js["result"][key],
        columns=["ts","Open","High","Low","Close","vwap","Volume","cnt"])
    df  = df[["ts","Open","High","Low","Close","Volume"]].copy()
    df["ts"] = pd.to_datetime(df["ts"].astype(int), unit="s", utc=True)
    df.set_index("ts", inplace=True)
    df = df.apply(pd.to_numeric, errors="coerce").dropna()
    return df.tail(limit)

# ── Gold: find Kraken pair ────────────────────────────────
def get_kraken_gold_pair():
    global _gold_pair
    if _gold_pair: return _gold_pair
    r = requests.get("https://api.kraken.com/0/public/AssetPairs", timeout=15)
    for pid, info in r.json().get("result", {}).items():
        if "XAU" in info.get("base","").upper() and "USD" in info.get("quote","").upper():
            log.info(f"Kraken gold pair found: {pid}")
            _gold_pair = pid
            return pid
    return None

# ── Gold source 1: Kraken ────────────────────────────────
def gold_kraken():
    pair = get_kraken_gold_pair()
    if not pair: raise Exception("No XAU pair on Kraken")
    df = kraken_ohlc(pair)
    log.info(f"[GOLD] Kraken {pair}: {len(df)} candles close={df['Close'].iloc[-1]:.2f}")
    return df

# ── Gold source 2: Yahoo Finance v8 (no library) ─────────
def gold_yahoo():
    hdrs = {"User-Agent":"Mozilla/5.0 (Linux; Android 13; Pixel 7) "
            "AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36",
            "Accept":"application/json",
            "Referer":"https://finance.yahoo.com/"}
    for sym in ["GC=F", "XAUUSD=X"]:
        try:
            r = requests.get(
                f"https://query2.finance.yahoo.com/v8/finance/chart/{sym}",
                params={"interval":"5m","range":"2d","includePrePost":"false"},
                headers=hdrs, timeout=15)
            if r.status_code != 200: continue
            res = r.json().get("chart",{}).get("result")
            if not res: continue
            ch  = res[0]
            ts  = ch.get("timestamp",[])
            q   = ch.get("indicators",{}).get("quote",[{}])[0]
            if not ts or not q.get("close"): continue
            df = pd.DataFrame({"Open":q.get("open"),"High":q.get("high"),
                "Low":q.get("low"),"Close":q.get("close"),
                "Volume":q.get("volume",[0]*len(ts))},
                index=pd.to_datetime(ts, unit="s", utc=True))
            df = df.apply(pd.to_numeric, errors="coerce").dropna(subset=["Close"])
            if len(df) < 10: continue
            log.info(f"[GOLD] Yahoo {sym}: {len(df)} candles close={df['Close'].iloc[-1]:.2f}")
            return df.tail(200)
        except Exception as e:
            log.warning(f"Yahoo {sym}: {e}")
    raise Exception("Yahoo: all symbols failed")

# ── Gold source 3: Stooq ─────────────────────────────────
def gold_stooq():
    r = requests.get("https://stooq.com/q/d/l/?s=xauusd&i=5",
        headers={"User-Agent":"Mozilla/5.0"}, timeout=15)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text))
    if "Date" not in df.columns: raise Exception(f"Stooq bad cols: {df.columns.tolist()}")
    df.index = pd.to_datetime(df["Date"].astype(str)+" "+df.get("Time","00:00:00").astype(str), utc=True)
    df = df[["Open","High","Low","Close","Volume"]].apply(pd.to_numeric, errors="coerce").dropna()
    df.sort_index(inplace=True)
    if len(df) < 10: raise Exception(f"Stooq only {len(df)} rows")
    log.info(f"[GOLD] Stooq: {len(df)} candles close={df['Close'].iloc[-1]:.2f}")
    return df.tail(200)

# ── BTC: Kraken ───────────────────────────────────────────
def btc_kraken():
    df = kraken_ohlc("XXBTZUSD")
    log.info(f"[BTC] Kraken: {len(df)} candles close={df['Close'].iloc[-1]:.1f}")
    return df

# ── Master fetch ──────────────────────────────────────────
def fetch(asset_key):
    if asset_key == "BTCUSD":
        for i in range(3):
            try: return btc_kraken()
            except Exception as e: log.warning(f"BTC attempt {i+1}: {e}"); time.sleep(3)

    elif asset_key == "XAUUSD":
        for name, fn in [("Kraken",gold_kraken),("Yahoo",gold_yahoo),("Stooq",gold_stooq)]:
            try:
                df = fn()
                if not df.empty: return df
            except Exception as e:
                log.warning(f"[GOLD] {name}: {e}"); time.sleep(2)

    log.error(f"{asset_key}: all sources failed")
    return pd.DataFrame()

def new_candle(key, df):
    ts = str(df.index[-1])
    if ts != _last_candle[key]: _last_candle[key]=ts; return True
    return False

# ── Indicators ────────────────────────────────────────────
def ema(s,n): return s.ewm(span=n,adjust=False).mean()

def rsi(s,p=14):
    d=s.diff(); g=d.clip(lower=0).rolling(p).mean()
    l=(-d.clip(upper=0)).rolling(p).mean()
    return float((100-100/(1+g/l.replace(0,np.nan))).iloc[-1])

def vwap(df):
    tp=(df["High"]+df["Low"]+df["Close"])/3
    v=df["Volume"].replace(0,np.nan).fillna(1)
    return float((tp*v).cumsum().iloc[-1]/v.cumsum().iloc[-1])

def fib(df,w=80):
    s=df.tail(w); hi,lo=float(s["High"].max()),float(s["Low"].min()); d=hi-lo
    return {"hi":hi,"lo":lo,"d":d,
            "236":hi-0.236*d,"382":hi-0.382*d,"50":hi-0.5*d,
            "618":hi-0.618*d,"705":hi-0.705*d,"786":hi-0.786*d}

def ob(df,w=50):
    s=df.tail(w).reset_index(drop=True)
    o,h,l,c=s["Open"].values,s["High"].values,s["Low"].values,s["Close"].values
    bull=bear=None
    for i in range(len(s)-2,0,-1):
        if c[i]<o[i] and not bull:
            for j in range(i+1,min(i+7,len(s))):
                if c[j]>h[i]: bull={"h":h[i],"l":l[i]}; break
        if c[i]>o[i] and not bear:
            for j in range(i+1,min(i+7,len(s))):
                if c[j]<l[i]: bear={"h":h[i],"l":l[i]}; break
        if bull and bear: break
    return bull,bear

def fvg(df,w=50):
    s=df.tail(w).reset_index(drop=True); h,l=s["High"].values,s["Low"].values
    bf=brf=None
    for i in range(1,len(s)-1):
        if l[i+1]>h[i-1]: bf={"top":l[i+1],"bot":h[i-1]}
        if h[i+1]<l[i-1]: brf={"top":l[i-1],"bot":h[i+1]}
    return bf,brf

def bos(df,w=40):
    s=df.tail(w); half=w//2
    c=float(s["Close"].iloc[-1])
    if c>float(s.iloc[:half]["High"].max()): return "BULL"
    if c<float(s.iloc[:half]["Low"].min()):  return "BEAR"
    return "NONE"

def structure(df,w=60):
    s=df.tail(w); m=w//2; h,l=s["High"].values,s["Low"].values
    if h[m:].max()>h[:m].max() and l[m:].min()>l[:m].min(): return "UP"
    if h[m:].max()<h[:m].max() and l[m:].min()<l[:m].min(): return "DOWN"
    return "RANGE"

def engulf(df):
    o,c=df["Open"].values,df["Close"].values
    b1,b2=abs(c[-2]-o[-2]),abs(c[-1]-o[-1])
    if c[-2]<o[-2] and c[-1]>o[-1] and b2>b1 and c[-1]>o[-2] and o[-1]<c[-2]: return "BULL"
    if c[-2]>o[-2] and c[-1]<o[-1] and b2>b1 and c[-1]<o[-2] and o[-1]>c[-2]: return "BEAR"
    return "NONE"

def sweep(df,w=30):
    s=df.tail(w); p=s.iloc[:-1]; la=s.iloc[-1]
    if float(la["Low"])<float(p["Low"].min()) and float(la["Close"])>float(p["Low"].min()): return "BULL"
    if float(la["High"])>float(p["High"].max()) and float(la["Close"])<float(p["High"].max()): return "BEAR"
    return "NONE"

# ── Signal engine ─────────────────────────────────────────
def signal(key, df):
    if len(df)<80: return None
    a=ASSETS[key]; dec=a["dec"]; cl=float(df["Close"].iloc[-1])
    e9=float(ema(df["Close"],9).iloc[-1]); e21=float(ema(df["Close"],21).iloc[-1])
    vw=vwap(df); rs=rsi(df["Close"]); fb=fib(df)
    bob,rob=ob(df); bfv,rfv=fvg(df)
    bk=bos(df); ms=structure(df)
    zone="P" if cl>fb["50"] else "D"
    cp=engulf(df); lq=sweep(df); tol=fb["d"]*0.012

    bs=0; rs2=0; bw=[]; rw=[]

    # 9 EMA
    if cl>e9:  bs+=1;bw.append(f"Above 9 EMA ({e9:.{dec}f})")
    else:      rs2+=1;rw.append(f"Below 9 EMA ({e9:.{dec}f})")
    # EMA align
    if e9>e21: bs+=1;bw.append("9 EMA > 21 EMA bullish")
    else:      rs2+=1;rw.append("9 EMA < 21 EMA bearish")
    # VWAP
    if cl>vw:  bs+=1;bw.append(f"Above VWAP ({vw:.{dec}f})")
    else:      rs2+=1;rw.append(f"Below VWAP ({vw:.{dec}f})")
    # RSI
    if rs<45:  bs+=1;bw.append(f"RSI oversold ({rs:.1f})")
    elif rs>55:rs2+=1;rw.append(f"RSI overbought ({rs:.1f})")
    # Structure
    if ms=="UP":   bs+=1;bw.append("Uptrend HH+HL")
    elif ms=="DOWN":rs2+=1;rw.append("Downtrend LH+LL")
    # Fibonacci
    for n,v in [("61.8%",fb["618"]),("70.5%",fb["705"]),("78.6%",fb["786"])]:
        if abs(cl-v)<=tol and zone=="D": bs+=2;bw.append(f"Fib {n} support @ {v:.{dec}f}");break
    for n,v in [("23.6%",fb["236"]),("38.2%",fb["382"]),("50.0%",fb["50"])]:
        if abs(cl-v)<=tol and zone=="P": rs2+=2;rw.append(f"Fib {n} resist @ {v:.{dec}f}");break
    # Order Block
    if bob:
        ext=bob["h"]+(bob["h"]-bob["l"])*0.15
        if bob["l"]<=cl<=ext: bs+=2;bw.append(f"Bullish OB ({bob['l']:.{dec}f}-{bob['h']:.{dec}f})")
    if rob:
        ext=rob["l"]-(rob["h"]-rob["l"])*0.15
        if ext<=cl<=rob["h"]: rs2+=2;rw.append(f"Bearish OB ({rob['l']:.{dec}f}-{rob['h']:.{dec}f})")
    # FVG
    if bfv and bfv["bot"]<=cl<=bfv["top"]: bs+=1;bw.append(f"Bullish FVG ({bfv['bot']:.{dec}f}-{bfv['top']:.{dec}f})")
    if rfv and rfv["bot"]<=cl<=rfv["top"]: rs2+=1;rw.append(f"Bearish FVG ({rfv['bot']:.{dec}f}-{rfv['top']:.{dec}f})")
    # BOS
    if bk=="BULL": bs+=2;bw.append("Bullish BOS confirmed")
    elif bk=="BEAR":rs2+=2;rw.append("Bearish BOS confirmed")
    # Engulf
    if cp=="BULL": bs+=1;bw.append("Bullish engulfing")
    elif cp=="BEAR":rs2+=1;rw.append("Bearish engulfing")
    # Sweep
    if lq=="BULL": bs+=1;bw.append("ICT liquidity sweep buyside")
    elif lq=="BEAR":rs2+=1;rw.append("ICT liquidity sweep sellside")

    if bs>7 and bs>rs2:   d="BUY"; reasons=bw; sc=bs
    elif rs2>7 and rs2>bs: d="SELL";reasons=rw; sc=rs2
    else: return None

    dec2=a["dec"]; sl=a["sl"]; tp=a["tp"]
    entry=round(cl,dec2)
    SL=round(cl-sl,dec2) if d=="BUY" else round(cl+sl,dec2)
    TP=round(cl+tp,dec2) if d=="BUY" else round(cl-tp,dec2)
    return {"d":d,"entry":entry,"sl":SL,"tp":TP,
            "e9":round(e9,dec2),"e21":round(e21,dec2),"vw":round(vw,dec2),"rs":round(rs,1),
            "f236":round(fb["236"],dec2),"f382":round(fb["382"],dec2),"f50":round(fb["50"],dec2),
            "f618":round(fb["618"],dec2),"f786":round(fb["786"],dec2),
            "fhi":round(fb["hi"],dec2),"flo":round(fb["lo"],dec2),
            "ms":ms,"zone":zone,"bos":bk,"bs":bs,"rs2":rs2,"sc":sc,"why":reasons}

# ── Format message ────────────────────────────────────────
def fmt(key, sig):
    a=ASSETS[key]; dec=a["dec"]; now=datetime.utcnow().strftime("%d %b %Y %H:%M UTC")
    rr=round(a["tp"]/a["sl"],2)
    di="🟢 <b>BUY  ▲</b>" if sig["d"]=="BUY" else "🔴 <b>SELL ▼</b>"
    zi="🟦 Discount zone" if sig["zone"]=="D" else "🟥 Premium zone"
    sm={"UP":"📈 Uptrend","DOWN":"📉 Downtrend","RANGE":"↔️ Ranging"}
    bm={"BULL":"⚡ Bullish BOS","BEAR":"⚡ Bearish BOS","NONE":"—"}
    cf="🔥 ULTRA HIGH" if sig["sc"]>=11 else "✅ HIGH"
    st="⭐"*min(sig["sc"],12)
    rs="\n".join(f"   ✅ {r}" for r in sig["why"])
    return (
        f"{a['emoji']} <b>AURUM AI — {a['name']} ({key})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{di}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 <b>Entry :</b> <code>{sig['entry']:.{dec}f}</code>\n"
        f"🛑 <b>SL    :</b> <code>{sig['sl']:.{dec}f}</code>  ({a['sl']} pts)\n"
        f"🎯 <b>TP    :</b> <code>{sig['tp']:.{dec}f}</code>  ({a['tp']} pts)\n"
        f"📊 <b>R:R   :</b> 1 : {rr}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 9 EMA  : {sig['e9']:.{dec}f}\n"
        f"📉 21 EMA : {sig['e21']:.{dec}f}\n"
        f"💧 VWAP   : {sig['vw']:.{dec}f}\n"
        f"📊 RSI    : {sig['rs']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📐 <b>Fibonacci</b>\n"
        f"   🔝 {sig['fhi']:.{dec}f}\n"
        f"   · 23.6% {sig['f236']:.{dec}f}\n"
        f"   · 38.2% {sig['f382']:.{dec}f}\n"
        f"   ➡️ 50.0% {sig['f50']:.{dec}f}\n"
        f"   · 61.8% {sig['f618']:.{dec}f}\n"
        f"   · 78.6% {sig['f786']:.{dec}f}\n"
        f"   🔻 {sig['flo']:.{dec}f}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🧠 <b>Confluence</b>\n{rs}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏗 {sm.get(sig['ms'],sig['ms'])}  |  {zi}\n"
        f"⚡ BOS: {bm.get(sig['bos'],sig['bos'])}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{cf}  {st} ({sig['sc']}/14)\n"
        f"🕐 {now}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <i>Not financial advice.</i>"
    )

# ── Main loop ─────────────────────────────────────────────
def main():
    log.info(f"AURUM AI v7 starting — {RUN_DURATION}s")
    start = time.time()
    while (time.time()-start) < RUN_DURATION:
        for key in ASSETS:
            try:
                df = fetch(key)
                if df.empty: continue
                if not new_candle(key, df): continue
                sig = signal(key, df)
                if sig is None: log.info(f"{key}: no signal"); continue
                sid = f"{sig['d']}_{sig['entry']}"
                if sid == _last_sig[key]: continue
                _last_sig[key] = sid
                if tg(fmt(key, sig)):
                    log.info(f"SENT {key} {sig['d']} @ {sig['entry']} score={sig['sc']}/14")
            except Exception as e:
                log.exception(f"{key}: {e}")
        elapsed = int(time.time()-start)
        log.info(f"Elapsed {elapsed}s — scan in {SCAN_SEC}s")
        time.sleep(min(SCAN_SEC, max(0, RUN_DURATION-elapsed)))
    log.info("Done.")

if __name__ == "__main__":
    main()