import logging
import csv
import os
import re
import threading
import random  # <--- Essential for shuffling
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, Poll, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PollAnswerHandler,
    filters
)

# --- CONFIGURATION ---
TOKEN = "7880111023:AAHtsxHxQjUDL_j3jGMi-ph-RW0CI6rv7Ho"
ADMIN_ID = 947768900
QUIZ_FOLDER = "quizzes"
PORT = int(os.environ.get('PORT', 5000))

# --- WEB SERVER (Keeps Bot Alive) ---
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.wfile.write(b"Bot is running!")
    def do_HEAD(self):
        self.send_response(200)

def run_web_server():
    server = HTTPServer(('0.0.0.0', PORT), SimpleHandler)
    server.serve_forever()

# --- BOT SETUP ---
logging.basicConfig(level=logging.INFO)
if not os.path.exists(QUIZ_FOLDER):
    os.makedirs(QUIZ_FOLDER)

active_quizzes = {}

# --- HELPER FUNCTIONS ---
def sanitize_filename(filename):
    name, ext = os.path.splitext(filename)
    clean_name = re.sub(r'[^a-zA-Z0-9]', '_', name)
    clean_name = re.sub(r'_+', '_', clean_name).lower()
    return f"{clean_name}{ext}"

def load_quiz_from_file(filename):
    questions = []
    path = os.path.join(QUIZ_FOLDER, filename)
    if not path.endswith(".csv"): path += ".csv"
    if not os.path.exists(path): return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            first_line = f.readline()
            f.seek(0)
            delimiter = ';' if ';' in first_line and ',' not in first_line else ','
            reader = csv.reader(f, delimiter=delimiter)
            for row in reader:
                if not row or all(x.strip() == '' for x in row): continue
                if len(row) < 4: continue
                try:
                    # Logic: We must shuffle options AND track where the correct answer went
                    original_correct_idx = int(row[-1])
                    original_options = row[1:-1]
                    
                    if len(original_options) < 2: continue
                    
                    # 1. Identify the text of the correct answer
                    correct_text = original_options[original_correct_idx]
                    
                    # 2. Shuffle the options list
                    final_options = original_options[:]
                    random.shuffle(final_options)
                    
                    # 3. Find the new index of the correct answer
                    new_correct_idx = final_options.index(correct_text)
                    
                    questions.append({
                        "question": row[0], 
                        "options": final_options, 
                        "correct_id": new_correct_idx
                    })
                except ValueError: continue 
        
        # 4. Shuffle the order of questions
        random.shuffle(questions)
        return questions
    except Exception: return []

async def send_next_question(context, user_id):
    user_data = active_quizzes.get(user_id)
    if not user_data: return
    q_list = user_data["questions"]
    index = user_data["q_index"]

    if index >= len(q_list):
        await context.bot.send_message(user_id, f"🏁 **Quiz Completed!**\nScore: {user_data['score']}/{len(q_list)}")
        del active_quizzes[user_id] 
        return

    q = q_list[index]
    try:
        message = await context.bot.send_poll(
            chat_id=user_id,
            question=f"[{index + 1}/{len(q_list)}] {q['question']}",
            options=q['options'],
            type=Poll.QUIZ,
            correct_option_id=q['correct_id'],
            is_anonymous=False
        )
        context.bot_data[message.poll.id] = {"user_id": user_id, "correct": q['correct_id']}
    except Exception:
        user_data["q_index"] += 1
        await send_next_question(context, user_id)

# --- MENUS ---
async def show_main_menu(update, context):
    keyboard = [["📂 My Quizzes", "❌ Stop Quiz"]]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    await context.bot.send_message(chat_id=update.effective_chat.id, text="👇 **Menu**", reply_markup=markup)

# --- HANDLERS ---
async def start(update, context):
    await show_main_menu(update, context)
    
    args = context.args
    text = update.message.text
    if text.startswith("/start_"):
        args = [text[7:]]

    if args:
        quiz_id = args[0]
        questions = load_quiz_from_file(quiz_id)
        if not questions:
            await update.message.reply_text("❌ Quiz not found.")
            return
        active_quizzes[update.effective_user.id] = {"quiz_id": quiz_id, "q_index": 0, "score": 0, "questions": questions}
        await update.message.reply_text(f"🚀 **Starting {len(questions)} Questions (Randomized)...**")
        await send_next_question(context, update.effective_user.id)
    else:
        await update.message.reply_text("👋 **Bot is Online!**\n\nSelect an option below.")

async def list_quizzes(update, context):
    files = [f for f in os.listdir(QUIZ_FOLDER) if f.endswith('.csv')]
    if not files:
        await update.message.reply_text("📂 No quizzes found.")
        return

    await update.message.reply_text("📂 **Your Quizzes:**")
    for f in files:
        clean_name = f.replace('.csv', '')
        keyboard = [[InlineKeyboardButton("▶️ Play", callback_data=f"play_{clean_name}"), InlineKeyboardButton("🗑️ Delete", callback_data=f"del_{clean_name}")]]
        await update.message.reply_text(f"📄 **{clean_name}**", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_click(update, context):
    query = update.callback_query
    data = query.data
    user_id = update.effective_user.id
    
    if data.startswith("del_"):
        if user_id != ADMIN_ID:
            await query.answer("⛔ Security: Only the Admin can delete quizzes!", show_alert=True)
            return
        
        await query.answer()
        filename = data[4:]
        path = os.path.join(QUIZ_FOLDER, filename + ".csv")
        if os.path.exists(path):
            os.remove(path)
            await query.edit_message_text(f"🗑️ **Deleted:** {filename}")
        else:
            await query.edit_message_text(f"❌ File missing.")

    elif data.startswith("play_"):
        await query.answer()
        filename = data[5:]
        questions = load_quiz_from_file(filename)
        if questions:
            active_quizzes[user_id] = {"quiz_id": filename, "q_index": 0, "score": 0, "questions": questions}
            await query.message.reply_text(f"🚀 **Starting {len(questions)} Questions (Randomized)...**")
            await send_next_question(context, user_id)
        else:
            await query.message.reply_text("❌ Error loading quiz.")

async def cancel_quiz(update, context):
    user_id = update.effective_user.id
    if user_id in active_quizzes:
        del active_quizzes[user_id]
        await update.message.reply_text("🛑 **Quiz Stopped.**")
    else:
        await update.message.reply_text("No active quiz.")

async def handle_document(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ **Admin Access Required** to upload files.")
        return

    doc = update.message.document
    if not doc.file_name.endswith('.csv'): return
    file = await context.bot.get_file(doc.file_id)
    safe_name = sanitize_filename(doc.file_name)
    save_path = os.path.join(QUIZ_FOLDER, safe_name)
    await file.download_to_drive(save_path)
    await update.message.reply_text("✅ **Saved!**\nCheck '📂 My Quizzes' to play.")

async def handle_poll_answer(update, context):
    poll_data = context.bot_data.get(update.poll_answer.poll_id)
    if not poll_data: return
    user_id = poll_data["user_id"]
    user_data = active_quizzes.get(user_id)
    if user_data:
        if update.poll_answer.option_ids[0] == poll_data["correct"]:
            user_data["score"] += 1
        user_data["q_index"] += 1
        await send_next_question(context, user_id)

if __name__ == '__main__':
    threading.Thread(target=run_web_server, daemon=True).start()
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('list', list_quizzes))
    app.add_handler(CommandHandler('cancel', cancel_quiz))
    app.add_handler(MessageHandler(filters.Regex(r'📂 My Quizzes'), list_quizzes))
    app.add_handler(MessageHandler(filters.Regex(r'❌ Stop Quiz'), cancel_quiz))
    app.add_handler(CallbackQueryHandler(button_click))
    app.add_handler(MessageHandler(filters.Document.FileExtension("csv"), handle_document))
    app.add_handler(PollAnswerHandler(handle_poll_answer))
    
    print("Bot is running...")
    app.run_polling()
