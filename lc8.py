import os
import time
import json
import math
import collections
import logging
import threading
import requests
import telebot
from telebot import types
from flask import Flask, jsonify

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

    # ============ LAYER 1: Anti-break ============
    if f["cur"]["len"] >= 3:
        return (last_res, 88.5, f"L1 Anti-break: bệt {last_res} x{f['cur']['len']} → theo bệt")

    # ============ LAYER 2: Cầu 1-1 ============
    if (n >= 4
        and results[-1] != results[-2]
        and results[-2] != results[-3]
        and results[-3] != results[-4]):
        next_11 = "XIU" if last_res == "TAI" else "TAI"
        return (next_11, 82.0, f"L2 Cầu 1-1 ({'-'.join(results[-4:])}) → đảo {next_11}")

    # ============ LAYER 3: Markov n-gram=3 ============
    pat_cnt = collections.defaultdict(lambda: {"TAI": 0, "XIU": 0})
    for i in range(n - 3):
        pat = f"{results[i]}-{results[i+1]}-{results[i+2]}"
        nxt = results[i+3]
        pat_cnt[pat][nxt] += 1
    last_pat3 = f"{results[-3]}-{results[-2]}-{results[-1]}"
    pat3 = pat_cnt[last_pat3]
    tot3 = pat3["TAI"] + pat3["XIU"]
    markov3_score = (pat3["TAI"] / tot3 - 0.5) * 100 if tot3 > 0 else 0

    # ============ LAYER 4: Markov n-gram=4 ============
    pat4_cnt = collections.defaultdict(lambda: {"TAI": 0, "XIU": 0})
    for i in range(n - 4):
        pat = f"{results[i]}-{results[i+1]}-{results[i+2]}-{results[i+3]}"
        nxt = results[i+4]
        pat4_cnt[pat][nxt] += 1
    last_pat4 = "-".join(results[-4:])
    pat4 = pat4_cnt[last_pat4]
    tot4 = pat4["TAI"] + pat4["XIU"]
    markov4_score = (pat4["TAI"] / tot4 - 0.5) * 100 if tot4 > 0 else 0

    # ============ LAYER 5: Point bias ============
    point_bias = 0
    if len(f["totals"]) >= 3:
        avg3 = sum(f["totals"][-3:]) / 3.0
        if avg3 <= 6.0: point_bias = 15
        elif avg3 >= 15.0: point_bias = -15

    # ============ LAYER 6: Freq 10 ============
    r10 = results[-10:]
    t10 = r10.count("TAI")
    x10 = 10 - t10
    freq10_bias = (t10 - x10) * 3

    # ============ LAYER 7: Freq 20 ============
    freq20_bias = 0
    if n >= 20:
        r20 = results[-20:]
        t20 = r20.count("TAI")
        freq20_bias = (t20 - 10) * 1.5

    # ============ LAYER 8: Momentum ============
    momentum_score = 0
    if n >= 20:
        trend5 = results[-5:].count("TAI") / 5
        trend20 = results[-20:].count("TAI") / 20
        momentum_score = (trend5 - trend20) * 50

    # ============ LAYER 9: Volatility ============
    volatility_score = 0
    if f["switchRate"] > 0.75:
        volatility_score = -10

    # ============ LAYER 10: Entropy ============
    entropy_score = 0
    if f["ent"] > 0.95:
        if t10 > x10: entropy_score = 10
        elif x10 > t10: entropy_score = -10
    elif f["ent"] < 0.4:
        if f["ratio"] > 0.6: entropy_score = 8
        elif f["ratio"] < 0.4: entropy_score = -8

    # ============ LAYER 11: Streak ratio ============
    streak_score = 0
    if f["cur"]["len"] == 2:
        streak_score = 5 if f["cur"]["val"] == "TAI" else -5

    # ============ LAYER 12: Recent 5 contrarian ============
    recent5_bias = 0
    r5 = results[-5:]
    t5 = r5.count("TAI")
    if t5 >= 4: recent5_bias = -8
    elif t5 <= 1: recent5_bias = 8

    # ============ TỔNG HỢP ============
    final_score = 50.0
    final_score += markov3_score * 0.25
    final_score += markov4_score * 0.20
    final_score += point_bias * 1.0
    final_score += freq10_bias * 0.8
    final_score += freq20_bias * 0.5
    final_score += momentum_score * 0.4
    final_score += volatility_score * 0.3
    final_score += entropy_score * 0.5
    final_score += streak_score * 0.4
    final_score += recent5_bias * 1.0

    if final_score >= 50.0:
        suggestion = "TAI"
        conf = min(92.0, max(58.0, final_score))
    else:
        suggestion = "XIU"
        conf = min(92.0, max(58.0, 100.0 - final_score))

    reason = f"12L | M3:{markov3_score:+.0f} M4:{markov4_score:+.0f} P:{point_bias:+d} F:{freq10_bias:+.0f}"
    return suggestion, conf, reason

# ==================== FETCH ====================
def get_latest_data():
    try:
        res = requests.get(API_URL, headers=HEADERS, timeout=8)
        if res.status_code == 200:
            return res.json()
    except Exception as e:
        log.warning(f"fetch err: {e}")
    return None

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

# ==================== FORMAT ====================
def fmt_prediction(session_id, suggestion, confidence, reason):
    arrow = "🟢 TÀI" if suggestion == "TAI" else "🔵 XỈU"
    return (
        f"🎯 <b>DỰ ĐOÁN PHIÊN #{session_id}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Kết quả: <b>{arrow}</b>\n"
        f"⚡ Tin cậy: <b>{confidence:.1f}%</b>\n"
        f"🔍 <i>{reason}</i>"
    )

def fmt_result(session_id, actual, predicted, correct, win, loss):
    icon = "✅" if correct else "❌"
    status = "WIN" if correct else "LOSE"
    total = win + loss
    wr = (win / total * 100) if total > 0 else 0.0
    return (
        f"📍 <b>LC79 — AI v2 12L</b> ⚙️📈\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Phiên <b>#{session_id}</b>\n"
        f"📌 Đối soát: <b>{icon} {status}</b>\n"
        f"🎯 Đoán: <b>{predicted}</b> | Ra: <b>{actual}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📈 Tổng: <b>{win}W</b> / <b>{loss}L</b> — WR: <b>{wr:.1f}%</b>"
    )

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
                        log_entry = f"#{current_id} | {predicted} → {actual_result} | {'✅' if correct else '❌'}"
                        state["history_logs"].append(log_entry)
                        if len(state["history_logs"]) > 5:
                            state["history_logs"].pop(0)
                        if CHAT_ID:
                            msg = fmt_result(
                                current_id, actual_result, predicted, correct,
                                state["win_count"], state["loss_count"]
                            )
                            try:
                                bot.send_message(CHAT_ID, msg, parse_mode="HTML")
                            except Exception as e:
                                log.warning(f"send result: {e}")

                    reversed_sessions = list(reversed(sessions))
                    suggestion, confidence, reason = engine_v2(reversed_sessions)
                    target_session_id = current_id + 1

                    state["next_prediction"] = {
                        "target_id": target_session_id,
                        "suggestion": suggestion,
                    }
                    state["last_session_id"] = current_id
                    state["last_result"] = actual_result

                    if CHAT_ID and confidence > 50.0:
                        msg = fmt_prediction(target_session_id, suggestion, confidence, reason)
                        try:
                            bot.send_message(CHAT_ID, msg, parse_mode="HTML")
                            log.info(f"Sent prediction #{target_session_id}: {suggestion} ({confidence:.1f}%)")
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
        "status": "ok",
        "service": "lc79-bot-v2",
        "uptime": round(time.time() - _start_time, 2),
        "running": state["running"],
        "win": state["win_count"],
        "loss": state["loss_count"],
    })

@flask_app.route("/health")
def health():
    total = state["win_count"] + state["loss_count"]
    wr = (state["win_count"] / total * 100) if total > 0 else 0.0
    return jsonify({
        "status": "healthy",
        "win": state["win_count"],
        "loss": state["loss_count"],
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
    kb.add(types.InlineKeyboardButton("🔄 Reset điểm", callback_data="reset"))
    return kb

# ==================== COMMANDS ====================
def is_admin(uid): return uid == ADMIN_ID

@bot.message_handler(commands=["start", "menu"])
def cmd_start(m):
    bot.reply_to(
        m,
        "🤖 <b>LC79 TELE68 Bot v2</b>\n\n"
        "Engine 12 layer — auto hô mỗi phiên.\n"
        "API: tele68.com\n\n"
        "/stats — thống kê\n"
        "/predict — dự đoán ngay\n"
        "/chatid — lấy chat ID\n"
        "/toggle — bật/tắt auto (admin)\n"
        "/reset — reset điểm (admin)",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
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
        f"📌 Phiên cuối: <b>#{state['last_session_id'] or '—'}</b>"
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
    suggestion, confidence, reason = engine_v2(reversed_sessions)
    msg = fmt_prediction(current_id + 1, suggestion, confidence, reason)
    bot.reply_to(m, msg, parse_mode="HTML")

@bot.message_handler(commands=["toggle"])
def cmd_toggle(m):
    if not is_admin(m.from_user.id):
        bot.reply_to(m, "⛔ Admin only"); return
    state["running"] = not state["running"]
    bot.reply_to(m, f"🔄 Auto: {'🟢 ON' if state['running'] else '🔴 OFF'}",
                 reply_markup=main_keyboard())

@bot.message_handler(commands=["reset"])
def cmd_reset(m):
    if not is_admin(m.from_user.id):
        bot.reply_to(m, "⛔ Admin only"); return
    state["win_count"] = 0
    state["loss_count"] = 0
    state["history_logs"] = []
    state["next_prediction"] = None
    state["last_session_id"] = None
    bot.reply_to(m, "✅ Đã reset điểm.")

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
            bot.edit_message_reply_markup(
                chat_id=c.message.chat.id,
                message_id=c.message.message_id,
                reply_markup=main_keyboard(),
            )
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
        bot.send_message(c.message.chat.id, "✅ Đã reset điểm.")

# ==================== MAIN ====================
def main():
    log.info("Bot starting...")
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=poll_loop, daemon=True).start()
    bot.infinity_polling(timeout=30, long_polling_timeout=30)

if __name__ == "__main__":
    main()
