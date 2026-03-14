import os
import datetime
import config
from utils import save_json, load_json, setup_logger

logger = setup_logger(__name__)

class HitTracker:
    """AI 추천 성적 추적 클래스"""
    def __init__(self):
        self.filename = "ai_predictions.json"
        
        # 파일이 없거나 오류 발생 시 기본값 세팅
        data = load_json(self.filename)
        if not data or "history" not in data:
            self.data = {
                "history": [],
                "total_evaluated": 0,
                "hits": 0,
                "hit_rate": 50.0  # 초기 기본 적중률 50%
            }
        else:
            self.data = data

    def save_data(self):
        """데이터 저장 (utils 모듈의 save_json 사용)"""
        save_json(self.data, self.filename)

    def record_prediction(self, symbol: str, name: str, price: float, score: float):
        """AI 추천 종목 기록 (Step 2에서 호출)"""
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        
        # 동일 종목+날짜 중복 방지
        for entry in self.data["history"]:
            if entry["code"] == symbol and entry["date"] == today_str:
                return  # 이미 기록된 종목은 패스
                
        entry = {
            "date": today_str,
            "code": symbol,
            "name": name,
            "score": score,
            "buy_price": price,
            "evaluate_price": 0,
            "status": "PENDING", # 아직 5일이 지나지 않음
            "is_hit": False
        }
        self.data["history"].append(entry)
        
        # 최근 100건만 유지 (데이터 비대화 방지)
        if len(self.data["history"]) > 100:
            self.data["history"] = self.data["history"][-100:]
            
        self.save_data()

    def update_results(self, broker=None):
        """5일 지난 예측의 결과 업데이트 (Step 0에서 호출)
        
        Args:
            broker: 현재가를 조회할 수 있는 함수나 객체
                    여기서는 ai_quant_manager의 inquire_price 등을 추상화하여 넘김
        """
        updated_count = 0
        now = datetime.datetime.now()
        
        # 실제 환경에서는 kis_sample의 inquire_price를 broker 객체로 받아온다고 가정합니다.
        # import 문 순환 참조 방지를 위해 외부에서 주입받는 구조 유지
        
        for entry in self.data["history"]:
            if entry["status"] != "PENDING": continue
                
            entry_date = datetime.datetime.strptime(entry["date"], "%Y-%m-%d")
            days_passed = (now - entry_date).days
            
            # 설정한 검토 기간(5일)이 지났을 경우
            if days_passed >= config.HIT_RATE_CHECK_DAYS:
                try:
                    # broker 함수가 존재하면 조회 시도 (ex: broker(entry['code']))
                    if broker:
                        current_price = float(broker(entry["code"]))
                    else:
                        logger.warning("현재가 조회 브로커가 전달되지 않아 테스트 모드로 간주합니다.")
                        current_price = entry["buy_price"] * 1.05 # 테스트용 5% 상승 가정
                        
                    entry["evaluate_price"] = current_price
                    entry["status"] = "EVALUATED"
                    
                    # 수익률 계산 및 HIT/MISS 판정 (+3% 기준)
                    profit_rate = (current_price - entry["buy_price"]) / entry["buy_price"]
                    if profit_rate >= config.HIT_THRESHOLD_PCT:
                        entry["is_hit"] = True
                    else:
                        entry["is_hit"] = False
                        
                    updated_count += 1
                    
                except Exception as e:
                    logger.error(f"적중률 업데이트 중 가격 조회 실패 ({entry['name']}): {e}")

        # 적중률 갱신 트리거 호출
        if updated_count > 0:
            self._recalc_hit_rate()
            self.save_data()
            
        return updated_count

    def _recalc_hit_rate(self):
        """내부용: 최근 50건을 기준으로 적중률 재계산"""
        evaluated_entries = [e for e in self.data["history"] if e["status"] == "EVALUATED"]
        
        # 최근 50건 (가장 뒤에 있는 항목부터 50개)
        recent_evals = evaluated_entries[-50:]
        
        if not recent_evals:
            self.data["hit_rate"] = 50.0 # 평가된 항목이 없다면 기본 50%
            self.data["total_evaluated"] = 0
            self.data["hits"] = 0
        else:
            hits = sum(1 for e in recent_evals if e["is_hit"])
            total = len(recent_evals)
            
            self.data["total_evaluated"] = total
            self.data["hits"] = hits
            self.data["hit_rate"] = round((hits / total) * 100, 2)

    def get_hit_rate(self):
        """현재 적중률 반환 (%, 기본값 50.0)"""
        return self.data.get("hit_rate", 50.0)

    def get_dynamic_min_score(self):
        """적중률 기반 동적 최소 AI 점수 반환
        - 55% 이상: 65점 (완화)
        - 45~55%: 70점 (보통)
        - 45% 미만: 80점 (강화)
        """
        hit_rate = self.get_hit_rate()
        if hit_rate >= 55.0:
            return 65
        elif hit_rate >= 45.0:
            return 70
        else:
            return 80

    def get_dynamic_position_size(self):
        """적중률 기반 동적 투자 비중 반환
        - 55% 이상: 12% (0.12)
        - 45~55%: 10% (0.10)
        - 45% 미만: 7% (0.07)
        """
        hit_rate = self.get_hit_rate()
        if hit_rate >= 55.0:
            return 0.12 # 12%
        elif hit_rate >= 45.0:
            return 0.10 # 10%
        else:
            return 0.07 # 7%

    def get_stats_summary(self):
        """텔레그램 리포트용 요약 문자열 반환"""
        hit_rate = self.get_hit_rate()
        hits = self.data.get("hits", 0)
        total = self.data.get("total_evaluated", 0)
        
        if hit_rate >= 55.0:
            status = "🟢 High"
        elif hit_rate >= 45.0:
            status = "🟡 Normal"
        else:
            status = "🔴 Low"
            
        return f"📊 AI 적중률: {hit_rate:.1f}% ({hits}/{total}건) - {status}"
