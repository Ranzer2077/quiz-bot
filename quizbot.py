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
# ADMIN_ID check is REMOVED for debugging
QUIZ_FOLDER = "quizzes"
PORT = int(os.environ.get('PORT', 5000))

# --- WEB SERVER ---
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
    """Force filenames to be simple: quiz1.csv"""
    name, ext = os.path.splitext(filename)
    # Replace anything that isn't a letter or number with _
    clean_name = re.sub(r'[^a-zA-Z0-9]', '_', name)
    clean_name = re.sub(r'_+', '_', clean_name).lower()
    return f"{clean_name}{ext}"

def load_quiz_from_file(filename):
    questions = []
    path = os.path.join(QUIZ_FOLDER, filename)
    if not path.endswith(".csv"): path += ".csv"
    
    if not os.path.exists(path):
        return None
    
    try:
        with open(path, 'r', encoding='utf-8') as f:
            # Auto-detect separator
            first_line = f.readline()
            f.seek(0)
            delimiter = ';' if ';' in first_line and ',' not in first_line else ','
            
            reader = csv.reader(f, delimiter=delimiter)
            
            for row in reader:
                # Skip empty rows
                if not row or all(x.strip() == '' for x in row): continue
                
                # Check column count (Need Question + 2 Options + Index)
                if len(row) < 4: continue
                
                try:
                    correct_idx = int(row[-1])
                    options = row[1:-1]
                    # Simple validation
                    if len(options) < 2: continue
                    
                    q = {"question": row[0], "options": options, "correct_id": correct_idx}
                    questions.append(q)
                except ValueError:
                    continue 
        return questions
    except Exception as e:
        print(f"Error reading CSV: {e}")
        return []

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
        
    except Exception as e:
        # If error (e.g. Question too long), tell user and skip
        await context.bot.send_message(user_id, f"⚠️ Error on Q{index+1}: {e}\nSkipping...")
        user_data["q_index"] += 1
        await send_next_question(context, user_id)

# --- COMMAND HANDLERS ---

async def set_commands(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.set_my_commands([
        ("list", "Show quizzes"), 
        ("cancel", "Stop quiz"), 
        ("delete", "Delete file")
    ])

async def start(update, context):
    args = context.args
    user_id = update.effective_user.id
    text = update.message.text
    
    # 1. Force Menu Button
    await set_commands(context)

    # 2. Check for Deep Link (e.g. /start_myquiz)
    # The Regex filter catches these, but we double check here
    if text and text.startswith("/start_"):
        filename = text[7:] # Remove "/start_"
        args = [filename]

    if args:
        quiz_id = args[0]
        # Try to load
        questions = load_quiz_from_file(quiz_id)
        
        if not questions:
            await update.message.reply_text(f"❌ Could not load '{quiz_id}'.\nFile might be empty or missing.")
            return
            
        active_quizzes[user_id] = {"quiz_id": quiz_id, "q_index": 0, "score": 0, "questions": questions}
        await update.message.reply_text(f"🚀 **Starting {len(questions)} Questions...**")
        await send_next_question(context, user_id)
    else:
        await update.message.reply_text("👋 **Bot is Online!**\n\nUpload a .csv file now.")

async def list_quizzes(update, context):
    files = [f for f in os.listdir(QUIZ_FOLDER) if f.endswith('.csv')]
    if not files:
        await update.message.reply_text("📂 No quizzes found.\nUpload a CSV file first.")
        return
        
    msg = "📂 **Available Quizzes:**\n\n"
    for f in files:
        clean_name = f.replace('.csv', '')
        # Link format: /start_filename
        msg += f"• {clean_name} → /start_{clean_name}\n"
        
    await update.message.reply_text(msg)

async def handle_document(update, context):
    # REMOVED ADMIN CHECK for debugging
    doc = update.message.document
    if not doc.file_name.endswith('.csv'):
        await update.message.reply_text("❌ Not a CSV file.")
        return

    # 1. Tell user we got it
    status_msg = await update.message.reply_text("⏳ processing file...")

    try:
        file = await context.bot.get_file(doc.file_id)
        safe_name = sanitize_filename(doc.file_name)
        save_path = os.path.join(QUIZ_FOLDER, safe_name)
        
        await file.download_to_drive(save_path)
        
        # 2. Test read the file immediately
        questions = load_quiz_from_file(safe_name)
        count = len(questions) if questions else 0
        
        quiz_id = safe_name.replace(".csv", "")
        
        if count > 0:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=status_msg.message_id,
                text=f"✅ **Saved!** ({count} questions)\n\nTap to play: /start_{quiz_id}"
            )
        else:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=status_msg.message_id,
                text=f"⚠️ **Saved, but found 0 questions.**\nCheck your CSV format.\nTry: /delete {quiz_id}"
            )
            
    except Exception as e:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=status_msg.message_id,
            text=f"❌ Error: {e}"
        )

async def delete_quiz(update, context):
    if not context.args: return
    filename = context.args[0]
    path = os.path.join(QUIZ_FOLDER, filename + ".csv")
    if os.path.exists(path):
        os.remove(path)
        await update.message.reply_text(f"🗑️ Deleted {filename}")
    else:
        await update.message.reply_text("❌ File not found.")

async def cancel_quiz(update, context):
    if update.effective_user.id in active_quizzes:
        del active_quizzes[update.effective_user.id]
        await update.message.reply_text("🛑 Cancelled.")

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
    
    # Handlers
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('list', list_quizzes))
    application.add_handler(CommandHandler('cancel', cancel_quiz))
    application.add_handler(CommandHandler('delete', delete_quiz))
    
    # Catch /start_filename
    application.add_handler(MessageHandler(filters.Regex(r'^/start_'), start))
    
    # Documents
    application.add_handler(MessageHandler(filters.Document.FileExtension("csv"), handle_document))
    
    # Polls
    application.add_handler(PollAnswerHandler(handle_poll_answer))
    
    print("Bot is running...")
    application.run_polling()
