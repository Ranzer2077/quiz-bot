import logging
import csv
import os
import re
import threading
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
                    correct_idx = int(row[-1])
                    options = row[1:-1]
                    if len(options) < 2: continue
                    questions.append({"question": row[0], "options": options, "correct_id": correct_idx})
                except ValueError: continue 
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

# --- KEYBOARD MENUS ---

async def show_main_menu(update, context):
    """Shows the persistent keyboard at the bottom"""
    keyboard = [["📂 My Quizzes", "❌ Stop Quiz"]]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="👇 **Menu**",
        reply_markup=markup
    )

async def set_bot_commands(context):
    """Sets the Blue Menu Button logic"""
    await context.bot.set_my_commands([
        ("list", "Show quizzes"),
        ("cancel", "Stop quiz"),
        ("start", "Restart bot")
    ])

# --- HANDLERS ---

async def start(update, context):
    await set_bot_commands(context)
    await show_main_menu(update, context) # Show the big buttons
    
    args = context.args
    text = update.message.text
    
    # Check for Deep Link (/start_filename)
    if text.startswith("/start_"):
        filename = text[7:]
        args = [filename]

    if args:
        quiz_id = args[0]
        questions = load_quiz_from_file(quiz_id)
        if not questions:
            await update.message.reply_text("❌ Quiz not found.")
            return
        active_quizzes[update.effective_user.id] = {"quiz_id": quiz_id, "q_index": 0, "score": 0, "questions": questions}
        await update.message.reply_text(f"🚀 **Starting {len(questions)} Questions...**")
        await send_next_question(context, update.effective_user.id)
    else:
        await update.message.reply_text("👋 **Bot is Online!**\nUpload a CSV to add a quiz.")

async def list_quizzes(update, context):
    files = [f for f in os.listdir(QUIZ_FOLDER) if f.endswith('.csv')]
    
    if not files:
        await update.message.reply_text("📂 No quizzes found.")
        return

    await update.message.reply_text("📂 **Your Quizzes:**")
    
    # Create a button row for EVERY file
    for f in files:
        clean_name = f.replace('.csv', '')
        
        # Buttons: [Play] [Delete]
        keyboard = [
            [
                InlineKeyboardButton("▶️ Play", callback_data=f"play_{clean_name}"),
                InlineKeyboardButton("🗑️ Delete", callback_data=f"del_{clean_name}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"📄 **{clean_name}**",
            reply_markup=reply_markup
        )

async def button_click(update, context):
    """Handles the Play and Delete buttons"""
    query = update.callback_query
    await query.answer() # Stop loading animation
    
    data = query.data
    
    if data.startswith("del_"):
        filename = data[4:] # Remove "del_"
        path = os.path.join(QUIZ_FOLDER, filename + ".csv")
        if os.path.exists(path):
            os.remove(path)
            await query.edit_message_text(f"🗑️ **Deleted:** {filename}")
        else:
            await query.edit_message_text(f"❌ File {filename} already deleted.")

    elif data.startswith("play_"):
        filename = data[5:] # Remove "play_"
        # Simulate /start command
        questions = load_quiz_from_file(filename)
        if questions:
            active_quizzes[update.effective_user.id] = {"quiz_id": filename, "q_index": 0, "score": 0, "questions": questions}
            await query.message.reply_text(f"🚀 **Starting {len(questions)} Questions...**")
            await send_next_question(context, update.effective_user.id)
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
    doc = update.message.document
    if not doc.file_name.endswith('.csv'): return

    file = await context.bot.get_file(doc.file_id)
    safe_name = sanitize_filename(doc.file_name)
    save_path = os.path.join(QUIZ_FOLDER, safe_name)
    await file.download_to_drive(save_path)
    
    await update.message.reply_text(f"✅ **Saved!**\nGo to '📂 My Quizzes' to play or delete.")

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
    
    # Commands
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('list', list_quizzes))
    application.add_handler(CommandHandler('cancel', cancel_quiz))
    
    # Text Triggers (For the persistent keyboard buttons)
    application.add_handler(MessageHandler(filters.Regex(r'📂 My Quizzes'), list_quizzes))
    application.add_handler(MessageHandler(filters.Regex(r'❌ Stop Quiz'), cancel_quiz))
    
    # Button Handler (For Play/Delete clicks)
    application.add_handler(CallbackQueryHandler(button_click))
    
    # File & Poll Handlers
    application.add_handler(MessageHandler(filters.Document.FileExtension("csv"), handle_document))
    application.add_handler(PollAnswerHandler(handle_poll_answer))
    
    print("Bot is running...")
    application.run_polling()
