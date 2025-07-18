# --- START OF FILE OmegleBot [v5.3_EN].py ---

import logging
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler
from datetime import datetime

# --- Configuration ---
BOT_TOKEN = "YOUR_TOKEN_HERE"  # IMPORTANT: Paste your bot token here
BOT_OWNER_ID = 123456789      # Replace with the bot owner's Telegram user ID
ADMIN_GROUP_ID = -1001234567890 # Replace with the admin group chat ID

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# --- Database ---
DB_FILE = "omegle_bot.db"

def get_db_connection():
    """Creates and returns a database connection."""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def setup_database(conn: sqlite3.Connection):
    """Creates tables if they don't exist. Updates the schema if needed."""
    with conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS chat_pairs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user1_id INTEGER NOT NULL,
                user2_id INTEGER NOT NULL,
                connected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                disconnected_at TIMESTAMP
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pair_id INTEGER NOT NULL,
                sender_id INTEGER NOT NULL,
                message_text TEXT,
                media_type TEXT,
                media_id TEXT,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (pair_id) REFERENCES chat_pairs(id)
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id INTEGER PRIMARY KEY,
                reason TEXT,
                banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                banned_by_admin_id INTEGER
            )
        ''')
        # Attempt to add the new column to an existing table if it's missing
        try:
            conn.execute("ALTER TABLE banned_users ADD COLUMN banned_by_admin_id INTEGER;")
            logger.info("Column 'banned_by_admin_id' added to 'banned_users' table.")
        except sqlite3.OperationalError:
            # Column already exists, which is fine
            pass
            
        conn.execute('''
            CREATE TABLE IF NOT EXISTS sudo_users (
                user_id INTEGER PRIMARY KEY,
                username TEXT
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reporter_id INTEGER,
                reported_id INTEGER,
                reason TEXT,
                reported_message_text TEXT,
                reported_media_id TEXT,
                reported_media_type TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'pending' -- pending, accepted, rejected
            )
        ''')
    logger.info("Database setup complete.")

# --- In-memory state (for waiting users only) ---
waiting_users = []

# --- Helper Functions ---
async def is_sudo_user(user_id: int) -> bool:
    """Checks if a user is an admin."""
    with get_db_connection() as conn:
        cursor = conn.execute("SELECT 1 FROM sudo_users WHERE user_id = ?", (user_id,))
        return cursor.fetchone() is not None

async def get_ban_info(user_id: int) -> tuple | None:
    """Checks if a user is banned and returns their ban details."""
    with get_db_connection() as conn:
        cursor = conn.execute(
            "SELECT reason, banned_at, banned_by_admin_id FROM banned_users WHERE user_id = ?",
            (user_id,)
        )
        return cursor.fetchone()

async def get_active_partner(user_id: int) -> int | None:
    """Gets the active partner's ID from the database."""
    with get_db_connection() as conn:
        cursor = conn.execute(
            "SELECT user1_id, user2_id FROM chat_pairs WHERE (user1_id = ? OR user2_id = ?) AND disconnected_at IS NULL",
            (user_id, user_id)
        )
        pair = cursor.fetchone()
        if pair:
            return pair['user2_id'] if pair['user1_id'] == user_id else pair['user1_id']
    return None

async def disconnect_user(user_id: int, partner_id: int, conn: sqlite3.Connection):
    """Handles the logic of disconnecting a pair in the database."""
    conn.execute(
        "UPDATE chat_pairs SET disconnected_at = CURRENT_TIMESTAMP WHERE ((user1_id = ? AND user2_id = ?) OR (user1_id = ? AND user2_id = ?)) AND disconnected_at IS NULL",
        (user_id, partner_id, partner_id, user_id)
    )

# --- User Commands ---
async def start(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    with get_db_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
            (user.id, user.username)
        )
    await update.message.reply_text(
        f"Welcome to *OmegleBot*, {user.first_name}!\n\n"
        "This bot lets you have anonymous chats with random users.\n\n"
        "Use /connect to find a chat partner.\n"
        "Use /reconnect to quickly find a new person.\n\n"
        "For a full list of commands, use /help.",
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: CallbackContext) -> None:
    is_admin = await is_sudo_user(update.effective_user.id) or update.effective_user.id == BOT_OWNER_ID
    
    user_text = (
        "Here are the available commands:\n"
        "/start - Start the bot and get to know it\n"
        "/connect - Find a random chat partner\n"
        "/disconnect - End your current chat\n"
        "/reconnect - End the current chat and find a new one\n"
        "/report - Report a user (use by replying to their message)\n"
        "/rules - Read the chat rules"
    )
    
    admin_text = (
        "\n\n*Admin Commands:*\n"
        "`/addsudo <user_id> <username>` - Add an admin\n"
        "`/delsudo <user_id>` - Remove an admin\n"
        "`/ban <user_id> <reason>` - Ban a user\n"
        "`/unban <user_id>` - Unban a user\n"
        "`/checkban <user_id>` - Check a user's ban status"
    )
    
    full_text = user_text
    if is_admin:
        full_text += admin_text
        
    await update.message.reply_text(full_text, parse_mode='Markdown')

async def rules(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "OmegleBot Rules:\n"
        "1. Be respectful and kind to others.\n"
        "2. Do not share personal information or ask for it.\n"
        "3. Adult content, illegal activities, and spam are strictly forbidden.\n"
        "4. Follow Telegram's Terms of Service.\n\n"
        "Breaking the rules will result in a permanent ban."
    )

async def connect(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id

    if await get_ban_info(user_id):
        await update.message.reply_text("You are permanently banned and cannot use this bot.")
        return

    if await get_active_partner(user_id):
        await update.message.reply_text("You are already in a chat. Use /disconnect or /reconnect.")
        return

    if user_id in waiting_users:
        await update.message.reply_text("You are already waiting for a partner. Please be patient.")
        return

    if waiting_users:
        partner_id = waiting_users.pop(0)
        
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO chat_pairs (user1_id, user2_id) VALUES (?, ?)",
                (user_id, partner_id)
            )

        await context.bot.send_message(user_id, "✅ Partner found! Enjoy your chat.\nUse /disconnect to end it, or /reconnect to find someone new.")
        await context.bot.send_message(partner_id, "✅ Partner found! Enjoy your chat.\nUse /disconnect to end it, or /reconnect to find someone new.")
        logger.info(f"Paired {user_id} with {partner_id}")
    else:
        waiting_users.append(user_id)
        await update.message.reply_text("⏳ Searching for a partner... Please wait.")
        logger.info(f"User {user_id} is now waiting.")
        
async def disconnect(update: Update, context: CallbackContext, silent: bool = False) -> None:
    """Disconnects the user. silent=True avoids sending messages (used by /reconnect)."""
    user_id = update.effective_user.id
    partner_id = await get_active_partner(user_id)

    if not partner_id:
        if user_id in waiting_users:
            waiting_users.remove(user_id)
            if not silent:
                await update.message.reply_text("Stopped searching for a partner.")
        elif not silent:
            await update.message.reply_text("You are not in any chat.")
        return

    with get_db_connection() as conn:
        await disconnect_user(user_id, partner_id, conn)

    if not silent:
        await context.bot.send_message(user_id, "You have been disconnected.")
    await context.bot.send_message(partner_id, "Your partner has disconnected.")
    logger.info(f"User {user_id} disconnected from {partner_id}")

async def reconnect(update: Update, context: CallbackContext) -> None:
    """Disconnects and immediately searches for a new partner."""
    user_id = update.effective_user.id
    
    if await get_ban_info(user_id):
        await update.message.reply_text("You are permanently banned and cannot use this bot.")
        return

    await update.message.reply_text("Ending the current chat and finding a new one...")
    
    # Silently disconnect
    await disconnect(update, context, silent=True)
    
    # Immediately connect
    await connect(update, context)

async def report(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    
    if not update.message.reply_to_message:
        await update.message.reply_text("To report someone, reply to their message with the command /report <reason>.")
        return
        
    partner_id = await get_active_partner(user.id)
    if not partner_id:
        await update.message.reply_text("You are not in a chat.")
        return

    reason = ' '.join(context.args)
    if not reason:
        await update.message.reply_text("You must provide a reason for the report. Usage: /report <reason>")
        return

    reported_msg = update.message.reply_to_message
    
    if not reported_msg.from_user.is_bot:
        await update.message.reply_text("You can only report messages received from your partner through the bot.")
        return

    text, media_id, media_type = (reported_msg.text or reported_msg.caption), None, None
    if reported_msg.photo:
        media_id = reported_msg.photo[-1].file_id
        media_type = 'photo'
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO reports (reporter_id, reported_id, reason, reported_message_text, reported_media_id, reported_media_type)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user.id, partner_id, reason, text, media_id, media_type)
        )
        report_id = cursor.lastrowid
        conn.commit()

    keyboard = [
        [InlineKeyboardButton("✅ Accept (Ban)", callback_data=f"accept_report_{report_id}"),
         InlineKeyboardButton("❌ Reject", callback_data=f"reject_report_{report_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    reporter_username = f"@{user.username}" if user.username else "N/A"
    report_message = (
        f"🚨 *New Report #{report_id}*\n\n"
        f"👤 *Reporter:*\n"
        f"   ID: `{user.id}`\n"
        f"   User: {reporter_username}\n\n"
        f"🎯 *Reported User:*\n"
        f"   ID: `{partner_id}`\n\n"
        f"📝 *Reason:*\n"
        f"   `{reason}`\n\n"
        f"👇 *Reported message is below* 👇"
    )
    
    await context.bot.send_message(ADMIN_GROUP_ID, report_message, reply_markup=reply_markup, parse_mode='Markdown')
    await context.bot.forward_message(ADMIN_GROUP_ID, from_chat_id=user.id, message_id=reported_msg.message_id)

    await update.message.reply_text(f"Report #{report_id} has been submitted. Thank you.")
    logger.info(f"Report {report_id} submitted by {user.id} against {partner_id}")


async def message_handler(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    partner_id = await get_active_partner(user_id)

    if not partner_id:
        await update.message.reply_text("You are not in a chat. Use /connect to find a partner.")
        return
        
    message = update.message
    text, media_id, media_type = None, None, None

    try:
        if message.text:
            text = message.text
            await context.bot.send_message(partner_id, text)
        elif message.photo:
            media_id = message.photo[-1].file_id
            media_type = 'photo'
            await context.bot.send_photo(partner_id, media_id, caption=message.caption)
        elif message.video:
            media_id = message.video.file_id
            media_type = 'video'
            await context.bot.send_video(partner_id, media_id, caption=message.caption)
        elif message.animation:
            media_id = message.animation.file_id
            media_type = 'animation'
            await context.bot.send_animation(partner_id, media_id, caption=message.caption)
        elif message.sticker:
            media_id = message.sticker.file_id
            media_type = 'sticker'
            await context.bot.send_sticker(partner_id, media_id)
        else:
            return
    except Exception as e:
        logger.error(f"Failed to forward message from {user_id} to {partner_id}: {e}")
        await update.message.reply_text("An error occurred while sending your message. Please try again.")
        return

    with get_db_connection() as conn:
        cursor = conn.execute(
            "SELECT id FROM chat_pairs WHERE ((user1_id = ? AND user2_id = ?) OR (user1_id = ? AND user2_id = ?)) AND disconnected_at IS NULL",
            (user_id, partner_id, partner_id, user_id)
        )
        pair_data = cursor.fetchone()
        if pair_data:
            conn.execute(
                "INSERT INTO messages (pair_id, sender_id, message_text, media_type, media_id) VALUES (?, ?, ?, ?, ?)",
                (pair_data['id'], user_id, text or message.caption, media_type, media_id)
            )

# --- Admin Handlers and Commands ---

async def handle_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()

    admin_user = query.from_user
    data = query.data.split('_')
    action, subject, item_id = data[0], data[1], int(data[2])

    if not await is_sudo_user(admin_user.id) and admin_user.id != BOT_OWNER_ID:
        await query.edit_message_text(text="Error: You do not have permission to perform this action.")
        return

    with get_db_connection() as conn:
        if subject == "report":
            report_data = conn.execute("SELECT * FROM reports WHERE id = ?", (item_id,)).fetchone()
            if not report_data:
                await query.edit_message_text(text=f"Error: Report #{item_id} not found.")
                return
            
            reporter_id, reported_id = report_data['reporter_id'], report_data['reported_id']

            if action == 'accept':
                ban_reason = f"Report #{item_id} ({report_data['reason']})"
                # Ban the user, logging who did it
                conn.execute(
                    "INSERT OR REPLACE INTO banned_users (user_id, reason, banned_by_admin_id) VALUES (?, ?, ?)",
                    (reported_id, ban_reason, admin_user.id)
                )
                conn.execute("UPDATE reports SET status = 'accepted' WHERE id = ?", (item_id,))
                
                active_partner = await get_active_partner(reported_id)
                if active_partner == reporter_id:
                    await disconnect_user(reported_id, reporter_id, conn)
                    await context.bot.send_message(reporter_id, "Your report was accepted. The chat has been terminated.")

                await query.edit_message_text(text=f"✅ Report #{item_id} accepted by {admin_user.mention_markdown()}. User `{reported_id}` has been banned.", parse_mode='Markdown')
                await context.bot.send_message(reported_id, f"You have been permanently banned due to an accepted report.\nReason: {ban_reason}")
            
            elif action == 'reject':
                conn.execute("UPDATE reports SET status = 'rejected' WHERE id = ?", (item_id,))
                await query.edit_message_text(text=f"❌ Report #{item_id} rejected by {admin_user.mention_markdown()}.", parse_mode='Markdown')
                await context.bot.send_message(reporter_id, f"Your report #{item_id} has been rejected by the administration.")

async def add_sudo(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != BOT_OWNER_ID:
        await update.message.reply_text('Permission denied.')
        return
    try:
        user_id = int(context.args[0])
        username = context.args[1]
    except (IndexError, ValueError):
        await update.message.reply_text('Usage: /addsudo <user_id> <username>')
        return
    with get_db_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO sudo_users (user_id, username) VALUES (?, ?)", (user_id, username))
    await update.message.reply_text(f'User {username} ({user_id}) has been added as an admin.')

async def del_sudo(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != BOT_OWNER_ID:
        await update.message.reply_text('Permission denied.')
        return
    try:
        user_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text('Usage: /delsudo <user_id>')
        return
    with get_db_connection() as conn:
        conn.execute("DELETE FROM sudo_users WHERE user_id = ?", (user_id,))
    await update.message.reply_text(f'User {user_id} has been removed from the admin list.')
    
async def ban_user(update: Update, context: CallbackContext) -> None:
    admin_id = update.effective_user.id
    if not await is_sudo_user(admin_id) and admin_id != BOT_OWNER_ID:
        await update.message.reply_text('Permission denied.')
        return
    try:
        target_id = int(context.args[0])
        reason = ' '.join(context.args[1:]) if len(context.args) > 1 else "Manual ban by an administrator"
    except (IndexError, ValueError):
        await update.message.reply_text('Usage: /ban <user_id> <reason>')
        return

    if target_id == BOT_OWNER_ID or await is_sudo_user(target_id):
        await update.message.reply_text('You cannot ban the owner or another admin.')
        return
    
    with get_db_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO banned_users (user_id, reason, banned_by_admin_id) VALUES (?, ?, ?)", (target_id, reason, admin_id))
        
        partner_id = await get_active_partner(target_id)
        if partner_id:
            await disconnect_user(target_id, partner_id, conn)
            await context.bot.send_message(partner_id, "Your partner has been banned by an admin. The chat has been terminated.")

    await update.message.reply_text(f'User {target_id} has been banned. Reason: {reason}')
    await context.bot.send_message(target_id, f'You have been banned by an administrator. Reason: {reason}')

async def unban_user(update: Update, context: CallbackContext) -> None:
    admin_id = update.effective_user.id
    if not await is_sudo_user(admin_id) and admin_id != BOT_OWNER_ID:
        await update.message.reply_text('Permission denied.')
        return
    try:
        target_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text('Usage: /unban <user_id>')
        return
        
    with get_db_connection() as conn:
        result = conn.execute("DELETE FROM banned_users WHERE user_id = ?", (target_id,))
    
    if result.rowcount > 0:
        await update.message.reply_text(f'User {target_id} has been unbanned.')
        await context.bot.send_message(target_id, "You have been unbanned by an administrator.")
    else:
        await update.message.reply_text(f'User {target_id} was not banned.')

async def check_ban(update: Update, context: CallbackContext) -> None:
    admin_id = update.effective_user.id
    if not await is_sudo_user(admin_id) and admin_id != BOT_OWNER_ID:
        await update.message.reply_text('Permission denied.')
        return
    try:
        target_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text('Usage: /checkban <user_id>')
        return
    
    ban_info = await get_ban_info(target_id)

    if not ban_info:
        await update.message.reply_text(f"User `{target_id}` is not banned.", parse_mode='Markdown')
        return

    reason, banned_at_str, banned_by_admin_id = ban_info
    
    admin_info_str = "System (following a report)"
    if banned_by_admin_id:
        with get_db_connection() as conn:
            admin = conn.execute("SELECT username FROM sudo_users WHERE user_id = ?", (banned_by_admin_id,)).fetchone()
        if admin:
            admin_info_str = f"Admin @{admin['username']} (`{banned_by_admin_id}`)"
        elif banned_by_admin_id == BOT_OWNER_ID:
             admin_info_str = f"Bot Owner (`{banned_by_admin_id}`)"
        else:
            admin_info_str = f"Admin ID `{banned_by_admin_id}`"
    
    banned_at = datetime.strptime(banned_at_str, '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d at %H:%M')

    response_text = (
        f"🔍 *Ban Status for User `{target_id}`*\n\n"
        f"*Status:* Banned\n"
        f"*Ban Date:* {banned_at}\n"
        f"*Banned by:* {admin_info_str}\n"
        f"*Reason:* `{reason}`"
    )
    await update.message.reply_text(response_text, parse_mode='Markdown')

def main() -> None:
    # Initialize the database on startup
    conn = get_db_connection()
    setup_database(conn)
    conn.close()

    application = Application.builder().token(BOT_TOKEN).build()
    
    # Filter for private chats only
    private_filter = filters.ChatType.PRIVATE

    # Command Handlers
    application.add_handler(CommandHandler("start", start, filters=private_filter))
    application.add_handler(CommandHandler("help", help_command, filters=private_filter))
    application.add_handler(CommandHandler("rules", rules, filters=private_filter))
    application.add_handler(CommandHandler("connect", connect, filters=private_filter))
    application.add_handler(CommandHandler("disconnect", disconnect, filters=private_filter))
    application.add_handler(CommandHandler("reconnect", reconnect, filters=private_filter))
    application.add_handler(CommandHandler("report", report, filters=private_filter))

    # Admin Handlers
    application.add_handler(CommandHandler("addsudo", add_sudo, filters=private_filter))
    application.add_handler(CommandHandler("delsudo", del_sudo, filters=private_filter))
    application.add_handler(CommandHandler("ban", ban_user, filters=private_filter))
    application.add_handler(CommandHandler("unban", unban_user, filters=private_filter))
    application.add_handler(CommandHandler("checkban", check_ban, filters=private_filter))
    
    # Callback Handler for buttons
    application.add_handler(CallbackQueryHandler(handle_callback))

    # Message Handler (must be one of the last)
    message_filters = (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.Sticker.ALL) & ~filters.COMMAND & private_filter
    application.add_handler(MessageHandler(message_filters, message_handler))

    logger.info("OmegleBot is starting...")
    application.run_polling()

if __name__ == '__main__':
    main()
