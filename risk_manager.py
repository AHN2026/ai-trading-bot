import logging
import config
from utils import save_json, load_json, setup_logger, get_today_str

logger = setup_logger(__name__)

class RiskManager:
    """리스크 관리 클래스 (6단계 안전 점검 및 서킷브레이커)"""
    def __init__(self, stop_loss_rate: float, max_positions: int):
        self.stop_loss_rate = stop_loss_rate
        self.max_positions = max_positions
        self.daily_counters = {
            'date': get_today_str(),
            'trade_count': 0,
            'realized_pnl': 0,
            'buy_amount': 0,
            'start_asset': 0,
            'is_halted': False
        }
        self.api_failure_count = 0
        self.API_FAILURE_THRESHOLD = 3
        
        # 일일 카운터 데이터 로드
        loaded = load_json("daily_counters.json")
        if loaded and loaded.get('date') == get_today_str():
            self.daily_counters.update(loaded)
        else:
            self.reset_daily_counters()

    def reset_daily_counters(self):
        """매일 Step 0에서 호출, 일일 카운터 초기화"""
        self.daily_counters['date'] = get_today_str()
        self.daily_counters['trade_count'] = 0
        self.daily_counters['realized_pnl'] = 0
        self.daily_counters['buy_amount'] = 0
        self.daily_counters['is_halted'] = False
        save_json(self.daily_counters, "daily_counters.json")
        
    def can_buy_more(self, current_position_count: int) -> bool:
        """기존 메서드 호환성: 현재 보유 종목 수와 최대 허용 종목 수를 비교하여 추가 매수 가능 여부 판단"""
        return current_position_count < self.max_positions

    def get_max_order_amount(self, total_capital: float, cash_buffer_ratio: float = 0.3, max_per_stock_ratio: float = 0.1) -> float:
        """기존 메서드 호환성: 종목당 최대 주문 가능 금액 계산"""
        investable_capital = total_capital * (1 - cash_buffer_ratio)
        max_amount_per_stock = total_capital * max_per_stock_ratio
        return min(investable_capital / self.max_positions, max_amount_per_stock)

    def can_buy(self, symbol, amount, sector, current_positions, market_mode):
        """매수 가능 여부를 6단계로 체크
        
        Returns:
            (bool, str): (승인 여부, 사유)
        """
        # Stage 0: 시스템 정지 여부
        if self.daily_counters['is_halted']:
            return False, "시스템이 정지된 상태입니다 (서킷브레이커 발동 등)."
            
        # Stage 1: 일일 거래 횟수
        if self.daily_counters['trade_count'] >= config.MAX_DAILY_TRADES:
            return False, f"일일 최대 거래 횟수({config.MAX_DAILY_TRADES}회)를 초과했습니다."
            
        # Stage 2: 현금 버퍼 체크 
        start_asset = self.daily_counters.get('start_asset', 0)
        current_invested = sum(p['quantity'] * p['avg_price'] for p in current_positions.values())
        if start_asset > 0:
            if (current_invested + amount) > start_asset * (1 - config.CASH_BUFFER_RATIO):
                return False, f"현금 버퍼 비중({config.CASH_BUFFER_RATIO*100}%) 유지 실패 (투자금 한도 초과)."
        
        # Stage 3: 종목당 최대 투자 비중
        if start_asset > 0:
            if amount > start_asset * config.MAX_PER_STOCK_RATIO:
                return False, f"종목당 최대 투자 비중({config.MAX_PER_STOCK_RATIO*100}%)을 넘어섭니다."
                
        # Stage 4: 섹터당 최대 종목 수 (기본 2개로 제한 가정)
        MAX_PER_SECTOR = 2
        sector_count = 0
        universe_sectors = {item['code']: item['sector'] for item in config.UNIVERSE}
        my_sector = universe_sectors.get(symbol, sector)
        
        for code in current_positions.keys():
            if universe_sectors.get(code) == my_sector:
                sector_count += 1
                
        if sector_count >= MAX_PER_SECTOR:
            return False, f"섹터({my_sector})당 최대 보유 개수({MAX_PER_SECTOR}개) 도달."

        # Stage 5: 최대 보유 종목 수
        if len(current_positions) >= config.MAX_POSITIONS:
            return False, f"최대 보유 가능 종목 수({config.MAX_POSITIONS}종목)를 초과했습니다."
            
        # Stage 6: 중복 보유 체크
        if symbol in current_positions:
            return False, f"이미 보유 중인 종목입니다 ({symbol})."
            
        return True, "매수 승인"

    def can_pyramid(self, symbol, current_stage, profit_rate, current_positions):
        """피라미딩(추가 매수) 가능 여부
        
        Returns:
            (bool, int, str): (승인, 다음 단계, 사유)
        """
        if current_stage == 1 and profit_rate >= config.PYRAMID_TRIGGER_2:
            return True, 2, "2단계 피라미딩 조건 달성"
        elif current_stage == 2 and profit_rate >= config.PYRAMID_TRIGGER_3:
            return True, 3, "3단계 피라미딩 조건 달성"
        return False, current_stage, "조건 미달"

    def record_trade(self, pnl, trade_type):
        """거래 기록 (수익 갱신 및 매수 카운트)"""
        if trade_type == 'BUY':
            self.daily_counters['trade_count'] += 1
            
        self.daily_counters['realized_pnl'] += pnl
        save_json(self.daily_counters, "daily_counters.json")
        
    def check_daily_circuit_breaker(self):
        """일일 손실률 체크하여 서킷브레이커 발동 판단"""
        start = self.daily_counters.get('start_asset', 0)
        pnl = self.daily_counters.get('realized_pnl', 0)
        
        if start > 0 and (pnl / start) <= config.MAX_DAILY_LOSS:
            if not self.daily_counters['is_halted']:
                logger.error(f"🚨 서킷브레이커 발동! (일일 손실 한도 {config.MAX_DAILY_LOSS*100}% 초과)")
                self.daily_counters['is_halted'] = True
                save_json(self.daily_counters, "daily_counters.json")
            return True
        return False

    def check_system_health(self):
        """API 오류 누적 체크하여 시스템 정지 여부 판단"""
        self.api_failure_count += 1
        if self.api_failure_count >= self.API_FAILURE_THRESHOLD:
            logger.error("🚨 연속 API 호출 실패로 시스템을 정지합니다.")
            self.daily_counters['is_halted'] = True
            save_json(self.daily_counters, "daily_counters.json")
            return False
        return True
        
    def calculate_real_profit(self, buy_price, sell_price, quantity):
        """수수료/세금 포함 실수익 계산"""
        # 매수 수수료 0.015%, 매도 수수료 0.015%, 거래세 0.18%
        buy_fee = buy_price * quantity * 0.00015
        sell_fee = sell_price * quantity * 0.00015
        tax = sell_price * quantity * 0.0018
        
        gross_profit = (sell_price - buy_price) * quantity
        net_profit = gross_profit - buy_fee - sell_fee - tax
        return net_profit

