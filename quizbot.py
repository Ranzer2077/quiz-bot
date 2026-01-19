import logging
import csv
import os
import re
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, Poll
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
            # 1. Read first line to detect separator (; or ,)
            first_line = f.readline()
            f.seek(0) # Go back to start
            
            delimiter = ';' if ';' in first_line and ',' not in first_line else ','
            
            reader = csv.reader(f, delimiter=delimiter)
            row_num = 0
            for row in reader:
                row_num += 1
                # Remove empty strings from end of row (Excel artifact)
                row = [x for x in row if x.strip()]
                
                # We need at least: Question, Option1, Option2, Index
                if len(row) < 4: 
                    print(f"Skipping Row {row_num}: Not enough columns {row}")
                    continue
                
                try:
                    correct_idx = int(row[-1])
                    options = row[1:-1]
                    
                    # Validate Telegram Limits
                    if len(options) < 2:
                        print(f"Row {row_num}: Need at least 2 options.")
                        continue
                    if len(options) > 10:
                        print(f"Row {row_num}: Too many options.")
                        continue
                        
                    q = {"question": row[0], "options": options, "correct_id": correct_idx}
                    questions.append(q)
                except ValueError:
                    continue # Header row or bad index
        return questions
    except Exception as e:
        print(f"File Error: {e}")
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
    
    # --- DIAGNOSTIC MODE: REPORT ERRORS TO USER ---
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
        # If sending fails, TELL THE USER WHY
        error_msg = str(e)
        await context.bot.send_message(user_id, f"⚠️ **Error sending Question {index+1}:**\n`{error_msg}`\n\nSkipping to next...", parse_mode='Markdown')
        
        # Skip to next question automatically
        user_data["q_index"] += 1
        await send_next_question(context, user_id)

# --- COMMAND HANDLERS ---
async def set_commands(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.set_my_commands([
        ("list", "Show quizzes"), ("cancel", "Stop quiz"), ("delete", "Delete file")
    ])

async def start(update, context):
    args = context.args
    user_id = update.effective_user.id
    text = update.message.text
    
    await set_commands(context) 

    if text.startswith("/start_"):
        filename = text[7:] 
        args = [filename]

    if args:
        quiz_id = args[0]
        questions = load_quiz_from_file(quiz_id)
        
        if not questions:
            await update.message.reply_text("❌ **Error:** Found 0 valid questions in file.\nCheck if your CSV uses commas or if columns are empty.")
            return
            
        active_quizzes[user_id] = {"quiz_id": quiz_id, "q_index": 0, "score": 0, "questions": questions}
        await update.message.reply_text(f"🚀 **Starting {len(questions)} Questions...**", parse_mode='Markdown')
        await send_next_question(context, user_id)
    else:
        await update.message.reply_text("👋 **Bot Online!** Upload a CSV.")

async def list_quizzes(update, context):
    files = [f for f in os.listdir(QUIZ_FOLDER) if f.endswith('.csv')]
    if not files:
        await update.message.reply_text("📂 No quizzes found.")
        return
    msg = "📂 **Available Quizzes:**\n\n"
    for f in files:
        clean_name = f.replace('.csv', '')
        msg += f"• {clean_name} -> /start_{clean_name}\n"
    await update.message.reply_text(msg)

async def delete_quiz(update, context):
    if update.effective_user.id != ADMIN_ID: return
    if not context.args:
        await update.message.reply_text("Usage: /delete <filename>")
        return
    filename = context.args[0]
    path = os.path.join(QUIZ_FOLDER, filename + ".csv")
    if os.path.exists(path):
        os.remove(path)
        await update.message.reply_text(f"🗑️ Deleted: {filename}")
    else:
        await update.message.reply_text("❌ File not found.")

async def cancel_quiz(update, context):
    if update.effective_user.id in active_quizzes:
        del active_quizzes[update.effective_user.id]
        await update.message.reply_text("🛑 Cancelled.")
    else:
        await update.message.reply_text("No active quiz.")

async def handle_document(update, context):
    if update.effective_user.id != ADMIN_ID: return
    doc = update.message.document
    if not doc.file_name.endswith('.csv'): return

    file = await context.bot.get_file(doc.file_id)
    safe_name = sanitize_filename(doc.file_name)
    save_path = os.path.join(QUIZ_FOLDER, safe_name)
    await file.download_to_drive(save_path)
    
    quiz_id = safe_name.replace(".csv", "")
    await update.message.reply_text(f"✅ Saved!\nStart: /start_{quiz_id}")

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
    app.add_handler(CommandHandler('delete', delete_quiz))
    app.add_handler(MessageHandler(filters.Regex(r'^/start_'), start))
    app.add_handler(MessageHandler(filters.Document.FileExtension("csv"), handle_document))
    app.add_handler(PollAnswerHandler(handle_poll_answer))
    print("Bot is running...")
    app.run_polling()
