import requests
import logging
import os
import json
import datetime

class TelegramNotifier:
    """텔레그램 알림 전송 클래스"""
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{self.token}/sendMessage"

    def send_message(self, message: str):
        """메시지 전송"""
        if not self.token or not self.chat_id or "YOUR_" in self.token:
            logging.warning("텔레그램 설정이 완료되지 않아 메시지를 보내지 않습니다.")
            return False
            
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        try:
            res = requests.post(self.base_url, json=payload, timeout=10)
            return res.status_code == 200
        except Exception as e:
            logging.error(f"텔레그램 전송 실패: {e}")
            return False

def save_log(message: str, level: str = "INFO"):
    """로그 저장 (기본 logging 활용)"""
    if level == "INFO":
        logging.info(message)
    elif level == "ERROR":
        logging.error(message)
    elif level == "WARNING":
        logging.warning(message)

def save_data(filename: str, data: dict):
    """데이터를 JSON 파일로 저장"""
    filepath = os.path.join("data", filename)
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        logging.error(f"데이터 저장 실패 ({filename}): {e}")
        return False

def load_data(filename: str):
    """JSON 파일에서 데이터 로드"""
    filepath = os.path.join("data", filename)
    if not os.path.exists(filepath):
        return {}
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"데이터 로드 실패 ({filename}): {e}")
        return {}
