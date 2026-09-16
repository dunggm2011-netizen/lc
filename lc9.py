import os
import io
import math
import time
import logging
import threading
import collections
import requests
import telebot
from telebot import types
from flask import Flask, jsonify
from PIL import Image, ImageDraw, ImageFont

# ==================== CONFIG ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8385677064:AAEtgKFsUGflb5a5H5F93xELObm5zoNfxTc")
CHAT_ID   = os.environ.get("CHAT_ID", "-1003985322705")
ADMIN_ID  = int(os.environ.get("ADMIN_ID", "7564889663"))
POLL_SEC  = int(os.environ.get("POLL_INTERVAL", "3"))

API_URL = "https://wtxmd52.tele68.com/v1/txmd5/sessions"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

TOOL_NAME = "TOOL THANH DUY"
GROUP_LINK = "https://t.me/+LaaT-vDzLo02MDJl"
GROUP_LINK_TEXT = "t.me/+LaaT-vDzLo02MDJl"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("lc79-bot")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ==================== UTILS ====================
def clamp(v, lo, hi): return max(lo, min(hi, v))
def avg(a): return sum(a) / len(a) if a else 0
def std(a):
    if not a: return 0
    m = avg(a)
    return math.sqrt(sum((x - m) ** 2 for x in a) / len(a))

def entropy(arr):
    if not arr: return 0
    f = {}
    for v in arr: f[v] = f.get(v, 0) + 1
    e = 0
    for k in f:
        p = f[k] / len(arr)
        e -= p * math.log2(p)
    return e

def runs(tx):
    if not tx: return []
    out = []
    cur = tx[0]; ln = 1
    for x in tx[1:]:
        if x == cur: ln += 1
        else:
            out.append({"val": cur, "len": ln})
            cur = x; ln = 1
    out.append({"val": cur, "len": ln})
    return out

def extract_features(sessions):
    tx = []
    for s in sessions:
        r = (s.get("resultTruyenThong")
             or s.get("result")
             or s.get("ket_qua")
             or s.get("result_truyen_thong")
             or s.get("tx"))
        if r:
            rs = str(r).upper()
            if "TAI" in rs or rs == "T" or "TÀI" in rs:
                tx.append("TAI")
            elif "XIU" in rs or rs == "X" or "XỈU" in rs:
                tx.append("XIU")

    totals = []
    for s in sessions:
        p = (s.get("point")
             or s.get("totalPoint")
             or s.get("dicesSum")
             or s.get("tong"))
        if p is not None:
            try: totals.append(int(p))
            except: pass

    n = len(tx)
    if n == 0: return None

    rs = runs(tx)
    cur = rs[-1] if rs else {"val": None, "len": 0}

    TT = TX = XT = XX = 0
    for i in range(1, n):
        p, c = tx[i-1], tx[i]
        if p == "TAI" and c == "TAI": TT += 1
        elif p == "TAI" and c == "XIU": TX += 1
        elif p == "XIU" and c == "TAI": XT += 1
        elif p == "XIU" and c == "XIU": XX += 1

    return {
        "tx": tx, "totals": totals, "n": n,
        "ratio": tx.count("TAI") / n,
        "ent": entropy(tx),
        "rs": rs, "cur": cur,
        "TT": TT, "TX": TX, "XT": XT, "XX": XX,
        "switchRate": (TX + XT) / (n - 1) if n > 1 else 0.5,
        "meanTotal": avg(totals), "stdTotal": std(totals),
        "recentMean": avg(totals[-10:]),
        "maxRun": max((r["len"] for r in rs), default=0),
    }

# ==================== 15 MICRO MODEL ====================
def m_freq5(f):
    if f["n"] < 5: return None
    r = f["tx"][-5:]
    return {"id": "freq5", "p": (r.count("TAI") + 1) / 7, "c": 0.5, "s": 5}

def m_freq10(f):
    if f["n"] < 10: return None
    r = f["tx"][-10:]
    return {"id": "freq10", "p": (r.count("TAI") + 1) / 12, "c": 0.55, "s": 10}

def m_freq20(f):
    if f["n"] < 20: return None
    r = f["tx"][-20:]
    return {"id": "freq20", "p": (r.count("TAI") + 1) / 22, "c": 0.6, "s": 20}

def m_exp_freq(f):
    r = f["tx"][:30]
    if not r: return None
    wt = 0; w = 1; tt = 0
    for x in r:
        if x == "TAI": tt += w
        wt += w; w *= 0.9
    return {"id": "exp_freq", "p": tt / wt if wt else 0.5, "c": 0.55, "s": len(r)}

def m_streak(f):
    if f["cur"]["len"] < 3: return None
    tx = f["tx"]; sl = f["cur"]["len"]; st = f["cur"]["val"]
    cont = tot = 0
    for i in range(len(tx) - sl):
        if all(tx[i+k] == st for k in range(sl)) and i + sl < len(tx):
            tot += 1
            if tx[i+sl] == st: cont += 1
    pc = cont / tot if tot >= 2 else 0.5
    p = pc if st == "TAI" else 1 - pc
    return {"id": "streak", "p": p, "c": clamp(0.4 + min(sl, 6) * 0.05, 0.4, 0.75), "s": tot}

def m_reversal(f):
    if f["cur"]["len"] < 5: return None
    bias = min((f["cur"]["len"] - 4) * 0.08, 0.35)
    p = 0.5 - bias if f["cur"]["val"] == "TAI" else 0.5 + bias
    return {"id": "reversal", "p": p, "c": clamp(0.4 + f["cur"]["len"] * 0.03, 0.4, 0.65), "s": f["cur"]["len"]}

def m_markov1(f):
    if f["n"] < 3: return None
    last = f["tx"][-1]
    if last == "TAI": p = (f["TT"] + 1) / (f["TT"] + f["TX"] + 2)
    else: p = (f["XT"] + 1) / (f["XT"] + f["XX"] + 2)
    return {"id": "markov1", "p": p, "c": 0.5, "s": f["TT"] + f["TX"] + f["XT"] + f["XX"]}

def m_markov2(f):
    if f["n"] < 5: return None
    tx = f["tx"]; last2 = "".join(tx[-2:])
    tC = xC = 0
    for i in range(len(tx) - 2):
        if "".join(tx[i:i+2]) == last2:
            if tx[i+2] == "TAI": tC += 1
            else: xC += 1
    if tC + xC < 2: return None
    return {"id": "markov2", "p": (tC + 1) / (tC + xC + 2), "c": clamp(0.4 + (tC + xC) * 0.05, 0.4, 0.75), "s": tC + xC}

def m_markov3(f):
    if f["n"] < 8: return None
    tx = f["tx"]; last3 = "".join(tx[-3:])
    tC = xC = 0
    for i in range(len(tx) - 3):
        if "".join(tx[i:i+3]) == last3:
            if tx[i+3] == "TAI": tC += 1
            else: xC += 1
    if tC + xC < 2: return None
    return {"id": "markov3", "p": (tC + 1) / (tC + xC + 2), "c": clamp(0.42 + (tC + xC) * 0.04, 0.42, 0.72), "s": tC + xC}

def m_ngram4(f):
    if f["n"] < 10: return None
    tx = f["tx"]; last4 = "".join(tx[-4:])
    tC = xC = 0
    for i in range(len(tx) - 4):
        if "".join(tx[i:i+4]) == last4:
            if tx[i+4] == "TAI": tC += 1
            else: xC += 1
    if tC + xC < 2: return None
    return {"id": "ngram4", "p": (tC + 1) / (tC + xC + 2), "c": clamp(0.45 + (tC + xC) * 0.04, 0.45, 0.75), "s": tC + xC}

def m_alternation(f):
    if f["n"] < 8: return None
    if f["switchRate"] < 0.75: return None
    last = f["tx"][-1]
    return {"id": "alternation", "p": 0.35 if last == "TAI" else 0.65, "c": clamp(0.4 + f["switchRate"] * 0.3, 0.4, 0.7), "s": f["n"]}

def m_momentum(f):
    if f["n"] < 10: return None
    trend5 = f["tx"][-5:].count("TAI") / 5
    trend20 = f["tx"][-20:].count("TAI") / 20 if f["n"] >= 20 else 0.5
    momentum = trend5 - trend20
    if abs(momentum) < 0.15: return None
    return {"id": "momentum", "p": 0.55 if momentum > 0 else 0.45, "c": clamp(0.4 + abs(momentum), 0.4, 0.65), "s": f["n"]}

def m_contrarian(f):
    if f["n"] < 5: return None
    t = f["tx"][-5:].count("TAI")
    if t >= 4: return {"id": "contrarian", "p": 0.35, "c": 0.4, "s": 5}
    if t <= 1: return {"id": "contrarian", "p": 0.65, "c": 0.4, "s": 5}
    return None

def m_volatility(f):
    if f["n"] < 15: return None
    if f["switchRate"] > 0.75 or f["switchRate"] < 0.25: return None
    p = clamp(0.5 + (f["ratio"] - 0.5) * 0.7, 0.35, 0.65)
    return {"id": "volatility", "p": p, "c": 0.5, "s": f["n"]}

def m_entropy_break(f):
    if f["n"] < 20: return None
    e5 = entropy(f["tx"][-5:])
    e15 = entropy(f["tx"][-15:])
    if e15 - e5 < 0.4: return None
    p = 0.6 if f["cur"]["val"] == "TAI" else 0.4
    return {"id": "entropy_break", "p": p, "c": clamp(0.42 + (e15-e5)*0.3, 0.42, 0.7), "s": 20}

def m_cycle11(f):
    if f["n"] < 4: return None
    tx = f["tx"]
    if tx[-1] != tx[-2] and tx[-2] != tx[-3] and tx[-3] != tx[-4]:
        last = tx[-1]
        p = 0.35 if last == "TAI" else 0.65
        return {"id": "cycle11", "p": p, "c": 0.82, "s": 4}
    return None

# ==================== 8 MACRO MODEL ====================
def M_long_ratio(f):
    if f["n"] < 30: return None
    t = f["tx"][-30:].count("TAI")
    p = (t + 3) / 36
    return {"id": "long_ratio", "p": p, "c": clamp(0.4 + abs(p-0.5)*1.5, 0.4, 0.7), "s": 30}

def M_regime_shift(f):
    if f["n"] < 50: return None
    first = f["tx"][-50:-25]; second = f["tx"][-25:]
    r1 = first.count("TAI") / 25; r2 = second.count("TAI") / 25
    shift = r2 - r1
    if abs(shift) < 0.2: return None
    return {"id": "regime_shift", "p": 0.55 if shift > 0 else 0.45, "c": clamp(0.4 + abs(shift), 0.4, 0.7), "s": 50}

def M_dice_trend(f):
    if len(f["totals"]) < 15: return None
    totals = f["totals"][-15:]; n = len(totals)
    sumX = sum(range(n)); sumY = sum(totals)
    sumXY = sum(i * totals[i] for i in range(n)); sumX2 = sum(i * i for i in range(n))
    denom = n * sumX2 - sumX * sumX
    if denom == 0: return None
    slope = (n * sumXY - sumX * sumY) / denom
    return {"id": "dice_trend", "p": clamp(0.5 + slope*0.15, 0.35, 0.65), "c": clamp(0.42 + abs(slope)*0.1, 0.42, 0.7), "s": 15}

def M_dice_reverse(f):
    if len(f["totals"]) < 8: return None
    m = avg(f["totals"][-8:])
    if m >= 12: return {"id": "dice_reverse", "p": 0.4, "c": 0.5, "s": 8}
    if m <= 9: return {"id": "dice_reverse", "p": 0.6, "c": 0.5, "s": 8}
    return None

def M_bridge_breaker(f):
    if f["n"] < 30 or f["cur"]["len"] < 4: return None
    sameType = [r for r in f["rs"] if r["val"] == f["cur"]["val"]]
    if len(sameType) < 5: return None
    lens = [r["len"] for r in sameType]
    m = avg(lens); s = std(lens)
    if f["cur"]["len"] > m + s * 1.8:
        p = 0.3 if f["cur"]["val"] == "TAI" else 0.7
        return {"id": "bridge_breaker", "p": p, "c": clamp(0.45 + f["cur"]["len"]*0.03, 0.45, 0.75), "s": f["cur"]["len"]}
    return None

def M_dice_chaos(f):
    if len(f["totals"]) < 15: return None
    last_total = f["totals"][-1]
    t = x = 0
    for i in range(len(f["totals"]) - 1):
        if f["totals"][i] == last_total:
            if i + 1 < len(f["tx"]):
                if f["tx"][i+1] == "TAI": t += 1
                else: x += 1
    if t + x < 3: return None
    return {"id": "dice_chaos", "p": clamp(t/(t+x), 0.3, 0.7), "c": clamp(0.42 + min(t+x,10)*0.03, 0.42, 0.7), "s": t+x}

def M_bayes_global(f):
    if f["n"] < 50: return None
    t = f["tx"].count("TAI")
    p = (t + 2) / (f["n"] + 4)
    return {"id": "bayes_global", "p": p, "c": clamp(0.4 + math.log2(1+f["n"])*0.05, 0.4, 0.7), "s": f["n"]}

def M_anti_streak(f):
    if f["cur"]["len"] < 3: return None
    s = min((f["cur"]["len"] - 2) * 0.08, 0.3)
    p = 0.5 - s if f["cur"]["val"] == "TAI" else 0.5 + s
    return {"id": "anti_streak", "p": p, "c": clamp(0.35 + f["cur"]["len"]*0.04, 0.35, 0.65), "s": f["cur"]["len"]}

# ==================== 8 GHIM CẦU MODEL ====================
def g_11(f):
    if f["n"] < 8: return None
    rh = f["rs"]
    if len(rh) < 4: return None
    recent = rh[-8:]
    if not all(r["len"] == 1 for r in recent) or len(recent) < 4: return None
    cur = recent[-1]
    p = 0.35 if cur["val"] == "TAI" else 0.65
    return {"id": "ghim_1_1", "p": p, "c": clamp(0.45 + len(recent)*0.03, 0.45, 0.72), "s": len(recent)}

def g_22(f):
    if f["n"] < 12: return None
    rh = f["rs"]
    if len(rh) < 4: return None
    recent = rh[-6:]
    if not all(r["len"] == 2 for r in recent[:min(6,len(recent))]) or len(recent) < 3: return None
    cur = recent[-1]
    p = (0.38 if cur["len"]>=2 else 0.6) if cur["val"]=="TAI" else (0.62 if cur["len"]>=2 else 0.4)
    return {"id": "ghim_2_2", "p": p, "c": clamp(0.4 + len(recent)*0.03, 0.4, 0.68), "s": len(recent)*2}

def g_33(f):
    if f["n"] < 18: return None
    rh = f["rs"]
    if len(rh) < 3: return None
    recent = rh[-4:]
    if not all(r["len"] == 3 for r in recent[:min(4,len(recent))]) or len(recent) < 2: return None
    cur = recent[-1]
    p = (0.62 if cur["len"]<3 else 0.38) if cur["val"]=="TAI" else (0.38 if cur["len"]<3 else 0.62)
    return {"id": "ghim_3_3", "p": p, "c": clamp(0.42 + len(recent)*0.04, 0.42, 0.7), "s": len(recent)*3}

def g_31(f):
    if f["n"] < 16: return None
    rh = f["rs"]
    if len(rh) < 4: return None
    recent = rh[-6:]
    def is_pattern(arr, first):
        if len(arr) < 4: return False
        for i in range(len(arr)):
            exp = first if i % 2 == 0 else (1 if first == 3 else 3)
            if arr[i]["len"] != exp: return False
        return True
    for first, label in [(3, "3_1"), (1, "1_3")]:
        if is_pattern(recent, first):
            cur = recent[-1]
            next_len = 3 if len(recent) % 2 == 1 else 1
            p = (0.62 if cur["len"]<next_len else 0.38) if cur["val"]=="TAI" else (0.38 if cur["len"]<next_len else 0.62)
            return {"id": f"ghim_{label}", "p": p, "c": clamp(0.42 + len(recent)*0.03, 0.42, 0.7), "s": len(recent)}
    return None

def g_21(f):
    if f["n"] < 12: return None
    rh = f["rs"]
    if len(rh) < 4: return None
    recent = rh[-6:]
    def is_pattern(arr, first):
        if len(arr) < 4: return False
        for i in range(len(arr)):
            exp = first if i % 2 == 0 else (1 if first == 2 else 2)
            if arr[i]["len"] != exp: return False
        return True
    for first, label in [(2, "2_1"), (1, "1_2")]:
        if is_pattern(recent, first):
            cur = recent[-1]
            next_len = 2 if len(recent) % 2 == 1 else 1
            p = (0.62 if cur["len"]<next_len else 0.4) if cur["val"]=="TAI" else (0.38 if cur["len"]<next_len else 0.6)
            return {"id": f"ghim_{label}", "p": p, "c": clamp(0.4 + len(recent)*0.03, 0.4, 0.68), "s": len(recent)}
    return None

def g_pattern_type(f):
    if f["n"] < 15: return None
    rh = f["rs"]
    if len(rh) < 3: return None
    recent = rh[-6:]
    lens = [r["len"] for r in recent]
    vals = [r["val"] for r in recent]
    last = recent[-1]
    typ = None
    if all(l==1 for l in lens) and all(vals[i]!=vals[i-1] for i in range(1,len(vals))): typ="1_1"
    elif all(l==2 for l in lens) and all(vals[i]!=vals[i-1] for i in range(1,len(vals))): typ="2_2"
    elif all(l==3 for l in lens) and all(vals[i]!=vals[i-1] for i in range(1,len(vals))): typ="3_3"
    elif len(lens)>=5 and ",".join(map(str,lens[-5:]))=="2,1,2,1,2": typ="2_1_2"
    elif len(lens)>=5 and ",".join(map(str,lens[-5:]))=="1,2,1,2,1": typ="1_2_1"
    elif len(lens)>=5 and ",".join(map(str,lens[-5:]))=="3,1,3,1,3": typ="3_1_3"
    elif last["len"] >= 5: typ="long_run"
    if not typ: return None
    p = 0.5
    if typ=="1_1": p = 0.35 if last["val"]=="TAI" else 0.65
    elif typ=="2_2": p = (0.38 if last["len"]>=2 else 0.6) if last["val"]=="TAI" else (0.62 if last["len"]>=2 else 0.4)
    elif typ=="3_3": p = (0.38 if last["len"]>=3 else 0.62) if last["val"]=="TAI" else (0.62 if last["len"]>=3 else 0.38)
    elif typ=="2_1_2": p = (0.4 if last["len"]==2 else 0.6) if last["val"]=="TAI" else (0.6 if last["len"]==2 else 0.4)
    elif typ=="1_2_1": p = (0.35 if last["len"]==1 else 0.6) if last["val"]=="TAI" else (0.65 if last["len"]==1 else 0.4)
    elif typ=="3_1_3": p = 0.42 if last["val"]=="TAI" else 0.58
    elif typ=="long_run":
        if last["len"]>=8: p = 0.3 if last["val"]=="TAI" else 0.7
        elif last["len"]>=5: p = 0.45 if last["val"]=="TAI" else 0.55
    return {"id": "pattern_type", "p": p, "c": clamp(0.42 + len(rh)*0.02, 0.42, 0.7), "s": len(rh)}

def g_long_trend(f):
    if f["n"] < 50: return None
    first = f["tx"][-50:-25]; second = f["tx"][-25:]
    r1 = first.count("TAI")/25; r2 = second.count("TAI")/25
    shift = r2 - r1
    if abs(shift) < 0.2: return None
    return {"id": "long_trend", "p": 0.55 if shift>0 else 0.45, "c": clamp(0.4 + abs(shift), 0.4, 0.7), "s": 50}

def g_similarity(f):
    if f["n"] < 15: return None
    tx = f["tx"]
    plen = min(7, f["n"]//5)
    target = "".join(tx[-plen:])
    tC = xC = 0
    for i in range(plen, len(tx)-1):
        if "".join(tx[i:i+plen]) == target:
            if tx[i-1] == "TAI": tC += 1
            else: xC += 1
    if tC + xC < 3: return None
    return {"id": "similarity", "p": (tC+1)/(tC+xC+2), "c": clamp(0.45 + (tC+xC)*0.04, 0.45, 0.8), "s": tC+xC}

# ==================== REGIME ====================
def detect_regime(f):
    if f["n"] < 20: return "COLD_START"
    if f["cur"]["len"] >= 5: return "STREAK_T" if f["cur"]["val"]=="TAI" else "STREAK_X"
    if f["switchRate"] >= 0.7: return "ALTERNATING"
    if f["ent"] < 0.6 and f["ratio"] > 0.65: return "STABLE_T"
    if f["ent"] < 0.6 and f["ratio"] < 0.35: return "STABLE_X"
    if f["ent"] > 0.95: return "HIGH_ENTROPY"
    if abs(f["ratio"] - 0.5) < 0.12: return "BALANCED"
    return "TRENDING"

# ==================== ENSEMBLE ====================
def run_engine(sessions):
    f = extract_features(sessions)
    if not f or f["n"] < 10:
        return {
            "prediction": "TAI", "confidence": 0.5,
            "regime": "COLD_START", "model_count": 0, "reasons": [],
        }

    models = []
    for fn in [
        m_freq5, m_freq10, m_freq20, m_exp_freq, m_streak, m_reversal,
        m_markov1, m_markov2, m_markov3, m_ngram4, m_alternation,
        m_momentum, m_contrarian, m_volatility, m_entropy_break, m_cycle11,
        M_long_ratio, M_regime_shift, M_dice_trend, M_dice_reverse,
        M_bridge_breaker, M_dice_chaos, M_bayes_global, M_anti_streak,
        g_11, g_22, g_33, g_31, g_21, g_pattern_type, g_long_trend, g_similarity,
    ]:
        try:
            m = fn(f)
            if m and isinstance(m.get("p"), (int, float)):
                m["p"] = clamp(m["p"], 0.1, 0.9)
                models.append(m)
        except Exception:
            pass

    if not models:
        return {
            "prediction": "TAI", "confidence": 0.5,
            "regime": "COLD_START", "model_count": 0, "reasons": [],
        }

    boost = {
        "markov1": 1.2, "markov2": 1.3, "markov3": 1.4, "ngram4": 1.3,
        "pattern_type": 1.4, "bridge_breaker": 1.5, "dice_chaos": 1.3,
        "ghim_1_1": 0.7, "ghim_2_2": 1.4, "ghim_3_3": 0.9,
        "long_ratio": 1.3, "regime_shift": 1.2, "long_trend": 1.3,
        "bayes_global": 1.4, "dice_trend": 1.1, "anti_streak": 0.7,
        "streak": 0.5, "momentum": 0.5, "freq5": 0.5,
    }

    voteT = voteX = 0.0
    totalW = 0.0
    votes = []
    for m in models:
        w = m.get("c", 0.5) * boost.get(m["id"], 1.0)
        if m["p"] >= 0.5: voteT += w
        else: voteX += w
        totalW += w
        votes.append({**m, "w": w})

    if totalW == 0:
        return {
            "prediction": "TAI", "confidence": 0.5,
            "regime": "COLD_START", "model_count": len(models), "reasons": [],
        }

    pT = voteT / totalW
    prediction = "TAI" if pT >= 0.5 else "XIU"
    voteCount = len(votes)
    agreeT = sum(1 for v in votes if v["p"] >= 0.5)
    agree = max(agreeT, voteCount - agreeT) / voteCount

    gap = abs(pT - 0.5) * 2
    conf = 0.30 + gap * 0.35 + agree * 0.25 - f["ent"] * 0.10
    conf = clamp(conf, 0.15, 0.92)
    regime = detect_regime(f)

    top = sorted(votes, key=lambda v: -v["w"])[:5]
    reasons = [v["id"] for v in top]

    return {
        "prediction": prediction,
        "tai": round(pT, 4),
        "xiu": round(1-pT, 4),
        "confidence": round(conf, 4),
        "regime": regime,
        "model_count": len(models),
        "agree": round(agree * 100),
        "reasons": reasons,
    }

# ==================== IMAGE GENERATOR (HOÀNG DÙNG STYLE) ====================
FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]

def load_font(size):
    for path in FONT_PATHS:
        if os.path.exists(path):
            try: return ImageFont.truetype(path, size)
            except: pass
    return ImageFont.load_default()

def draw_dice_card(draw, cx, cy, size, value, angle=0):
    half = size // 2
    rad = math.radians(angle)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    corners = [(-half, -half), (half, -half), (half, half), (-half, half)]
    rotated = []
    for (x, y) in corners:
        rx = x * cos_a - y * sin_a + cx
        ry = x * sin_a + y * cos_a + cy
        rotated.append((rx, ry))
    draw.polygon(rotated, fill=(255, 255, 255))
    draw.line(rotated + [rotated[0]], fill=(0, 0, 0), width=3)

    dot_r = size // 14
    if value == 1: positions = [(0, 0)]
    elif value == 2: positions = [(-1, -1), (1, 1)]
    elif value == 3: positions = [(-1, -1), (0, 0), (1, 1)]
    elif value == 4: positions = [(-1, -1), (1, -1), (-1, 1), (1, 1)]
    elif value == 5: positions = [(-1, -1), (1, -1), (0, 0), (-1, 1), (1, 1)]
    elif value == 6: positions = [(-1, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (1, 1)]
    else: positions = []

    for (dx, dy) in positions:
        rx = dx * size * 0.30 * cos_a - dy * size * 0.30 * sin_a + cx
        ry = dx * size * 0.30 * sin_a + dy * size * 0.30 * cos_a + cy
        draw.ellipse([rx - dot_r, ry - dot_r, rx + dot_r, ry + dot_r], fill=(230, 60, 60))

def generate_result_image(prediction, actual, dice, total, session_result,
                            session_next, confidence):
    W, H = 600, 600
    img = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Gradient background
    for y in range(H):
        ratio = y / H
        r = int(5 + 15 * ratio)
        g = int(5 + 25 * ratio)
        b = int(15 + 40 * ratio)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    # Outer gold borders
    draw.rectangle([8, 8, W-8, H-8], outline=(200, 160, 30), width=3)
    draw.rectangle([12, 12, W-12, H-12], outline=(255, 215, 0), width=2)

    # Blue header box
    draw.rectangle([16, 16, W-16, 500], fill=(0, 120, 200), outline=(255, 215, 0), width=3)
    draw.rectangle([20, 20, W-20, 496], outline=(255, 240, 100), width=1)

    # TÀI/XỈU top-left
    label = "TÀI" if actual == "TAI" else "XỈU"
    font_label = load_font(72)
    draw.text((43, 43), label, fill=(0, 0, 0), font=font_label)
    draw.text((40, 40), label, fill=(255, 230, 0), font=font_label)

    # Total top-right
    font_total = load_font(80)
    total_str = str(total) if total else "--"
    bbox = draw.textbbox((0, 0), total_str, font=font_total)
    tw = bbox[2] - bbox[0]
    tx = W - 45 - tw
    draw.text((tx+3, 33), total_str, fill=(0, 0, 0), font=font_total)
    draw.text((tx, 30), total_str, fill=(255, 255, 255), font=font_total)

    # White circle
    cx, cy = W // 2, 290
    circle_r = 175
    draw.ellipse([cx-circle_r, cy-circle_r, cx+circle_r, cy+circle_r],
                  fill=(255, 255, 255), outline=(0, 0, 0), width=4)

    # Dice
    if dice and len(dice) >= 3:
        d_size = 95
        draw_dice_card(draw, cx - 55, cy - 55, d_size, dice[0], angle=-15)
        draw_dice_card(draw, cx + 55, cy - 40, d_size, dice[1], angle=10)
        draw_dice_card(draw, cx + 10, cy + 70, d_size, dice[2], angle=-8)

    # Tool name
    font_tool = load_font(40)
    bbox = draw.textbbox((0, 0), TOOL_NAME, font=font_tool)
    tw = bbox[2] - bbox[0]
    tx = (W - tw) // 2
    draw.text((tx+2, 454), TOOL_NAME, fill=(0, 0, 0), font=font_tool)
    draw.text((tx, 452), TOOL_NAME, fill=(255, 230, 0), font=font_tool)

    # Bottom panel
    draw.rectangle([16, 508, W-16, H-16], fill=(0, 0, 0), outline=(255, 215, 0), width=2)

    font_info = load_font(20)
    font_small = load_font(16)

    y = 522
    draw.text((35, y), "AI LC79", fill=(0, 255, 200), font=font_info)
    draw.text((200, y), f"-- {TOOL_NAME}", fill=(255, 255, 255), font=font_info)

    y += 30
    draw.text((35, y), "Ban MD5", fill=(255, 100, 100), font=font_info)
    draw.text((180, y), f"Phien #{session_result}", fill=(255, 220, 0), font=font_info)

    y += 30
    actual_txt = "Tai" if actual == "TAI" else "Xiu"
    draw.text((35, y), "Ket qua:", fill=(200, 200, 200), font=font_info)
    draw.text((180, y), f"[OK] {actual_txt}", fill=(100, 255, 100), font=font_info)

    y += 30
    draw.line([35, y, W-35, y], fill=(255, 140, 0), width=2)
    y += 12

    draw.text((35, y), "Ban MD5", fill=(255, 100, 100), font=font_info)
    draw.text((180, y), f"Phien moi #{session_next}", fill=(255, 220, 0), font=font_info)

    y += 30
    pred_txt = "Tai" if prediction == "TAI" else "Xiu"
    draw.text((35, y), "Du doan:", fill=(200, 200, 200), font=font_info)
    pred_color = (255, 100, 100) if prediction == "TAI" else (100, 180, 255)
    draw.text((180, y), f"[OK] {pred_txt}", fill=pred_color, font=font_info)

    y += 30
    draw.text((35, y), "Do tin cay:", fill=(200, 200, 200), font=font_info)
    draw.text((200, y), f"{confidence}%", fill=(0, 255, 200), font=font_info)

    y += 30
    draw.text((35, y), "Nhom ho tro:", fill=(200, 200, 200), font=font_small)
    draw.text((35, y + 22), GROUP_LINK_TEXT, fill=(100, 180, 255), font=font_small)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ==================== STATE ====================
state = {
    "running": True,
    "last_session_id": None,
    "next_prediction": None,
    "win_count": 0,
    "loss_count": 0,
    "history_logs": [],
    "last_result": None,
}

# ==================== FETCH ====================
def get_latest_data():
    try:
        res = requests.get(API_URL, headers=HEADERS, timeout=8)
        if res.status_code == 200:
            return res.json()
    except Exception as e:
        log.warning(f"fetch err: {e}")
    return None

# ==================== FORMAT ====================
def fmt_prediction(session_id, engine_out):
    pred = engine_out["prediction"]
    conf = engine_out["confidence"]
    reason = ", ".join(engine_out.get("reasons", [])[:3])
    arrow = "🟢 TÀI" if pred == "TAI" else "🔵 XỈU"

    return (
        f"🎯 <b>DỰ ĐOÁN PHIÊN #{session_id}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Kết quả: <b>{arrow}</b>\n"
        f"⚡ Tin cậy: <b>{conf*100:.1f}%</b>\n"
        f"📊 Regime: <b>{engine_out.get('regime', '?')}</b>\n"
        f"🧠 Model: {engine_out.get('model_count', 0)} | Agree: {engine_out.get('agree', 0)}%\n"
        f"🔍 <i>{reason}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💬 Nhóm: <a href='{GROUP_LINK}'>{GROUP_LINK_TEXT}</a>"
    )

def send_result_with_image(session_id, actual, predicted, correct,
                             win, loss, dice, total):
    icon = "✅" if correct else "❌"
    status = "WIN" if correct else "LOSE"
    total_games = win + loss
    wr = (win / total_games * 100) if total_games > 0 else 0.0

    caption = (
        f"📍 <b>LC79 MD5— AI v4</b> ⚙️📈\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Phiên <b>#{session_id}</b>\n"
        f"📌 Đối soát: <b>{icon} {status}</b>\n"
        f"🎯 Đoán: <b>{predicted}</b> | Ra: <b>{actual}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📈 <b>{win}W</b> / <b>{loss}L</b> — WR: <b>{wr:.1f}%</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💬 Nhóm: <a href='{GROUP_LINK}'>{GROUP_LINK_TEXT}</a>"
    )

    try:
        img_buf = generate_result_image(
            prediction=predicted,
            actual=actual,
            dice=dice if dice else [0, 0, 0],
            total=total if total else 0,
            session_result=session_id,
            session_next=session_id + 1,
            confidence=int(70 + (win / max(total_games, 1)) * 20),
        )
        bot.send_photo(CHAT_ID, img_buf, caption=caption, parse_mode="HTML")
    except Exception as e:
        log.warning(f"send_photo err: {e}")
        bot.send_message(CHAT_ID, caption, parse_mode="HTML")

# ==================== POLL LOOP ====================
def poll_loop():
    log.info("Poll loop started")
    while True:
        if not state["running"]:
            time.sleep(POLL_SEC)
            continue
        try:
            data = get_latest_data()
            if data and "list" in data and len(data["list"]) > 0:
                sessions = data["list"]
                latest_game = sessions[0]
                current_id = latest_game.get("id")
                actual_result = latest_game.get("resultTruyenThong")

                if current_id != state["last_session_id"]:
                    np = state["next_prediction"]

                    if np is not None and np.get("target_id") == current_id:
                        predicted = np["suggestion"]
                        correct = predicted == actual_result
                        if correct: state["win_count"] += 1
                        else: state["loss_count"] += 1
                        log_entry = f"#{current_id} | {predicted} → {actual_result} | {'OK' if correct else 'FAIL'}"
                        state["history_logs"].append(log_entry)

                        dice = latest_game.get("dices") or latest_game.get("dice") or []
                        total = latest_game.get("point") or latest_game.get("totalPoint")

                        if CHAT_ID:
                            send_result_with_image(
                                current_id, actual_result, predicted, correct,
                                state["win_count"], state["loss_count"],
                                dice, total
                            )
                        if len(state["history_logs"]) > 5:
                            state["history_logs"].pop(0)

                    reversed_sessions = list(reversed(sessions))
                    engine_out = run_engine(reversed_sessions)
                    target_session_id = current_id + 1

                    state["next_prediction"] = {
                        "target_id": target_session_id,
                        "suggestion": engine_out["prediction"],
                    }
                    state["last_session_id"] = current_id
                    state["last_result"] = actual_result

                    if CHAT_ID:
                        msg = fmt_prediction(target_session_id, engine_out)
                        try:
                            bot.send_message(CHAT_ID, msg, parse_mode="HTML")
                            log.info(f"Sent #{target_session_id}: {engine_out['prediction']} ({engine_out['confidence']*100:.1f}%)")
                        except Exception as e:
                            log.warning(f"send prediction: {e}")
        except Exception as e:
            log.error(f"poll err: {e}")
        time.sleep(POLL_SEC)

# ==================== FLASK ====================
flask_app = Flask(__name__)
_start_time = time.time()

@flask_app.route("/")
def home():
    return jsonify({
        "status": "ok", "service": "lc79-bot-v4",
        "uptime": round(time.time() - _start_time, 2),
        "running": state["running"],
        "win": state["win_count"], "loss": state["loss_count"],
    })

@flask_app.route("/health")
def health():
    total = state["win_count"] + state["loss_count"]
    wr = (state["win_count"] / total * 100) if total > 0 else 0.0
    return jsonify({
        "status": "healthy",
        "win": state["win_count"], "loss": state["loss_count"],
        "win_rate": f"{wr:.1f}%",
        "last_session": state["last_session_id"],
    })

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# ==================== KEYBOARD ====================
def main_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    toggle = "🔴 Tắt auto" if state["running"] else "🟢 Bật auto"
    kb.add(types.InlineKeyboardButton(toggle, callback_data="toggle"))
    kb.add(
        types.InlineKeyboardButton("📊 Thống kê", callback_data="stats"),
        types.InlineKeyboardButton("📜 Lịch sử", callback_data="history"),
    )
    kb.add(types.InlineKeyboardButton("🎯 Dự đoán ngay", callback_data="predict"))
    kb.add(types.InlineKeyboardButton("🔄 Reset", callback_data="reset"))
    return kb

# ==================== COMMANDS ====================
def is_admin(uid): return uid == ADMIN_ID

@bot.message_handler(commands=["start", "menu"])
def cmd_start(m):
    bot.reply_to(
        m,
        f"🤖 <b>LC79 Bot v4 — 31 Model</b>\n\n"
        f"Tool: {TOOL_NAME}\n"
        f"Nhóm: {GROUP_LINK_TEXT}\n\n"
        "/stats — thống kê\n"
        "/predict — dự đoán ngay\n"
        "/chatid — lấy chat ID\n"
        "/toggle — bật/tắt auto (admin)\n"
        "/reset — reset điểm (admin)",
        parse_mode="HTML", reply_markup=main_keyboard(),
    )

@bot.message_handler(commands=["chatid"])
def cmd_chatid(m):
    bot.reply_to(m, f"Chat ID: <code>{m.chat.id}</code>", parse_mode="HTML")

@bot.message_handler(commands=["stats"])
def cmd_stats(m):
    win = state["win_count"]; loss = state["loss_count"]
    total = win + loss
    wr = (win / total * 100) if total > 0 else 0.0
    txt = (
        f"📊 <b>THỐNG KÊ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Thắng: <b>{win}</b>\n"
        f"❌ Thua: <b>{loss}</b>\n"
        f"📈 Tổng: <b>{total}</b> ván\n"
        f"🎯 Win Rate: <b>{wr:.1f}%</b>\n"
        f"🟢 Auto: <b>{'ON' if state['running'] else 'OFF'}</b>\n"
        f"📌 Phiên cuối: <b>#{state['last_session_id'] or '—'}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💬 Nhóm: <a href='{GROUP_LINK}'>{GROUP_LINK_TEXT}</a>"
    )
    bot.reply_to(m, txt, parse_mode="HTML")

@bot.message_handler(commands=["predict"])
def cmd_predict(m):
    data = get_latest_data()
    if not data or "list" not in data:
        bot.reply_to(m, "⏳ Chưa có dữ liệu."); return
    sessions = data["list"]
    current_id = sessions[0].get("id")
    reversed_sessions = list(reversed(sessions))
    engine_out = run_engine(reversed_sessions)
    msg = fmt_prediction(current_id + 1, engine_out)
    bot.reply_to(m, msg, parse_mode="HTML")

@bot.message_handler(commands=["toggle"])
def cmd_toggle(m):
    if not is_admin(m.from_user.id):
        bot.reply_to(m, "⛔ Admin only"); return
    state["running"] = not state["running"]
    bot.reply_to(m, f"🔄 Auto: {'🟢 ON' if state['running'] else '🔴 OFF'}", reply_markup=main_keyboard())

@bot.message_handler(commands=["reset"])
def cmd_reset(m):
    if not is_admin(m.from_user.id):
        bot.reply_to(m, "⛔ Admin only"); return
    state["win_count"] = 0
    state["loss_count"] = 0
    state["history_logs"] = []
    state["next_prediction"] = None
    state["last_session_id"] = None
    bot.reply_to(m, "✅ Đã reset.")

# ==================== CALLBACK ====================
@bot.callback_query_handler(func=lambda c: True)
def on_callback(c):
    data = c.data
    if data == "toggle":
        if not is_admin(c.from_user.id):
            bot.answer_callback_query(c.id, "⛔ Admin only"); return
        state["running"] = not state["running"]
        bot.answer_callback_query(c.id, f"Auto: {'ON' if state['running'] else 'OFF'}")
        try:
            bot.edit_message_reply_markup(chat_id=c.message.chat.id,
                                          message_id=c.message.message_id,
                                          reply_markup=main_keyboard())
        except Exception: pass
    elif data == "stats":
        cmd_stats(c.message); bot.answer_callback_query(c.id)
    elif data == "history":
        logs = state["history_logs"]
        if not logs:
            bot.answer_callback_query(c.id, "Chưa có dữ liệu"); return
        txt = "📜 <b>5 PHIÊN GẦN NHẤT</b>\n" + "\n".join(f"• {l}" for l in logs)
        bot.send_message(c.message.chat.id, txt, parse_mode="HTML")
        bot.answer_callback_query(c.id)
    elif data == "predict":
        cmd_predict(c.message); bot.answer_callback_query(c.id)
    elif data == "reset":
        if not is_admin(c.from_user.id):
            bot.answer_callback_query(c.id, "⛔ Admin only"); return
        state["win_count"] = 0
        state["loss_count"] = 0
        state["history_logs"] = []
        state["next_prediction"] = None
        state["last_session_id"] = None
        bot.answer_callback_query(c.id, "✅ Đã reset")
        bot.send_message(c.message.chat.id, "✅ Đã reset.")

# ==================== MAIN ====================
def main():
    log.info("Bot starting...")
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=poll_loop, daemon=True).start()
    bot.infinity_polling(timeout=30, long_polling_timeout=30)

if __name__ == "__main__":
    main()
