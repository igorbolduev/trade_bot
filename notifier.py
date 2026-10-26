import requests
import logging

class Notifier:
    def __init__(self, config, logger):
        self.token = config.get('telegram_bot_token')
        self.chat_id = config.get('telegram_chat_id')
        self.logger = logger

    def send_message(self, message):
        if not self.token or not self.chat_id or self.token == "YOUR_BOT_TOKEN":
            self.logger.info("Telegram notifications not configured. Skipping message.")
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML"
        }

        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                return True
            else:
                self.logger.error(f"Telegram API Error: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            self.logger.error(f"Exception sending Telegram message: {e}")
            return False
