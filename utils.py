import os
import json
import logging
import datetime
import requests

import config

def ensure_directories():
    """data/, logs/, reports/ 폴더가 없으면 생성"""
    directories = ['data', 'logs', 'reports']
    for directory in directories:
        if not os.path.exists(directory):
            os.makedirs(directory)

def get_today_str():
    """오늘 날짜 문자열 반환 ("2026-02-02" 형식)"""
    return datetime.datetime.now().strftime("%Y-%m-%d")

def setup_logger(name):
    """로거 생성 (파일 + 콘솔 출력)"""
    ensure_directories()
    today_str = get_today_str()
    log_file = os.path.join("logs", f"quant_{today_str}.log")
    
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    
    # 중복 추가 방지
    if logger.hasHandlers():
        logger.handlers.clear()
        
    # 콘솔 핸들러 (INFO 이상)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(console_format)
    
    # 파일 핸들러 (DEBUG 이상)
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(file_format)
    
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    
    return logger

def send_telegram_msg(message):
    """텔레그램 봇으로 메시지 발송"""
    token = getattr(config, 'TELEGRAM_BOT_TOKEN', '')
    chat_id = getattr(config, 'TELEGRAM_CHAT_ID', '')
    
    if not token or not chat_id or "YOUR_" in token:
        # 에러만 출력하고 진행
        print("텔레그램 설정이 미비하여 발송을 건너뜁니다.")
        return False
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"텔레그램 전송 실패: {e}")
        return False

def save_json(data, filename):
    """딕셔너리를 data/{filename}에 JSON으로 저장"""
    ensure_directories()
    filepath = os.path.join("data", filename)
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        print(f"JSON 저장 실패 ({filename}): {e}")
        return False

def load_json(filename):
    """data/{filename}에서 JSON 로드"""
    filepath = os.path.join("data", filename)
    if not os.path.exists(filepath):
        return {}
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"JSON 로드 실패 ({filename}): {e}")
        return {}

def is_weekday():
    """평일(월~금) 여부 반환"""
    # 0: 월요일, ..., 4: 금요일
    return datetime.datetime.now().weekday() < 5

def is_market_open_time():
    """장 운영 시간(09:00~15:30) 여부 반환"""
    now = datetime.datetime.now()
    market_start = now.replace(hour=9, minute=0, second=0, microsecond=0)
    market_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_start <= now <= market_end

def read_recent_logs(lines=20):
    """최근 로그 파일에서 마지막 N줄 읽기"""
    today_str = get_today_str()
    log_file = os.path.join("logs", f"quant_{today_str}.log")
    
    if not os.path.exists(log_file):
        return "오늘의 로그 파일이 존재하지 않습니다."
        
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            content = f.readlines()
            return "".join(content[-lines:])
    except Exception as e:
        return f"로그 읽기 오류: {e}"

def update_account_history(total_asset, date):
    """일일 자산 기록을 account_history.json에 추가"""
    filename = "account_history.json"
    filepath = os.path.join("data", filename)
    
    history = []
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                history = json.load(f)
                if not isinstance(history, list):
                    history = []
        except Exception:
            history = []
            
    history.append({
        "date": date,
        "total_asset": total_asset
    })
    
    save_json(history, filename)
    return True

