import os
import tempfile
import shutil
from pathlib import Path
import asyncio
import logging
from urllib.parse import urlparse
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode
from mega import Mega
import mimetypes

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class MegaTelegramBot:
    def __init__(self, bot_token: str, target_group_id: str):
        self.bot_token = bot_token
        self.target_group_id = target_group_id
        self.mega = Mega()
        self.temp_dir = tempfile.mkdtemp()
        
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start command handler"""
        welcome_message = """
🤖 **MEGA to Telegram Bot**

Send me a MEGA folder link and I'll download all images and videos, then upload them to the target group.

**Usage:**
1. Send me a MEGA folder link (e.g., https://mega.nz/folder/...)
2. I'll process the folder and upload media files
3. Files will be uploaded to the configured group

**Supported formats:**
- Images: JPG, PNG, GIF, WEBP, BMP
- Videos: MP4, AVI, MOV, MKV, WEBM

Ready to receive your MEGA link! 📁
        """
        await update.message.reply_text(welcome_message, parse_mode=ParseMode.MARKDOWN)

    async def handle_mega_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle MEGA folder links"""
        message_text = update.message.text.strip()
        
        # Check if it's a MEGA link
        if not self.is_mega_link(message_text):
            await update.message.reply_text(
                "❌ Please send a valid MEGA folder link.\n"
                "Example: https://mega.nz/folder/..."
            )
            return
        
        # Send processing message
        processing_msg = await update.message.reply_text("🔄 Processing MEGA folder...")
        
        try:
            # Download and upload files
            await self.process_mega_folder(message_text, update, processing_msg)
            
        except Exception as e:
            logger.error(f"Error processing MEGA folder: {e}")
            await processing_msg.edit_text(
                f"❌ Error processing folder: {str(e)}\n"
                "Please check the link and try again."
            )

    def is_mega_link(self, url: str) -> bool:
        """Check if URL is a valid MEGA folder link"""
        try:
            parsed = urlparse(url)
            return (
                parsed.netloc.lower() in ['mega.nz', 'www.mega.nz'] and
                '/folder/' in parsed.path
            )
        except:
            return False

    async def process_mega_folder(self, mega_url: str, update: Update, status_msg):
        """Download files from MEGA and upload to Telegram"""
        try:
            # Update status
            await status_msg.edit_text("📥 Connecting to MEGA...")
            
            # Login to MEGA (anonymous)
            m = self.mega.login()
            
            # Get folder contents
            await status_msg.edit_text("📋 Getting folder contents...")
            
            # Extract folder ID from URL
            import re
            folder_pattern = r'#F!([a-zA-Z0-9_-]+)!([a-zA-Z0-9_-]+)'
            match = re.search(folder_pattern, mega_url)
            
            if not match:
                await status_msg.edit_text("❌ Invalid MEGA folder URL format.")
                return
            
            folder_id = match.group(1)
            folder_key = match.group(2)
            
            # Get all files from the account
            files = m.get_files()
            
            # Find files in the specific folder
            folder_files = {}
            for file_id, file_data in files.items():
                if isinstance(file_data, dict) and 'a' in file_data:
                    # Check if this file belongs to our folder
                    if 'p' in file_data and file_data['p'] == folder_id:
                        folder_files[file_id] = file_data
            
            if not folder_files:
                # Try alternative method: import folder first
                try:
                    await status_msg.edit_text("📂 Importing folder...")
                    m.import_public_url(mega_url)
                    files = m.get_files()
                    
                    # Look for recently imported files
                    for file_id, file_data in files.items():
                        if isinstance(file_data, dict) and 'a' in file_data:
                            folder_files[file_id] = file_data
                            
                except Exception as e:
                    logger.error(f"Error importing folder: {e}")
                    await status_msg.edit_text("❌ Could not access the MEGA folder. Please ensure it's a public folder.")
                    return
            
            if not folder_files:
                await status_msg.edit_text("❌ No files found in the folder or folder is private.")
                return
            
            # Filter media files
            media_files = self.filter_media_files(folder_files)
            
            if not media_files:
                await status_msg.edit_text("❌ No supported media files found in the folder.")
                return
            
            await status_msg.edit_text(f"📁 Found {len(media_files)} media files. Starting download...")
            
            # Process each file
            uploaded_count = 0
            failed_count = 0
            
            for i, (file_id, file_info) in enumerate(media_files.items(), 1):
                try:
                    filename = file_info['a']['n']  # Original filename
                    file_size = file_info['s']
                    
                    # Update progress
                    await status_msg.edit_text(
                        f"📥 Downloading ({i}/{len(media_files)}): {filename}\n"
                        f"Size: {self.format_file_size(file_size)}"
                    )
                    
                    # Download file to temp directory
                    local_path = os.path.join(self.temp_dir, filename)
                    m.download(file_id, self.temp_dir)
                    
                    # Upload to Telegram group
                    await status_msg.edit_text(
                        f"📤 Uploading ({i}/{len(media_files)}): {filename}"
                    )
                    
                    success = await self.upload_to_telegram(local_path, filename)
                    
                    if success:
                        uploaded_count += 1
                    else:
                        failed_count += 1
                    
                    # Clean up local file
                    if os.path.exists(local_path):
                        os.remove(local_path)
                        
                except Exception as e:
                    logger.error(f"Error processing file {filename}: {e}")
                    failed_count += 1
                    continue
            
            # Final status
            result_message = f"✅ **Upload Complete!**\n\n"
            result_message += f"📊 **Results:**\n"
            result_message += f"• Successfully uploaded: {uploaded_count}\n"
            result_message += f"• Failed: {failed_count}\n"
            result_message += f"• Total processed: {len(media_files)}"
            
            await status_msg.edit_text(result_message, parse_mode=ParseMode.MARKDOWN)
            
        except Exception as e:
            logger.error(f"Error in process_mega_folder: {e}")
            await status_msg.edit_text(f"❌ Error: {str(e)}")

    def filter_media_files(self, files: dict) -> dict:
        """Filter only image and video files"""
        media_extensions = {
            # Images
            '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff',
            # Videos
            '.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv', '.m4v'
        }
        
        media_files = {}
        for file_id, file_info in files.items():
            if 'a' in file_info and 'n' in file_info['a']:
                filename = file_info['a']['n'].lower()
                if any(filename.endswith(ext) for ext in media_extensions):
                    media_files[file_id] = file_info
        
        return media_files

    async def upload_to_telegram(self, file_path: str, filename: str) -> bool:
        """Upload file to Telegram group"""
        try:
            # Get bot instance
            app = Application.builder().token(self.bot_token).build()
            
            # Determine file type
            mime_type, _ = mimetypes.guess_type(file_path)
            
            with open(file_path, 'rb') as file:
                if mime_type and mime_type.startswith('image/'):
                    # Upload as photo
                    await app.bot.send_photo(
                        chat_id=self.target_group_id,
                        photo=file,
                        caption=f"📸 {filename}"
                    )
                elif mime_type and mime_type.startswith('video/'):
                    # Upload as video
                    await app.bot.send_video(
                        chat_id=self.target_group_id,
                        video=file,
                        caption=f"🎥 {filename}"
                    )
                else:
                    # Upload as document
                    await app.bot.send_document(
                        chat_id=self.target_group_id,
                        document=file,
                        caption=f"📄 {filename}"
                    )
            
            return True
            
        except Exception as e:
            logger.error(f"Error uploading {filename}: {e}")
            return False

    def format_file_size(self, size_bytes: int) -> str:
        """Format file size in human readable format"""
        if size_bytes == 0:
            return "0 B"
        size_names = ["B", "KB", "MB", "GB"]
        i = 0
        while size_bytes >= 1024 and i < len(size_names) - 1:
            size_bytes /= 1024.0
            i += 1
        return f"{size_bytes:.1f} {size_names[i]}"

    def cleanup(self):
        """Clean up temporary directory"""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def run(self):
        """Run the bot"""
        app = Application.builder().token(self.bot_token).build()
        
        # Add handlers
        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_mega_link))
        
        # Start the bot
        logger.info("Bot starting...")
        app.run_polling()

# Configuration
if __name__ == "__main__":
    # Load environment variables from .env file
    load_dotenv()
    
    # Get bot token and target group ID from environment variables
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    TARGET_GROUP_ID = os.getenv("TARGET_GROUP_ID")
    
    # Validate that required environment variables are set
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        raise ValueError("BOT_TOKEN not set in .env file")
    
    if not TARGET_GROUP_ID or TARGET_GROUP_ID == "YOUR_GROUP_ID_HERE":
        raise ValueError("TARGET_GROUP_ID not set in .env file")
    
    # Create and run bot
    bot = MegaTelegramBot(BOT_TOKEN, TARGET_GROUP_ID)
    
    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    finally:
        bot.cleanup()