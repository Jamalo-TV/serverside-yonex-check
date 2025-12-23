#send a telegram message to users
import requests
import json
import os
from pathlib import Path
from typing import List, Optional

# Telegram Bot Configuration
# Create a bot with @BotFather on Telegram and get your token
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# File to store subscriber chat IDs
SUBSCRIBERS_FILE = Path(__file__).parent / 'telegram_subscribers.json'


class Notifier:
    """Telegram notification system for Yonex product updates."""
    
    def __init__(self):
        self.owner_id = None
        self.subscribers = self._load_subscribers()
    
    def _load_subscribers(self) -> List[int]:
        """Load subscriber chat IDs and owner from file."""
        if SUBSCRIBERS_FILE.exists():
            try:
                with open(SUBSCRIBERS_FILE, 'r') as f:
                    data = json.load(f)
                    self.owner_id = data.get('owner_id')
                    return data.get('chat_ids', [])
            except Exception as e:
                print(f"⚠️ Error loading subscribers: {e}")
        return []
    
    def _save_subscribers(self):
        """Save subscriber chat IDs and owner to file."""
        try:
            with open(SUBSCRIBERS_FILE, 'w') as f:
                data = {'chat_ids': self.subscribers}
                if self.owner_id:
                    data['owner_id'] = self.owner_id
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"⚠️ Error saving subscribers: {e}")
    
    def add_subscriber(self, chat_id: int) -> bool:
        """Add a new subscriber."""
        if chat_id not in self.subscribers:
            self.subscribers.append(chat_id)
            self._save_subscribers()
            return True
        return False
    
    def remove_subscriber(self, chat_id: int) -> bool:
        """Remove a subscriber."""
        if chat_id in self.subscribers:
            self.subscribers.remove(chat_id)
            self._save_subscribers()
            return True
        return False
    
    def _send_telegram_message(self, chat_id: int, message: str, parse_mode: str = "HTML") -> bool:
        """Send a message to a specific chat ID."""
        try:
            response = requests.post(
                f"{TELEGRAM_API_URL}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": message,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True
                },
                timeout=10
            )
            
            if response.status_code == 200:
                return True
            else:
                print(f"⚠️ Telegram API error for chat {chat_id}: {response.text}")
                return False
                
        except Exception as e:
            print(f"⚠️ Error sending Telegram message to {chat_id}: {e}")
            return False
    
    def send_error_to_owner(self, error_message: str) -> bool:
        """Send error notification only to the owner."""
        if not self.owner_id:
            print("⚠️ No owner configured - cannot send error notification")
            return False
        
        message = f"🚨 <b>ERROR</b>\n\n{error_message}"
        success = self._send_telegram_message(self.owner_id, message)
        if success:
            print(f"📱 Error sent to owner ({self.owner_id})")
        return success
    
    def send_notification(self, title: str, body: str, tag: str = "") -> int:
        """Send notification to all subscribers. Returns number of successful sends."""
        message = f"<b>{title}</b>\n\n{body}"
        
        if not self.subscribers:
            print("⚠️ No Telegram subscribers to notify")
            return 0
        
        success_count = 0
        for chat_id in self.subscribers:
            if self._send_telegram_message(chat_id, message):
                success_count += 1
        
        print(f"📱 Telegram: Sent to {success_count}/{len(self.subscribers)} subscribers")
        return success_count
    
    def notify_product_added(self, product_name: str, category: str, price: str = ""):
        """Notify about a new product."""
        title = f"🆕 New Product - {category.upper()}"
        body = f"<b>{product_name}</b>"
        if price:
            body += f"\n💰 Price: {price}"
        body += f"\n\n🔗 <a href='https://www.yonex.ch/de/badminton/produkte/{category}/'>View on Yonex.ch</a>"
        
        self.send_notification(title, body, "product-added")
    
    def notify_product_removed(self, product_name: str, category: str):
        """Notify about a removed product."""
        title = f"🗑️ Product Removed - {category.upper()}"
        body = f"<b>{product_name}</b> is no longer available."
        
        self.send_notification(title, body, "product-removed")
    
    def notify_price_change(self, product_name: str, old_price: str, new_price: str, category: str):
        """Notify about a price change."""
        title = f"💰 Price Change - {category.upper()}"
        body = f"<b>{product_name}</b>\n\n"
        body += f"❌ Old: {old_price}\n"
        body += f"✅ New: {new_price}"
        
        self.send_notification(title, body, "price-change")
    
    def notify_size_change(self, product_name: str, category: str, 
                           newly_available: List[str] = None, 
                           newly_sold_out: List[str] = None):
        """Notify about size availability changes."""
        title = f"📏 Size Update - {category.upper()}"
        body = f"<b>{product_name}</b>\n\n"
        
        if newly_available:
            body += f"✅ Now Available: {', '.join(newly_available)}\n"
        if newly_sold_out:
            body += f"❌ Now Sold Out: {', '.join(newly_sold_out)}\n"
        
        body += f"\n🔗 <a href='https://www.yonex.ch/de/badminton/produkte/{category}/'>Check on Yonex.ch</a>"
        
        self.send_notification(title, body, "size-change")
    
    def notify_description_change(self, product_name: str, category: str):
        """Notify about a description change."""
        title = f"📝 Description Updated - {category.upper()}"
        body = f"<b>{product_name}</b> description has been updated."
        body += f"\n\n🔗 <a href='https://www.yonex.ch/de/badminton/produkte/{category}/'>View on Yonex.ch</a>"
        
        self.send_notification(title, body, "description-change")


def start_telegram_bot():
    """
    Simple polling bot to handle /start and /stop commands.
    Run this separately or in a thread to manage subscriptions.
    """
    notifier = Notifier()
    last_update_id = 0
    
    print("🤖 Telegram bot started. Waiting for commands...")
    
    while True:
        try:
            response = requests.get(
                f"{TELEGRAM_API_URL}/getUpdates",
                params={"offset": last_update_id + 1, "timeout": 30},
                timeout=35
            )
            
            if response.status_code != 200:
                continue
            
            updates = response.json().get('result', [])
            
            for update in updates:
                last_update_id = update['update_id']
                message = update.get('message', {})
                chat_id = message.get('chat', {}).get('id')
                text = message.get('text', '')
                
                if not chat_id:
                    continue
                
                if text == '/start':
                    if notifier.add_subscriber(chat_id):
                        notifier._send_telegram_message(
                            chat_id,
                            "✅ <b>Subscribed!</b>\n\nYou'll receive notifications when Yonex products change.\n\nUse /stop to unsubscribe."
                        )
                    else:
                        notifier._send_telegram_message(
                            chat_id,
                            "ℹ️ You're already subscribed!\n\nUse /stop to unsubscribe."
                        )
                
                elif text == '/stop':
                    if notifier.remove_subscriber(chat_id):
                        notifier._send_telegram_message(
                            chat_id,
                            "👋 <b>Unsubscribed.</b>\n\nYou won't receive any more notifications.\n\nUse /start to subscribe again."
                        )
                    else:
                        notifier._send_telegram_message(
                            chat_id,
                            "ℹ️ You're not subscribed.\n\nUse /start to subscribe."
                        )
                
                elif text == '/status':
                    count = len(notifier.subscribers)
                    notifier._send_telegram_message(
                        chat_id,
                        f"📊 <b>Bot Status</b>\n\n👥 Subscribers: {count}\n✅ Bot is running"
                    )
        
        except requests.exceptions.Timeout:
            continue
        except Exception as e:
            print(f"⚠️ Telegram bot error: {e}")
            import time
            time.sleep(5)


def test_error():
    """Test function to simulate an error notification to owner."""
    notifier = Notifier()
    
    print("=" * 50)
    print("🧪 TEST ERROR - Sending to Owner")
    print("=" * 50)
    print(f"👤 Owner ID: {notifier.owner_id}")
    
    if not notifier.owner_id:
        print("❌ No owner configured! Set owner_id in telegram_subscribers.json")
        return
    
    error_message = """Test error from yonex_site_checker.py

Category: bekleidung
Error Type: ConnectionError
Details: Failed to fetch https://www.yonex.ch/de/badminton/produkte/bekleidung/

This is a TEST error notification.
Timestamp: 2025-12-23 12:00:00"""

    print("\n📤 Sending test error to owner...")
    success = notifier.send_error_to_owner(error_message)
    
    if success:
        print("✅ Test error sent successfully!")
    else:
        print("❌ Failed to send test error")


def test_notification():
    """Test function to simulate a product change notification."""
    notifier = Notifier()
    
    print("=" * 50)
    print("🧪 TEST MODE - Simulating Product Changes")
    print("=" * 50)
    print(f"📱 Subscribers: {len(notifier.subscribers)}")
    
    if not notifier.subscribers:
        print("❌ No subscribers! Use /start in Telegram first.")
        return
    
    # Simulate a full change notification (like the real one)
    test_message = """==================================================
PRODUCT CHANGES DETECTED - TEST
Timestamp: 2025-12-23 12:00:00
Total Products: 50 → 51
==================================================

🆕 NEW PRODUCTS (1):
----------------------------------------
NEW #1:
  Name: Test Product - Yonex Power Cushion
  Price: CHF 149.00
  Image URL: /test/image.jpg
  Description: Test sizes S, M, L, XL (=komplett)
----------------------------------------

🔄 MODIFIED PRODUCTS (1):
----------------------------------------
MODIFIED #1 - Image: /test/shoe.jpg
  ✅ Name (unchanged): Yonex SHB 65Z
  ❌ PRICE CHANGED:
     OLD: CHF 179.00
     NEW: CHF 159.00
  ❌ DESCRIPTION CHANGED:
     OLD: Sizes 40-45 (=komplett)
     NEW: Sizes 40-45 (=ausverkauft in 42)
----------------------------------------

End of changes for TEST at 2025-12-23 12:00:00
=================================================="""

    print("\n📤 Sending test notification...")
    success = notifier.send_notification(
        title="🧪 TEST - Yonex Product Update",
        body=test_message,
        tag="test"
    )
    
    print(f"\n✅ Test complete! Sent to {success} subscriber(s)")


if __name__ == "__main__":
    import sys
    
    # Check for test mode
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        test_notification()
        sys.exit(0)
    
    if len(sys.argv) > 1 and sys.argv[1] == "--test-error":
        test_error()
        sys.exit(0)
    
    print("=" * 50)
    print("Yonex Telegram Notification Bot")
    print("=" * 50)
    print("\nTo use this bot:")
    print("1. Create a bot with @BotFather on Telegram")
    print("2. Replace TELEGRAM_BOT_TOKEN in this file")
    print("3. Run this script to start the subscription bot")
    print("\nCommands:")
    print("  /start  - Subscribe to notifications")
    print("  /stop   - Unsubscribe")
    print("  /status - Check bot status")
    print("\nTest mode:")
    print("  python telegram_notifier.py --test        (test product notification)")
    print("  python telegram_notifier.py --test-error  (test error to owner)")
    print("=" * 50)
    
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("\n❌ ERROR: Please set your TELEGRAM_BOT_TOKEN first!")
    else:
        start_telegram_bot()

