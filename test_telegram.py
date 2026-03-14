from utils import send_telegram_msg

if __name__ == "__main__":
    print("텔레그램 메시지 전송 시도 중...")
    result = send_telegram_msg("🤖 시스템 연결 테스트 성공!")
    if result:
        print("성공! 텔레그램을 확인하세요.")
    else:
        print("실패! 설정값(토큰, 챗 ID) 및 인터넷 연결을 확인하세요.")
