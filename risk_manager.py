import logging

class RiskManager:
    """리스크 관리 클래스"""
    def __init__(self, stop_loss_rate: float, max_positions: int):
        self.stop_loss_rate = stop_loss_rate
        self.max_positions = max_positions

    def can_buy_more(self, current_position_count: int) -> bool:
        """현재 보유 종목 수와 최대 허용 종목 수를 비교하여 추가 매수 가능 여부 판단"""
        return current_position_count < self.max_positions

    def get_max_order_amount(self, total_capital: float, cash_buffer_ratio: float = 0.3, max_per_stock_ratio: float = 0.1) -> float:
        """종목당 최대 주문 가능 금액 계산"""
        # 전체 자본의 (1 - 현금 비중) 만 실제 투자에 사용
        investable_capital = total_capital * (1 - cash_buffer_ratio)
        # 한 종목당 최대 투자 가능 비중 적용
        max_amount_per_stock = total_capital * max_per_stock_ratio
        
        return min(investable_capital / self.max_positions, max_amount_per_stock)

    def is_stop_loss(self, current_price: float, avg_price: float) -> bool:
        """현재가가 손절선 아래로 내려갔는지 확인"""
        if avg_price <= 0: return False
        loss_rate = (current_price - avg_price) / avg_price
        return loss_rate <= self.stop_loss_rate
