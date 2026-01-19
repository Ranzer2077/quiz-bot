import logging
import csv
import os
import re
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, Poll, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    PollAnswerHandler,
    filters
)

# --- CONFIGURATION ---
TOKEN = "7880111023:AAHtsxHxQjUDL_j3jGMi-ph-RW0CI6rv7Ho"
ADMIN_ID = 947768900
QUIZ_FOLDER = "quizzes"
PORT = int(os.environ.get('PORT', 5000))

# --- WEB SERVER ---
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

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
            reader = csv.reader(f)
            for row in reader:
                if len(row) < 3: continue
                try:
                    correct_idx = int(row[-1])
                    options = row[1:-1]
                    q = {"question": row[0], "options": options, "correct_id": correct_idx}
                    questions.append(q)
                except ValueError: continue
        return questions
    except Exception: return []

async def send_next_question(context, user_id):
    user_data = active_quizzes.get(user_id)
    if not user_data: return
    
    q_list = user_data["questions"]
    index = user_data["q_index"]

    if index >= len(q_list):
        # Escaping Markdown disabled for safety
        await context.bot.send_message(user_id, f"🏁 Quiz Completed!\nScore: {user_data['score']}/{len(q_list)}")
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

# --- COMMAND HANDLERS ---

async def set_commands(context: ContextTypes.DEFAULT_TYPE):
    commands = [
        ("list", "Show available quizzes"),
        ("cancel", "Stop current quiz"),
        ("delete", "Delete a quiz file"),
        ("start", "Restart bot")
    ]
    await context.bot.set_my_commands(commands)

async def start(update, context):
    args = context.args
    user_id = update.effective_user.id
    text = update.message.text
    
    await set_commands(context) # Force menu refresh

    if text.startswith("/start_"):
        filename = text[7:] 
        args = [filename]

    if args:
        quiz_id = args[0]
        questions = load_quiz_from_file(quiz_id)
        if not questions:
            await update.message.reply_text("❌ Quiz not found. Please upload CSV again.")
            return
            
        active_quizzes[user_id] = {"quiz_id": quiz_id, "q_index": 0, "score": 0, "questions": questions}
        # Removed Markdown parsing here to prevent the "Byte Offset" error
        await update.message.reply_text(f"🚀 Starting: {quiz_id}")
        await send_next_question(context, user_id)
    else:
        await update.message.reply_text("👋 Bot Online!\nUpload a CSV to save a quiz.")

async def list_quizzes(update, context):
    files = [f for f in os.listdir(QUIZ_FOLDER) if f.endswith('.csv')]
    if not files:
        await update.message.reply_text("📂 No quizzes found.")
        return
        
    msg = "📂 Available Quizzes:\n\n"
    for f in files:
        clean_name = f.replace('.csv', '')
        # We manually construct the command link
        msg += f"• {clean_name} -> /start_{clean_name}\n"
        
    await update.message.reply_text(msg) # No markdown, safer

async def delete_quiz(update, context):
    if update.effective_user.id != ADMIN_ID: return
    
    if not context.args:
        await update.message.reply_text("⚠️ Usage: /delete <filename>\nExample: /delete my_quiz")
        return

    filename = context.args[0]
    path = os.path.join(QUIZ_FOLDER, filename + ".csv")
    
    if os.path.exists(path):
        os.remove(path)
        await update.message.reply_text(f"🗑️ Deleted: {filename}")
    else:
        await update.message.reply_text("❌ File not found.")

async def cancel_quiz(update, context):
    user_id = update.effective_user.id
    if user_id in active_quizzes:
        del active_quizzes[user_id]
        await update.message.reply_text("🛑 Quiz Cancelled.")
    else:
        await update.message.reply_text("You are not taking a quiz.")

async def handle_document(update, context):
    if update.effective_user.id != ADMIN_ID: return
    doc = update.message.document
    if not doc.file_name.endswith('.csv'): return

    file = await context.bot.get_file(doc.file_id)
    safe_name = sanitize_filename(doc.file_name)
    save_path = os.path.join(QUIZ_FOLDER, safe_name)
    
    await file.download_to_drive(save_path)
    
    quiz_id = safe_name.replace(".csv", "")
    # Removed Markdown parsing here to fix the crash
    await update.message.reply_text(f"✅ Saved!\nFilename: {safe_name}\nStart Link: /start_{quiz_id}")

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

# --- RUNNER ---
if __name__ == '__main__':
    threading.Thread(target=run_web_server, daemon=True).start()
    
    application = ApplicationBuilder().token(TOKEN).build()
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('list', list_quizzes))
    application.add_handler(CommandHandler('cancel', cancel_quiz))
    application.add_handler(CommandHandler('delete', delete_quiz)) # New Command
    
    application.add_handler(MessageHandler(filters.Regex(r'^/start_'), start))
    application.add_handler(MessageHandler(filters.Document.FileExtension("csv"), handle_document))
    application.add_handler(PollAnswerHandler(handle_poll_answer))
    
    print("Bot is running...")
    application.run_polling()
