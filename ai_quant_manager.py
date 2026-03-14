import sys
import os
import time
import datetime
import logging
import json
import schedule
import pandas as pd
import google.generativeai as genai
import yfinance as yf
from typing import List, Dict

# 프로젝트 모듈 임포트
import config
from utils import TelegramNotifier, save_log, save_data, load_data
from risk_manager import RiskManager
from ai_hit_tracker import HitTracker

# kis_sample 경로 추가 (인증 및 주문 기능 사용)
KIS_SAMPLE_PATH = os.path.join(os.path.dirname(__file__), 'kis_sample', 'examples_user')
sys.path.append(KIS_SAMPLE_PATH)
sys.path.append(os.path.join(KIS_SAMPLE_PATH, 'domestic_stock'))

try:
    import kis_auth as ka
    from domestic_stock_functions import inquire_balance, order_cash, inquire_price, chk_holiday, inquire_daily_price
except ImportError as e:
    logging.error(f"kis_sample 모듈 로드 실패: {e}")

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(config.LOG_DIR, "trading.log"), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class QuantManager:
    """AI 주식 자동매매 시스템의 메인 엔진"""
    def __init__(self):
        self.notifier = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
        self.risk_manager = RiskManager(config.STOP_LOSS_RATE, config.MAX_STOCK_COUNT)
        self.tracker = HitTracker()
        self.positions = {} # 현재 포지션 정보 {종목코드: 정보}
        self.trading_mode = "MODERATE" # 기본 모드
        self.max_stocks = 5
        self.is_running = True
        
        # Gemini AI 초기화
        genai.configure(api_key=config.GEMINI_API_KEY)
        self.model = genai.GenerativeModel(config.GEMINI_MODEL)

    # --------------------------------------------------------------------------
    # Step 0: 초기화 및 환경 점검
    # --------------------------------------------------------------------------
    def initialize_routine(self):
        """Step 0: 한투 API 초기화 및 거래일 확인"""
        logger.info("Step 0: 시스템 초기화 시작")
        
        # 1. 한투 API 인증 (모의투자/실전투자에 따라 설정)
        svr = "vps" if config.IS_PAPER_TRADING else "prod"
        ka.auth(svr=svr)
        self.trenv = ka.getTREnv()
        
        # 2. 거래일 확인
        today = datetime.datetime.now().strftime("%Y%m%d")
        holiday_df = chk_holiday(bass_dt=today)
        if not holiday_df.empty and holiday_df.iloc[0]['opnd_yn'] == 'N':
            logger.info("오늘은 개장일이 아닙니다. 휴식을 취합니다.")
            return False
            
        # 3. 잔고 조회 및 포지션 동기화
        self.sync_positions()
        return True

    def sync_positions(self):
        """현재 계좌의 잔고를 조회하여 시스템 포지션 데이터와 동기화"""
        logger.info("포지션 동기화 중...")
        res1, res2 = inquire_balance(
            env_dv="demo" if config.IS_PAPER_TRADING else "real",
            cano=self.trenv.my_acct,
            acnt_prdt_cd=self.trenv.my_prod,
            prcs_dvsn="00"
        )
        # res2(종목 리스트)를 바탕으로 self.positions 업데이트
        new_positions = {}
        if not res2.empty:
            for _, row in res2.iterrows():
                code = row['pdno']
                if code == '000000': continue # 예수금 합계 등 제외
                new_positions[code] = {
                    "name": row['prdt_name'],
                    "quantity": int(row['hldg_qty']),
                    "avg_price": float(row['pchs_avg_pric']),
                    "stop_loss": float(row['pchs_avg_pric']) * (1 + config.STOP_LOSS_RATE),
                    "target_price": float(row['pchs_avg_pric']) * (1 + config.TAKE_PROFIT_FULL),
                    "pyramid_stage": 1, 
                    "highest_price": float(row['pchs_avg_pric']),
                    "consecutive_down_days": 0
                }
        self.positions = new_positions
        logger.info(f"동기화 완료: 현재 {len(self.positions)}개 종목 보유 중")

    # --------------------------------------------------------------------------
    # Step 1: 거시경제 분석 및 트레이딩 모드 결정
    # --------------------------------------------------------------------------
    def analyze_macro_environment(self):
        """Step 1: VIX, NASDAQ 지수 등을 분석하여 투자 강도 결정"""
        logger.info("Step 1: 거시경제 분석 시작")
        
        try:
            # 1. 데이터 수집 (VIX: ^VIX, NASDAQ: ^IXIC)
            vix_data = yf.Ticker("^VIX").history(period="1d")
            vix = vix_data['Close'].iloc[-1]
            
            nasdaq_ticker = yf.Ticker("^IXIC")
            nasdaq_hist = nasdaq_ticker.history(period="2d")
            nasdaq_change = ((nasdaq_hist['Close'].iloc[-1] - nasdaq_hist['Close'].iloc[-2]) 
                             / nasdaq_hist['Close'].iloc[-2] * 100)

            # 2. 트레이딩 모드 결정 로직
            if vix >= config.VIX_STORMY_THRESHOLD or nasdaq_change <= -2.0:
                self.trading_mode = "DEFENSIVE"
                self.max_stocks = 0
            elif vix >= config.VIX_CLOUDY_THRESHOLD or nasdaq_change <= -1.0:
                self.trading_mode = "MODERATE"
                self.max_stocks = 5
            else:
                self.trading_mode = "AGGRESSIVE"
                self.max_stocks = 10
                
            msg = f"📈 거시경제 분석 결과\n- VIX: {vix:.2f}\n- NASDAQ: {nasdaq_change:+.2f}%\n- 모드: <b>{self.trading_mode}</b>"
            logger.info(msg.replace("<b>", "").replace("</b>", ""))
            self.notifier.send_message(msg)
            
        except Exception as e:
            logger.error(f"거시경제 분석 중 오류 발생 (기본값 MODERATE 사용): {e}")
            self.trading_mode = "MODERATE"
            self.max_stocks = 5

    # --------------------------------------------------------------------------
    # Step 2: 종목 선정 (Gemini AI 분석)
    # --------------------------------------------------------------------------
    def select_top_stocks(self):
        """Step 2: AI 분석을 통해 매수할 종목 선정 (08:30 실행)"""
        if self.max_stocks == 0:
            logger.info("시장 상태가 DEFENSIVE이므로 종목 선정을 건너뜁니다.")
            return []
            
        logger.info("Step 2: 종목 분석 및 선정 시작")
        self.notifier.send_message("🔍 AI 종목 분석을 시작합니다.")
        
        recommendations = []
        for item in config.UNIVERSE:
            code = item['code']
            name = item['name']
            
            try:
                # 1. 차트 데이터 수집 (최근 30일 일봉)
                df_daily = inquire_daily_price(
                    env_dv="demo" if config.IS_PAPER_TRADING else "real",
                    fid_cond_mrkt_div_code="J",
                    fid_input_iscd=code,
                    fid_period_div_code="D",
                    fid_org_adj_prc="1"
                )
                
                chart_summary = ""
                if not df_daily.empty:
                    # 최근 5일치 종가 정보 추출
                    recent_prices = df_daily.head(5)[['stck_bsop_date', 'stck_clpr']].to_string(index=False)
                    chart_summary = f"최근 5일 가격 추이:\n{recent_prices}"

                # 2. Gemini AI에게 분석 요청 (뉴스 및 기술 분석 포함 프롬프트)
                prompt = f"""
                종목: {name}({code})
                {chart_summary}
                시장 모드: {self.trading_mode}
                
                위 데이터를 바탕으로 오늘 매수 적합도를 분석해서 JSON으로 응답해줘.
                형식: {{"score": 점수(0~100), "reason": "이유", "target_price": 목표가, "stop_loss": 손절가}}
                """
                
                response = self.model.generate_content(prompt)
                # JSON 추출 (Markdown 코드 블럭 제거)
                res_text = response.text.replace("```json", "").replace("```", "").strip()
                analysis = json.loads(res_text)
                
                score = analysis.get("score", 0)
                if score >= config.MIN_AI_SCORE:
                    logger.info(f"[{name}] 점수: {score} - 선정됨")
                    recommendations.append({
                        "code": code,
                        "name": name,
                        "score": score,
                        "analysis": analysis
                    })
                else:
                    logger.info(f"[{name}] 점수: {score} - 제외됨")
                    
            except Exception as e:
                logger.error(f"{name} 분석 중 오류: {e}")
                
        # 점수 순 정렬 후 모드별 최대 개수만큼 선정
        recommendations.sort(key=lambda x: x['score'], reverse=True)
        self.selected_stocks = recommendations[:self.max_stocks]
        
        if self.selected_stocks:
            names = ", ".join([s['name'] for s in self.selected_stocks])
            self.notifier.send_message(f"✅ 종목 선정 완료: {names}")
        else:
            self.notifier.send_message("⚠️ 분석 결과 매수 적합 종목이 없습니다.")
            
        return self.selected_stocks

    # --------------------------------------------------------------------------
    # 종목 선정 결과 체크 및 보고 (사용자 요청 추가)
    # --------------------------------------------------------------------------
    def check_stock_selection(self):
        """선정된 종목이 있는지 최종 체크하고 요약 보고"""
        if hasattr(self, 'selected_stocks') and self.selected_stocks:
            logger.info(f"현재 선정된 종목 수: {len(self.selected_stocks)}")
            return True
        logger.warning("선정된 종목이 없습니다.")
        return False

    # --------------------------------------------------------------------------
    # Step 3: 매수 실행
    # --------------------------------------------------------------------------
    def execute_buying(self, selected_stocks):
        """Step 3: 선정된 종목 매수 실행"""
        logger.info("Step 3: 매수 프로세스 시작")
        for stock in selected_stocks:
            code = stock['code']
            if code in self.positions: continue # 이미 보유 중이면 패스
            
            if self.risk_manager.can_buy_more(len(self.positions)):
                order_amount = self.risk_manager.get_max_order_amount(config.TOTAL_CAPITAL) * config.PYRAMID_STAGE_1
                
                # 시장가(01) 매수 주문
                res = order_cash(
                    env_dv="demo" if config.IS_PAPER_TRADING else "real",
                    ord_dv="buy",
                    cano=self.trenv.my_acct,
                    acnt_prdt_cd=self.trenv.my_prod,
                    pdno=code,
                    ord_dvsn="01", # 시장가
                    ord_qty="0", # 수량은 금액에 맞춰 자동 계산 로직 필요
                    ord_unpr="0",
                    excg_id_dvsn_cd="KRX"
                )
                
                if not res.empty:
                    self.notifier.send_message(f"✅ 매수 완료: {stock['name']}({code})")
                    # positions 데이터 업데이트 로직 필요

    # --------------------------------------------------------------------------
    # Step 4: 실시간 모니터링 (장중 반복 실행)
    # --------------------------------------------------------------------------
    def monitoring_routine(self):
        """Step 4: 매 10분마다 보유 종목 상태 체크 (손절/익절/피라미딩)"""
        logger.info("Step 4: 상태 모니터링 중...")
        for code, pos in list(self.positions.items()):
            # 현재가 조회
            price_df = inquire_price(env_dv="demo" if config.IS_PAPER_TRADING else "real", 
                                     fid_cond_mrkt_div_code="J", fid_input_iscd=code)
            curr_price = float(price_df.iloc[0]['stck_prpr'])
            
            # 1. 손절 체크 (7거래일 유예 로직 포함)
            if curr_price <= pos['stop_loss']:
                pos['consecutive_down_days'] += 1
                if pos['consecutive_down_days'] >= config.FORGIVENESS_COUNT:
                    self.sell_stock(code, pos['quantity'], "손절 (유유 기간 만료)")
            else:
                pos['consecutive_down_days'] = 0
                
            # 2. 익절 체크
            profit_rate = (curr_price - pos['avg_price']) / pos['avg_price']
            if profit_rate >= config.TAKE_PROFIT_FULL:
                self.sell_stock(code, pos['quantity'], "전량 익절")
            elif profit_rate >= config.TAKE_PROFIT_HALF:
                # 절반 매도 로직 (단, 이미 절반 매도했는지 체크 필요)
                pass

    def sell_stock(self, code, qty, reason):
        """매도 실행 주체"""
        res = order_cash(
            env_dv="demo" if config.IS_PAPER_TRADING else "real",
            ord_dv="sell",
            cano=self.trenv.my_acct,
            acnt_prdt_cd=self.trenv.my_prod,
            pdno=code,
            ord_dvsn="01", # 시장가
            ord_qty=str(qty),
            ord_unpr="0",
            excg_id_dvsn_cd="KRX"
        )
        if not res.empty:
            self.notifier.send_message(f"🚨 매도 실행: {code} / 사유: {reason}")
            if code in self.positions: del self.positions[code]

    # --------------------------------------------------------------------------
    # Step 5: 종료 전략 (15:00)
    # --------------------------------------------------------------------------
    def closing_strategy(self):
        """Step 5: 장 마감 전 현금 비중 확인 및 부족 시 수익률 하위 종목 매도"""
        logger.info("Step 5: 종료 전략(현금 비중 확보) 시작")
        
        # 1. 현재 잔고 재조회
        self.sync_positions()
        
        # 2. 현재 총 자산 및 현금 확인 (간략화된 계산)
        total_value = config.TOTAL_CAPITAL # 실제로는 계좌 조회를 통해 얻어야 함
        current_cash = total_value - sum([p['avg_price'] * p['quantity'] for p in self.positions.values()])
        required_cash = total_value * config.CASH_BUFFER_RATIO
        
        if current_cash < required_cash:
            shortfall = required_cash - current_cash
            logger.info(f"현금 부족액: {shortfall:,.0f}원. 종목 매도를 시작합니다.")
            
            # 3. 수익률 하위 순으로 정렬
            sorted_positions = sorted(self.positions.items(), 
                                      key=lambda x: (inquire_price(env_dv="demo" if config.IS_PAPER_TRADING else "real", 
                                                                  fid_cond_mrkt_div_code="J", fid_input_iscd=x[0]).iloc[0]['stck_prpr'].astype(float) - x[1]['avg_price']) / x[1]['avg_price'])
            
            for code, pos in sorted_positions:
                if current_cash >= required_cash:
                    break
                
                # 전량 매도
                self.sell_stock(code, pos['quantity'], "종료 전략 (현금 비중 확보)")
                # (매도 후 current_cash 업데이트 로직 필요)
        else:
            logger.info("현금 비중이 충분합니다. 추가 매도 없이 장을 마감합니다.")

    # --------------------------------------------------------------------------
    # Step 6: 일일 리포트 (15:30)
    # --------------------------------------------------------------------------
    def finalize_day(self):
        """Step 6: 하루 마무리 및 리포트 발송"""
        logger.info("Step 6: 일일 리포트 생성 및 전송")
        # 자산 현황 조회 및 리포트 전송
        report = f"📊 오늘의 일일 리포트\n- 현재 보유 종목: {len(self.positions)}개\n- 트레이딩 모드: {self.trading_mode}"
        self.notifier.send_message(report)

    # --------------------------------------------------------------------------
    # 메인 엔진 루프
    # --------------------------------------------------------------------------
    def run(self):
        """시스템 실행 루프 (정해진 시간에 루틴 실행)"""
        logger.info("AI Quant 자동매매 시스템이 시작되었습니다.")
        self.notifier.send_message("🚀 시스템 가동 시작")
        
        # 스케줄 등록
        schedule.every().day.at("08:20").do(self.initialize_routine)
        schedule.every().day.at("08:20").do(self.analyze_macro_environment)
        schedule.every().day.at("08:30").do(self.select_top_stocks)
        # 매수 실행은 장 시작 후 (09:00)
        schedule.every().day.at("09:00").do(lambda: self.execute_buying(getattr(self, 'selected_stocks', [])))
        # Step 5: 종료 전략 (15:00)
        schedule.every().day.at("15:00").do(self.closing_strategy)
        # Step 6: 종료 및 리포트 (15:30)
        schedule.every().day.at("15:30").do(self.finalize_day)
        
        while self.is_running:
            try:
                # 09:00 ~ 15:00 사이에는 실시간 모니터링 실행
                now = datetime.datetime.now()
                if 9 <= now.hour < 15:
                    if now.minute % 10 == 0: # 10분마다 실행
                        self.monitoring_routine()
                        time.sleep(60) # 중복 실행 방지
                
                schedule.run_pending()
                time.sleep(1)
                
            except KeyboardInterrupt:
                logger.info("사용자에 의해 시스템이 종료됩니다.")
                self.is_running = False
            except Exception as e:
                logger.error(f"루프 실행 중 예외 발생: {e}")
                time.sleep(60)

if __name__ == "__main__":
    manager = QuantManager()
    manager.run()
