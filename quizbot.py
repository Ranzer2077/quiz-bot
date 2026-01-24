import logging
import csv
import os
import re
import threading
import random
import requests  # New library to read from GitHub
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

# REPLACE THIS WITH YOUR GITHUB RAW URL BASE
# Example: https://raw.githubusercontent.com/YourUsername/my-quiz-bot/main/
# For now, we will try to read local files first (which Render pulls from GitHub)
GITHUB_MODE = True 

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

def load_quiz(filename):
    """Loads quiz either from local folder or GitHub repo clone"""
    questions = []
    
    # Check 1: Is it a file we just uploaded to the bot?
    local_path = os.path.join(QUIZ_FOLDER, filename)
    if not local_path.endswith(".csv"): local_path += ".csv"
    
    # Check 2: Is it a file sitting in the root folder (from GitHub)?
    root_path = filename
    if not root_path.endswith(".csv"): root_path += ".csv"

    target_path = None
    if os.path.exists(local_path):
        target_path = local_path
    elif os.path.exists(root_path):
        target_path = root_path
    else:
        return None

    try:
        with open(target_path, 'r', encoding='utf-8') as f:
            first_line = f.readline()
            f.seek(0)
            delimiter = ';' if ';' in first_line and ',' not in first_line else ','
            reader = csv.reader(f, delimiter=delimiter)
            for row in reader:
                if not row or all(x.strip() == '' for x in row): continue
                if len(row) < 4: continue
                try:
                    original_correct_idx = int(row[-1])
                    original_options = row[1:-1]
                    if len(original_options) < 2: continue
                    
                    correct_text = original_options[original_correct_idx]
                    
                    # Shuffle Options logic
                    final_options = original_options[:]
                    random.shuffle(final_options)
                    new_correct_idx = final_options.index(correct_text)
                    
                    questions.append({
                        "question": row[0], 
                        "options": final_options, 
                        "correct_id": new_correct_idx,
                        "original_options": original_options, # Save specifically for retrying logic
                        "correct_text": correct_text          # Save text for retrying logic
                    })
                except ValueError: continue 
        
        random.shuffle(questions)
        return questions
    except Exception: return []

async def send_next_question(context, user_id):
    user_data = active_quizzes.get(user_id)
    if not user_data: return
    
    q_list = user_data["questions"]
    index = user_data["q_index"]

    if index >= len(q_list):
        await context.bot.send_message(user_id, f"🏁 **Quiz Completed!**\nFinal Score: {user_data['score']}")
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
    if text.startswith("/start_"): args = [text[7:]]

    if args:
        quiz_id = args[0]
        questions = load_quiz(quiz_id)
        if not questions:
            await update.message.reply_text("❌ Quiz not found.")
            return
        active_quizzes[update.effective_user.id] = {"quiz_id": quiz_id, "q_index": 0, "score": 0, "questions": questions}
        await update.message.reply_text(f"🚀 **Starting {len(questions)} Questions...**")
        await send_next_question(context, update.effective_user.id)
    else:
        await update.message.reply_text("👋 **Bot is Online!**\nSelect a quiz below.")

async def list_quizzes(update, context):
    # 1. Look in uploaded folder
    files = [f for f in os.listdir(QUIZ_FOLDER) if f.endswith('.csv')]
    
    # 2. Look in MAIN FOLDER (GitHub Files)
    root_files = [f for f in os.listdir('.') if f.endswith('.csv')]
    
    # Combine list
    all_files = list(set(files + root_files))

    if not all_files:
        await update.message.reply_text("📂 No quizzes found.")
        return

    await update.message.reply_text("📂 **Available Quizzes:**")
    for f in all_files:
        clean_name = f.replace('.csv', '')
        keyboard = [[InlineKeyboardButton("▶️ Play", callback_data=f"play_{clean_name}")]]
        # Only show delete button for Admin and only for local files (can't delete GitHub files from chat)
        if update.effective_user.id == ADMIN_ID and f in files:
            keyboard[0].append(InlineKeyboardButton("🗑️ Delete", callback_data=f"del_{clean_name}"))
            
        await update.message.reply_text(f"📄 **{clean_name}**", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_click(update, context):
    query = update.callback_query
    data = query.data
    user_id = update.effective_user.id
    
    if data.startswith("del_"):
        if user_id != ADMIN_ID:
            await query.answer("⛔ Admin Only!", show_alert=True)
            return
        filename = data[4:]
        path = os.path.join(QUIZ_FOLDER, filename + ".csv")
        if os.path.exists(path):
            os.remove(path)
            await query.edit_message_text(f"🗑️ **Deleted:** {filename}")
        else:
            await query.edit_message_text(f"❌ File missing (might be a GitHub file).")

    elif data.startswith("play_"):
        await query.answer()
        filename = data[5:]
        questions = load_quiz(filename)
        if questions:
            active_quizzes[user_id] = {"quiz_id": filename, "q_index": 0, "score": 0, "questions": questions}
            await query.message.reply_text(f"🚀 **Starting {len(questions)} Questions...**")
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
        await update.message.reply_text("⛔ Admin Access Required.")
        return
    doc = update.message.document
    if not doc.file_name.endswith('.csv'): return
    file = await context.bot.get_file(doc.file_id)
    safe_name = sanitize_filename(doc.file_name)
    save_path = os.path.join(QUIZ_FOLDER, safe_name)
    await file.download_to_drive(save_path)
    await update.message.reply_text("✅ **Saved!** (Temporary storage).")

async def handle_poll_answer(update, context):
    poll_data = context.bot_data.get(update.poll_answer.poll_id)
    if not poll_data: return
    user_id = poll_data["user_id"]
    user_data = active_quizzes.get(user_id)
    
    if user_data:
        chosen_option = update.poll_answer.option_ids[0]
        correct_option = poll_data["correct"]
        
        if chosen_option == correct_option:
            # CORRECT ANSWER
            user_data["score"] += 1
            user_data["q_index"] += 1
        else:
            # WRONG ANSWER -> REPEAT LOGIC
            # 1. Get the current question object
            current_q = user_data["questions"][user_data["q_index"]]
            
            # 2. Reshuffle options again for the next time it appears
            # We use the saved 'original_options' to ensure we have clean data
            retry_options = current_q["original_options"][:]
            random.shuffle(retry_options)
            new_correct_id = retry_options.index(current_q["correct_text"])
            
            # 3. Create a new question object
            retry_q = {
                "question": current_q["question"] + " (Retry 🔄)", # Mark it so they know
                "options": retry_options,
                "correct_id": new_correct_id,
                "original_options": current_q["original_options"],
                "correct_text": current_q["correct_text"]
            }
            
            # 4. Insert it 3 spots later in the queue (or at the end if fewer than 3 left)
            insert_pos = min(len(user_data["questions"]), user_data["q_index"] + 4)
            user_data["questions"].insert(insert_pos, retry_q)
            
            # 5. Send a small notification
            await context.bot.send_message(user_id, "❌ **Wrong!** I'll ask this again in a few turns.")
            
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
