# =====================================================================
# TXMD5 MULTI-LAYER ENSEMBLE v14 — TELEGRAM BOT
# Telebot + Flask + Auto Score + Inline Buttons
# Deploy Render Free
# ENV: BOT_TOKEN, CHAT_ID, ADMIN_ID, POLL_INTERVAL
# =====================================================================

import os
import sys
import time
import json
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
log = logging.getLogger("txmd5-bot")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ==================== ENGINE ====================
def calculate_point_bias(points):
    if not points or len(points) < 3:
        return 0
    avg_point = sum(points[-3:]) / 3.0
    if avg_point <= 6.0:
        return 15
    elif avg_point >= 15.0:
        return -15
    return 0

def ensemble_algorithm(sessions):
    n = len(sessions)
    if n < 10:
        return "TAI", 50.0, "Đang nạp đủ 10 chuỗi dữ liệu..."

    results = [s.get("resultTruyenThong") for s in sessions]

    points = []
    for s in sessions:
        p = s.get("point") or s.get("totalPoint") or s.get("dicesSum")
        if p is not None:
            try: points.append(int(p))
            except: pass

    last_res = results[-1]

    # Layer 1: Anti-break rule
    streak = 1
    for i in range(n - 2, -1, -1):
        if results[i] == last_res:
            streak += 1
        else:
            break

    if streak >= 3:
        return (
            last_res,
            88.5,
            f"BẮT CHUỖI BỆT MẠNH {last_res} ({streak} tay liên tiếp) → Không bẻ",
        )

    # Layer 2: Markov n-gram = 3
    pattern_cnt = collections.defaultdict(lambda: {"TAI": 0, "XIU": 0})
    for i in range(n - 3):
        pat = f"{results[i]}-{results[i+1]}-{results[i+2]}"
        nxt = results[i + 3]
        pattern_cnt[pat][nxt] += 1

    last_pat = f"{results[-3]}-{results[-2]}-{results[-1]}"
    pat_data = pattern_cnt[last_pat]
    pat_total = pat_data["TAI"] + pat_data["XIU"]

    markov_score = 0
    if pat_total > 0:
        tai_prob = pat_data["TAI"] / pat_total
        markov_score = (tai_prob - 0.5) * 100

    # Layer 3: Point bias
    point_bias = calculate_point_bias(points)

    # Layer 4: Freq 10
    recent_10 = results[-10:]
    t_cnt = recent_10.count("TAI")
    x_cnt = recent_10.count("XIU")
    freq_bias = (t_cnt - x_cnt) * 3

    # Total score
    final_score = 50.0 + (markov_score * 0.4) + point_bias + freq_bias

    # Bắt cầu 1-1
    if (n >= 4
        and results[-1] != results[-2]
        and results[-2] != results[-3]
        and results[-3] != results[-4]):
        next_11 = "XIU" if last_res == "TAI" else "TAI"
        return (
            next_11,
            82.0,
            f"XÁC NHẬN CẦU 1-1 ({'-'.join(results[-4:])}) → Đảo sang {next_11}",
        )

    if final_score >= 50.0:
        suggestion = "TAI"
        conf = min(92.0, max(58.0, final_score))
    else:
        suggestion = "XIU"
        conf = min(92.0, max(58.0, 100.0 - final_score))

    reason = f"Mẫu hình {last_pat} (Score: {final_score:.1f})"
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
        f"📍 <b>TXMD5 — AI v14</b> ⚙️📈\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Phiên <b>#{session_id}</b>\n"
        f"📌 Đối soát: <b>{icon} {status}</b>\n"
        f"🎯 Đoán: <b>{predicted}</b> | Ra: <b>{actual}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📈 Tổng: <b>{win}W</b> / <b>{loss}L</b> — Win Rate: <b>{wr:.1f}%</b>"
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
                    # Chấm điểm dự đoán cũ
                    np = state["next_prediction"]
                    if np is not None and np.get("target_id") == current_id:
                        predicted = np["suggestion"]
                        correct = predicted == actual_result
                        if correct:
                            state["win_count"] += 1
                        else:
                            state["loss_count"] += 1
                        log_entry = f"#{current_id} | Đoán {predicted} | Ra {actual_result} → {'✅' if correct else '❌'}"
                        state["history_logs"].append(log_entry)
                        if len(state["history_logs"]) > 5:
                            state["history_logs"].pop(0)

                        # Gửi kết quả
                        if CHAT_ID:
                            msg = fmt_result(
                                current_id, actual_result, predicted, correct,
                                state["win_count"], state["loss_count"]
                            )
                            try:
                                bot.send_message(CHAT_ID, msg, parse_mode="HTML")
                            except Exception as e:
                                log.warning(f"send result: {e}")

                    # Dự đoán phiên mới
                    reversed_sessions = list(reversed(sessions))
                    suggestion, confidence, reason = ensemble_algorithm(reversed_sessions)
                    target_session_id = current_id + 1

                    state["next_prediction"] = {
                        "target_id": target_session_id,
                        "suggestion": suggestion,
                    }
                    state["last_session_id"] = current_id

                    # Gửi dự đoán
                    if CHAT_ID and confidence > 50.0:
                        msg = fmt_prediction(target_session_id, suggestion, confidence, reason)
                        try:
                            bot.send_message(CHAT_ID, msg, parse_mode="HTML")
                        except Exception as e:
                            log.warning(f"send prediction: {e}")

        except Exception as e:
            log.error(f"poll err: {e}")

        time.sleep(POLL_SEC)

# ==================== FLASK KEEP-ALIVE ====================
flask_app = Flask(__name__)
_start_time = time.time()

@flask_app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "service": "txmd5-bot-v14",
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
    kb.add(
        types.InlineKeyboardButton(toggle, callback_data="toggle"),
    )
    kb.add(
        types.InlineKeyboardButton("📊 Thống kê", callback_data="stats"),
        types.InlineKeyboardButton("📜 Lịch sử", callback_data="history"),
    )
    kb.add(types.InlineKeyboardButton("🔄 Reset điểm", callback_data="reset"))
    return kb

# ==================== COMMANDS ====================
def is_admin(uid):
    return uid == ADMIN_ID

@bot.message_handler(commands=["start", "menu"])
def cmd_start(m):
    bot.reply_to(
        m,
        "🤖 <b>TXMD5 Multi-Layer Bot v14</b>\n\n"
        "Bot tự động dự đoán Tài/Xỉu cho TXMD5.\n\n"
        "/stats — thống kê\n"
        "/predict — dự đoán ngay\n"
        "/chatid — lấy chat ID\n"
        "/toggle — bật/tắt auto\n"
        "/reset — reset điểm (admin)",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )

@bot.message_handler(commands=["chatid"])
def cmd_chatid(m):
    bot.reply_to(m, f"Chat ID: <code>{m.chat.id}</code>", parse_mode="HTML")

@bot.message_handler(commands=["stats"])
def cmd_stats(m):
    win = state["win_count"]
    loss = state["loss_count"]
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
        bot.reply_to(m, "⏳ Chưa có dữ liệu.")
        return
    sessions = data["list"]
    current_id = sessions[0].get("id")
    reversed_sessions = list(reversed(sessions))
    suggestion, confidence, reason = ensemble_algorithm(reversed_sessions)
    msg = fmt_prediction(current_id + 1, suggestion, confidence, reason)
    bot.reply_to(m, msg, parse_mode="HTML")

@bot.message_handler(commands=["toggle"])
def cmd_toggle(m):
    if not is_admin(m.from_user.id):
        bot.reply_to(m, "⛔ Admin only")
        return
    state["running"] = not state["running"]
    bot.reply_to(m, f"🔄 Auto: {'🟢 ON' if state['running'] else '🔴 OFF'}", reply_markup=main_keyboard())

@bot.message_handler(commands=["reset"])
def cmd_reset(m):
    if not is_admin(m.from_user.id):
        bot.reply_to(m, "⛔ Admin only")
        return
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
            bot.answer_callback_query(c.id, "⛔ Admin only")
            return
        state["running"] = not state["running"]
        bot.answer_callback_query(c.id, f"Auto: {'ON' if state['running'] else 'OFF'}")
        try:
            bot.edit_message_reply_markup(
                chat_id=c.message.chat.id,
                message_id=c.message.message_id,
                reply_markup=main_keyboard(),
            )
        except Exception:
            pass
    elif data == "stats":
        cmd_stats(c.message)
        bot.answer_callback_query(c.id)
    elif data == "history":
        logs = state["history_logs"]
        if not logs:
            bot.answer_callback_query(c.id, "Chưa có dữ liệu")
            return
        txt = "📜 <b>5 PHIÊN GẦN NHẤT</b>\n" + "\n".join(f"• {l}" for l in logs)
        bot.send_message(c.message.chat.id, txt, parse_mode="HTML")
        bot.answer_callback_query(c.id)
    elif data == "reset":
        if not is_admin(c.from_user.id):
            bot.answer_callback_query(c.id, "⛔ Admin only")
            return
        state["win_count"] = 0
        state["loss_count"] = 0
        state["history_logs"] = []
        state["next_prediction"] = None
        state["last_session_id"] = None
        bot.answer_callback_query(c.id, "✅ Đã reset")
        bot.send_message(c.message.chat.id, "✅ Đã reset điểm.")

# ==================== MAIN ====================
def main():
    if not BOT_TOKEN:
        log.error("Missing BOT_TOKEN")
        return
    log.info("Bot starting...")
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=poll_loop, daemon=True).start()
    bot.infinity_polling(timeout=30, long_polling_timeout=30)

if __name__ == "__main__":
    main()
