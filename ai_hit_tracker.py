import logging
import datetime
import os
import json

class HitTracker:
    """AI 추천 성적 추적 클래스"""
    def __init__(self):
        self.filename = "hit_rate.json"
        self.data = self.load_data()

    def load_data(self):
        """데이터 로드"""
        filepath = os.path.join("data", self.filename)
        if not os.path.exists(filepath):
            return {"history": [], "total_recommendations": 0, "hits": 0, "hit_rate": 0.0}
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {"history": [], "total_recommendations": 0, "hits": 0, "hit_rate": 0.0}

    def save_data(self):
        """데이터 저장"""
        filepath = os.path.join("data", self.filename)
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logging.error(f"성적 데이터 저장 실패: {e}")

    def record_recommendation(self, code: str, name: str, score: float, price: float):
        """추천 종목 기록"""
        entry = {
            "date": datetime.datetime.now().strftime("%Y-%m-%d"),
            "code": code,
            "name": name,
            "score": score,
            "buy_price": price,
            "sell_price": 0,
            "is_hit": False,
            "status": "HOLDING"
        }
        self.data["history"].append(entry)
        self.data["total_recommendations"] += 1
        self.save_data()

    def update_result(self, code: str, sell_price: float):
        """매도 시 성적 업데이트"""
        for entry in self.data["history"]:
            if entry["code"] == code and entry["status"] == "HOLDING":
                entry["sell_price"] = sell_price
                entry["status"] = "SOLD"
                # 매수가 대비 수익이면 적중(Hit)으로 간주
                if sell_price > entry["buy_price"]:
                    entry["is_hit"] = True
                    self.data["hits"] += 1
                break
        
        # 적중률 갱신
        if self.data["total_recommendations"] > 0:
            self.data["hit_rate"] = self.data["hits"] / self.data["total_recommendations"]
        self.save_data()

    def get_hit_rate(self):
        """현재 적중률 반환"""
        return self.data["hit_rate"]
