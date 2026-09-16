# =====================================================================
# LC79 TELE68 BOT v9 — 2 ẢNH RIÊNG (DỰ ĐOÁN + KẾT QUẢ)
# API: wtxmd52.tele68.com/v1/txmd5/sessions
# Tool: TOOL THANH DUY
# Nhóm: https://t.me/+LaaT-vDzLo02MDJl
# =====================================================================

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
    tx = [s.get("resultTruyenThong") for s in sessions if s.get("resultTruyenThong")]
    totals = []
    for s in sessions:
        p = s.get("point") or s.get("totalPoint") or s.get("dicesSum")
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
        "meanTotal": avg(totals),
        "stdTotal": std(totals),
        "recentMean": avg(totals[-10:]),
        "maxRun": max((r["len"] for r in rs), default=0),
    }

# ==================== ENGINE 12 LAYER ====================
def engine_v2(sessions):
    f = extract_features(sessions)
    if not f or f["n"] < 10:
        return "TAI", 50.0, "Đang nạp đủ 10 phiên..."

    n = f["n"]
    results = f["tx"]
    last_res = results[-1]

    L = {f"L{i}": 0 for i in range(1, 13)}

    # L1: Anti-break
    if f["cur"]["len"] >= 3:
        L["L1"] = 1

    # L2: Cầu 1-1
    if (n >= 4 and results[-1] != results[-2]
        and results[-2] != results[-3] and results[-3] != results[-4]):
        L["L2"] = 1

    # L3: Markov n-gram=3
    pat_cnt = collections.defaultdict(lambda: {"TAI": 0, "XIU": 0})
    for i in range(n - 3):
        pat = f"{results[i]}-{results[i+1]}-{results[i+2]}"
        nxt = results[i+3]
        pat_cnt[pat][nxt] += 1
    last_pat3 = f"{results[-3]}-{results[-2]}-{results[-1]}"
    pat3 = pat_cnt[last_pat3]
    tot3 = pat3["TAI"] + pat3["XIU"]
    markov3_score = (pat3["TAI"] / tot3 - 0.5) * 100 if tot3 > 0 else 0
    L["L3"] = markov3_score * 0.25

    # L4: Markov n-gram=4
    pat4_cnt = collections.defaultdict(lambda: {"TAI": 0, "XIU": 0})
    for i in range(n - 4):
        pat = f"{results[i]}-{results[i+1]}-{results[i+2]}-{results[i+3]}"
        nxt = results[i+4]
        pat4_cnt[pat][nxt] += 1
    last_pat4 = "-".join(results[-4:])
    pat4 = pat4_cnt[last_pat4]
    tot4 = pat4["TAI"] + pat4["XIU"]
    markov4_score = (pat4["TAI"] / tot4 - 0.5) * 100 if tot4 > 0 else 0
    L["L4"] = markov4_score * 0.20

    # L5: Point bias
    point_bias = 0
    if len(f["totals"]) >= 3:
        avg3 = sum(f["totals"][-3:]) / 3.0
        if avg3 <= 6.0: point_bias = 15
        elif avg3 >= 15.0: point_bias = -15
    L["L5"] = point_bias * 1.0

    # L6: Freq 10
    r10 = results[-10:]
    t10 = r10.count("TAI")
    x10 = 10 - t10
    freq10_bias = (t10 - x10) * 3
    L["L6"] = freq10_bias * 0.8

    # L7: Freq 20
    freq20_bias = 0
    if n >= 20:
        r20 = results[-20:]
        t20 = r20.count("TAI")
        freq20_bias = (t20 - 10) * 1.5
    L["L7"] = freq20_bias * 0.5

    # L8: Momentum
    momentum_score = 0
    if n >= 20:
        trend5 = results[-5:].count("TAI") / 5
        trend20 = results[-20:].count("TAI") / 20
        momentum_score = (trend5 - trend20) * 50
    L["L8"] = momentum_score * 0.4

    # L9: Volatility
    volatility_score = 0
    if f["switchRate"] > 0.75:
        volatility_score = -10
    L["L9"] = volatility_score * 0.3

    # L10: Entropy
    entropy_score = 0
    if f["ent"] > 0.95:
        if t10 > x10: entropy_score = 10
        elif x10 > t10: entropy_score = -10
    elif f["ent"] < 0.4:
        if f["ratio"] > 0.6: entropy_score = 8
        elif f["ratio"] < 0.4: entropy_score = -8
    L["L10"] = entropy_score * 0.5

    # L11: Streak ratio
    streak_score = 0
    if f["cur"]["len"] == 2:
        streak_score = 5 if f["cur"]["val"] == "TAI" else -5
    L["L11"] = streak_score * 0.4

    # L12: Recent 5 contrarian
    recent5_bias = 0
    r5 = results[-5:]
    t5 = r5.count("TAI")
    if t5 >= 4: recent5_bias = -8
    elif t5 <= 1: recent5_bias = 8
    L["L12"] = recent5_bias * 1.0

    # OVERRIDE bởi L1/L2
    final_score = 50.0 + sum(L.values())

    if L["L1"] == 1:
        suggestion = last_res
        conf = 88.5
    elif L["L2"] == 1:
        suggestion = "XIU" if last_res == "TAI" else "TAI"
        conf = 82.0
    elif final_score >= 50.0:
        suggestion = "TAI"
        conf = min(92.0, max(58.0, final_score))
    else:
        suggestion = "XIU"
        conf = min(92.0, max(58.0, 100.0 - final_score))

    reason = " | ".join(f"{k}:{v:+.1f}" for k, v in L.items())
    return suggestion, conf, reason

# ==================== IMAGE HELPERS ====================
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

def draw_base_card(draw, W, H, label, total, dice, tool_name):
    """Vẽ phần trên: gradient + border + label + xúc xắc + tên tool."""
    # Gradient
    for y in range(H):
        ratio = y / H
        r = int(5 + 15 * ratio)
        g = int(5 + 25 * ratio)
        b = int(15 + 40 * ratio)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    # Borders
    draw.rectangle([8, 8, W-8, H-8], outline=(200, 160, 30), width=3)
    draw.rectangle([12, 12, W-12, H-12], outline=(255, 215, 0), width=2)

    # Blue header (500px tall)
    draw.rectangle([16, 16, W-16, 500], fill=(0, 120, 200), outline=(255, 215, 0), width=3)
    draw.rectangle([20, 20, W-20, 496], outline=(255, 240, 100), width=1)

    # Label
    font_label = load_font(72)
    draw.text((43, 43), label, fill=(0, 0, 0), font=font_label)
    draw.text((40, 40), label, fill=(255, 230, 0), font=font_label)

    # Total
    font_total = load_font(80)
    total_str = str(total) if total else "--"
    bbox = draw.textbbox((0, 0), total_str, font=font_total)
    tw = bbox[2] - bbox[0]
    tx = W - 45 - tw
    draw.text((tx+3, 33), total_str, fill=(0, 0, 0), font=font_total)
    draw.text((tx, 30), total_str, fill=(255, 255, 255), font=font_total)

    # White circle + dice
    cx, cy = W // 2, 290
    circle_r = 175
    draw.ellipse([cx-circle_r, cy-circle_r, cx+circle_r, cy+circle_r],
                  fill=(255, 255, 255), outline=(0, 0, 0), width=4)

    if dice and len(dice) >= 3:
        d_size = 95
        draw_dice_card(draw, cx - 55, cy - 55, d_size, dice[0], angle=-15)
        draw_dice_card(draw, cx + 55, cy - 40, d_size, dice[1], angle=10)
        draw_dice_card(draw, cx + 10, cy + 70, d_size, dice[2], angle=-8)

    # Tool name
    font_tool = load_font(40)
    bbox = draw.textbbox((0, 0), tool_name, font=font_tool)
    tw = bbox[2] - bbox[0]
    tx = (W - tw) // 2
    draw.text((tx+2, 454), tool_name, fill=(0, 0, 0), font=font_tool)
    draw.text((tx, 452), tool_name, fill=(255, 230, 0), font=font_tool)

# ==================== IMAGE: PREDICTION ====================
def generate_prediction_image(prediction, session_next, confidence, dice, total):
    """Ảnh dự đoán — đủ chỗ, không bị che."""
    W, H = 600, 720  # Tăng H từ 600 → 720
    img = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Vẽ phần trên cùng với H = 600 (giữ nguyên tỷ lệ)
    label = "TÀI" if prediction == "TAI" else "XỈU"

    # Gradient full H
    for y in range(H):
        ratio = y / H
        r = int(5 + 15 * ratio)
        g = int(5 + 25 * ratio)
        b = int(15 + 40 * ratio)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    # Borders
    draw.rectangle([8, 8, W-8, H-8], outline=(200, 160, 30), width=3)
    draw.rectangle([12, 12, W-12, H-12], outline=(255, 215, 0), width=2)

    # Blue header
    draw.rectangle([16, 16, W-16, 500], fill=(0, 120, 200), outline=(255, 215, 0), width=3)
    draw.rectangle([20, 20, W-20, 496], outline=(255, 240, 100), width=1)

    # Label
    font_label = load_font(72)
    draw.text((43, 43), label, fill=(0, 0, 0), font=font_label)
    draw.text((40, 40), label, fill=(255, 230, 0), font=font_label)

    # Total
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

    # ============ BOTTOM PANEL (từ 520 → 700) ============
    draw.rectangle([16, 520, W-16, 704], fill=(0, 0, 0), outline=(255, 215, 0), width=2)

    font_info = load_font(24)
    font_big = load_font(28)

    y = 540
    draw.text((40, y), "AI LC79", fill=(0, 255, 200), font=font_info)
    draw.text((210, y), f"-- {TOOL_NAME}", fill=(255, 255, 255), font=font_info)

    y += 42
    draw.text((40, y), "Ban MD5", fill=(255, 100, 100), font=font_info)
    draw.text((210, y), f"Phien #{session_next}", fill=(255, 220, 0), font=font_info)

    y += 42
    pred_txt = "Tai" if prediction == "TAI" else "Xiu"
    pred_color = (255, 100, 100) if prediction == "TAI" else (100, 180, 255)
    draw.text((40, y), "Du doan:", fill=(200, 200, 200), font=font_info)
    draw.text((210, y), f"-> {pred_txt}", fill=pred_color, font=font_big)

    y += 42
    draw.text((40, y), "Do tin cay:", fill=(200, 200, 200), font=font_info)
    draw.text((210, y), f"{confidence}%", fill=(0, 255, 200), font=font_big)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ==================== IMAGE: RESULT ====================
def generate_result_image(actual, session_result, dice, total):
    """Ảnh kết quả — đủ chỗ, không bị che."""
    W, H = 600, 720
    img = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    label = "TÀI" if actual == "TAI" else "XỈU"

    # Gradient
    for y in range(H):
        ratio = y / H
        r = int(5 + 15 * ratio)
        g = int(5 + 25 * ratio)
        b = int(15 + 40 * ratio)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    # Borders
    draw.rectangle([8, 8, W-8, H-8], outline=(200, 160, 30), width=3)
    draw.rectangle([12, 12, W-12, H-12], outline=(255, 215, 0), width=2)

    # Blue header
    draw.rectangle([16, 16, W-16, 500], fill=(0, 120, 200), outline=(255, 215, 0), width=3)
    draw.rectangle([20, 20, W-20, 496], outline=(255, 240, 100), width=1)

    # Label
    font_label = load_font(72)
    draw.text((43, 43), label, fill=(0, 0, 0), font=font_label)
    draw.text((40, 40), label, fill=(255, 230, 0), font=font_label)

    # Total
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
    draw.rectangle([16, 520, W-16, 704], fill=(0, 0, 0), outline=(255, 215, 0), width=2)

    font_info = load_font(24)
    font_big = load_font(28)

    y = 540
    draw.text((40, y), "AI LC79", fill=(0, 255, 200), font=font_info)
    draw.text((210, y), f"-- {TOOL_NAME}", fill=(255, 255, 255), font=font_info)

    y += 42
    draw.text((40, y), "Ban MD5", fill=(255, 100, 100), font=font_info)
    draw.text((210, y), f"Phien #{session_result}", fill=(255, 220, 0), font=font_info)

    y += 42
    actual_txt = "Tai" if actual == "TAI" else "Xiu"
    actual_color = (255, 100, 100) if actual == "TAI" else (100, 180, 255)
    draw.text((40, y), "Ket qua:", fill=(200, 200, 200), font=font_info)
    draw.text((210, y), f"-> {actual_txt}", fill=actual_color, font=font_big)

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

# ==================== SEND ====================
def send_prediction_image(prediction, session_next, confidence, dice, total):
    """Gửi ảnh dự đoán — caption chỉ link nhóm."""
    try:
        img_buf = generate_prediction_image(prediction, session_next, confidence, dice, total)
        caption = f"💬 Nhóm hỗ trợ: <a href='{GROUP_LINK}'>{GROUP_LINK_TEXT}</a>"
        bot.send_photo(CHAT_ID, img_buf, caption=caption, parse_mode="HTML")
    except Exception as e:
        log.warning(f"send_prediction_image err: {e}")

def send_result_image(actual, session_result, dice, total, correct, predicted):
    """Gửi ảnh kết quả — caption link nhóm + đúng/sai."""
    try:
        img_buf = generate_result_image(actual, session_result, dice, total)
        icon = "✅ ĐÚNG" if correct else "❌ SAI"
        caption = (
            f"{icon} | Phiên #{session_result}: {predicted} → {actual}\n"
            f"💬 Nhóm hỗ trợ: <a href='{GROUP_LINK}'>{GROUP_LINK_TEXT}</a>"
        )
        bot.send_photo(CHAT_ID, img_buf, caption=caption, parse_mode="HTML")
    except Exception as e:
        log.warning(f"send_result_image err: {e}")

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

                    # 1. Chấm điểm + gửi ảnh kết quả
                    if np is not None and np.get("target_id") == current_id:
                        predicted = np["suggestion"]
                        correct = predicted == actual_result
                        if correct: state["win_count"] += 1
                        else: state["loss_count"] += 1

                        dice = latest_game.get("dices") or latest_game.get("dice") or []
                        total = latest_game.get("point") or latest_game.get("totalPoint")

                        if CHAT_ID:
                            send_result_image(actual_result, current_id, dice, total,
                                              correct, predicted)

                    # 2. Dự đoán phiên mới + gửi ảnh dự đoán
                    reversed_sessions = list(reversed(sessions))
                    suggestion, confidence, reason = engine_v2(reversed_sessions)
                    target_session_id = current_id + 1

                    state["next_prediction"] = {
                        "target_id": target_session_id,
                        "suggestion": suggestion,
                    }
                    state["last_session_id"] = current_id
                    state["last_result"] = actual_result

                    if CHAT_ID:
                        dice_display = latest_game.get("dices") or latest_game.get("dice") or []
                        total_display = latest_game.get("point") or latest_game.get("totalPoint")
                        send_prediction_image(
                            suggestion, target_session_id, confidence,
                            dice_display, total_display
                        )
                        log.info(f"Sent #{target_session_id}: {suggestion} ({confidence:.1f}%)")
        except Exception as e:
            log.error(f"poll err: {e}")
        time.sleep(POLL_SEC)

# ==================== FLASK ====================
flask_app = Flask(__name__)
_start_time = time.time()

@flask_app.route("/")
def home():
    return jsonify({
        "status": "ok", "service": "lc79-bot-v9",
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
        types.InlineKeyboardButton("🎯 Dự đoán ngay", callback_data="predict"),
    )
    kb.add(types.InlineKeyboardButton("🔄 Reset", callback_data="reset"))
    return kb

# ==================== COMMANDS ====================
def is_admin(uid): return uid == ADMIN_ID

@bot.message_handler(commands=["start", "menu"])
def cmd_start(m):
    bot.reply_to(
        m,
        f"🤖 <b>LC79 Bot v9</b>\n\n"
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
    latest = sessions[0]
    current_id = latest.get("id")
    reversed_sessions = list(reversed(sessions))
    suggestion, confidence, reason = engine_v2(reversed_sessions)
    dice = latest.get("dices") or latest.get("dice") or []
    total = latest.get("point") or latest.get("totalPoint")
    send_prediction_image(suggestion, current_id + 1, confidence, dice, total)

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
    elif data == "predict":
        cmd_predict(c.message); bot.answer_callback_query(c.id)
    elif data == "reset":
        if not is_admin(c.from_user.id):
            bot.answer_callback_query(c.id, "⛔ Admin only"); return
        state["win_count"] = 0
        state["loss_count"] = 0
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
