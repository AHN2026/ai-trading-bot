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
from utils import send_telegram_msg, save_json, load_json, setup_logger
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
        # Notifier is removed, using utils.send_telegram_msg directly
        self.risk_manager = RiskManager(config.STOP_LOSS_RATE, config.MAX_POSITIONS)
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
        """Step 1: VIX, NASDAQ 지수, 환율, 유가 등을 분석하여 투자 강도 결정 (08:20 실행)"""
        logger.info("Step 1: 거시경제 분석 시작")
        
        try:
            # 1. 데이터 수집
            # VIX(^VIX), NASDAQ(^IXIC), 환율(KRW=X), 유가(CL=F)
            tickers = yf.Tickers("^VIX ^IXIC KRW=X CL=F")
            
            # VIX (마지막 종가)
            vix = tickers.tickers['^VIX'].history(period="1d")['Close'].iloc[-1]
            
            # NASDAQ (최근 2일 변동률)
            nasdaq_hist = tickers.tickers['^IXIC'].history(period="2d")
            nasdaq_change = ((nasdaq_hist['Close'].iloc[-1] - nasdaq_hist['Close'].iloc[-2]) 
                             / nasdaq_hist['Close'].iloc[-2] * 100)
            
            # 환율 (최근 2일 변동률)
            usdkrw_hist = tickers.tickers['KRW=X'].history(period="2d")
            usdkrw_change = ((usdkrw_hist['Close'].iloc[-1] - usdkrw_hist['Close'].iloc[-2]) 
                             / usdkrw_hist['Close'].iloc[-2] * 100)
            
            # 유가 (최근 2일 변동률)
            oil_hist = tickers.tickers['CL=F'].history(period="2d")
            oil_change = ((oil_hist['Close'].iloc[-1] - oil_hist['Close'].iloc[-2]) 
                             / oil_hist['Close'].iloc[-2] * 100)

            # 2. 트레이딩 모드 결정 로직 (방어적 조건 우선)
            # 조건 1: 매우 방어적인 상황
            is_defensive = (
                vix >= config.VIX_STORMY_THRESHOLD or 
                nasdaq_change <= -2.0 or 
                usdkrw_change >= 2.0 or 
                abs(oil_change) >= 7.0
            )
            
            # 조건 2: 약간 방어적인 상황
            is_moderate = (
                vix >= config.VIX_CLOUDY_THRESHOLD or 
                nasdaq_change <= -1.0 or 
                usdkrw_change >= 1.0 or 
                abs(oil_change) >= 5.0
            )

            # 새 모드 산출
            new_mode = "AGGRESSIVE"
            new_max_stocks = 10
            
            if is_defensive:
                new_mode = "DEFENSIVE"
                new_max_stocks = 0
            elif is_moderate:
                new_mode = "MODERATE"
                new_max_stocks = 5

            # 3. One-Way Downgrade 규칙 적용 (모드는 하향만 가능하고 상향 불가능)
            mode_rank = {"AGGRESSIVE": 3, "MODERATE": 2, "DEFENSIVE": 1}
            current_rank = mode_rank.get(self.trading_mode, 3)
            new_rank = mode_rank.get(new_mode, 3)
            
            if new_rank < current_rank:
                self.trading_mode = new_mode
                self.max_stocks = new_max_stocks
            
            msg = (f"📈 거시경제 분석 결과\n"
                   f"- VIX: {vix:.2f}\n"
                   f"- NASDAQ: {nasdaq_change:+.2f}%\n"
                   f"- USD/KRW: {usdkrw_change:+.2f}%\n"
                   f"- WTI Oil: {oil_change:+.2f}%\n"
                   f"- 최종 모드: <b>{self.trading_mode}</b> (최대 {self.max_stocks}종목 매수)")
                   
            logger.info(msg.replace("<b>", "").replace("</b>", ""))
            send_telegram_msg(msg)
            
        except Exception as e:
            logger.error(f"거시경제 분석 중 오류 발생 (방어적 대응 - MODERATE 설정): {e}")
            # 안전을 위해 모드 하향 조정
            if self.trading_mode == "AGGRESSIVE":
                self.trading_mode = "MODERATE"
                self.max_stocks = 5
            send_telegram_msg(f"⚠️ 거시경제 데이터 수집 실패. 현재 모드 유지: {self.trading_mode}")

    # --------------------------------------------------------------------------
    # Step 2: 종목 선정 (Gemini AI 분석)
    # --------------------------------------------------------------------------
    def select_top_stocks(self):
        """Step 2: AI 분석을 통해 매수할 종목 선정 (08:30 실행)"""
        if self.max_stocks == 0:
            logger.info("시장 상태가 DEFENSIVE이므로 종목 선정을 건너뜁니다.")
            send_telegram_msg("🛡️ 시장 상태 방어 모드로 인해 신규 종목 선정을 진행하지 않습니다.")
            self.selected_stocks = []
            return []
            
        logger.info("Step 2: 종목 분석 및 선정 시작")
        send_telegram_msg(f"🔍 AI 종목 분석을 시작합니다. (대상: {len(config.UNIVERSE)}개 종목)")
        
        recommendations = []
        for item in config.UNIVERSE:
            code = item['code']
            name = item['name']
            
            # 이미 보유 중인 종목은 개별 모니터링 로직에서 관리되므로 신규 평가를 건너뛸지 여부 (일단은 평가 포함)
            
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
                
                위 데이터를 바탕으로 오늘 기준 이 주식을 매수하는 것이 좋을지 점수를 매겨줘.
                점수 기준 (100점 만점):
                - 최근 뉴스 및 모멘텀 호재성 점수 (30점)
                - 가격 추세 및 기술적 분석 점수 (40점)
                - 진입 시점 대비 손익비 점수 (30점)
                
                응답은 반드시 아래 JSON 형식으로만 반환해줘. 그 외 설명은 붙이지 마.
                {{"score": 85, "reason": "반도체 업황 개선 기대감...", "target_price": 85000, "stop_loss": 72000}}
                """
                
                response = self.model.generate_content(prompt)
                
                # 역슬래시나 코드블록 태그 제거 오류 대비 방어코드 강화
                res_text = response.text.replace("```json", "").replace("```", "").strip()
                # 첫 번째와 마지막 중괄호 내부만 추출 시도 
                start_idx = res_text.find('{')
                end_idx = res_text.rfind('}')
                
                if start_idx != -1 and end_idx != -1:
                    clean_json_str = res_text[start_idx:end_idx+1]
                    analysis = json.loads(clean_json_str)
                    score = int(analysis.get("score", 0))
                    
                    # 동적 최소 점수 (적중률 기반)
                    dynamic_min_score = self.tracker.get_dynamic_min_score()
                    
                    if score >= dynamic_min_score:
                        logger.info(f"[{name}] API 분석 점수: {score} (기준: {dynamic_min_score}) - 매수 대상 선정")
                        recommendations.append({
                            "code": code,
                            "name": name,
                            "score": score,
                            "analysis": analysis
                        })
                    else:
                        logger.info(f"[{name}] API 분석 점수: {score} - 점수 미달 제외")
                else:
                    logger.warning(f"[{name}] 올바른 JSON 형식이 반환되지 않았습니다. {res_text}")
                    
            except Exception as e:
                logger.error(f"[{name}] 분석 처리 중 오류 발생: {e}")
                time.sleep(2) # 연속 API 오류 방지를 위한 약간의 대기
                
        # 점수 순 정렬 후 모드별 최대 개수만큼 선정
        recommendations.sort(key=lambda x: x['score'], reverse=True)
        self.selected_stocks = recommendations[:self.max_stocks]
        
        if self.selected_stocks:
            names = ", ".join([f"{s['name']}({s['score']}점)" for s in self.selected_stocks])
            send_telegram_msg(f"✅ 오늘의 매수 종목 선정 완료: {names}")
        else:
            send_telegram_msg("⚠️ 분석 결과 매수 조건(최소 점수)을 만족하는 종목이 없습니다.")
            
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
        """Step 3: 선정된 종목 매수 실행 (09:00 장 시작 직후)"""
        if not selected_stocks:
            logger.info("Step 3: 매수할 종목이 없어 패스합니다.")
            return

        logger.info("Step 3: 매수 프로세스 시작")
        for stock in selected_stocks:
            code = stock['code']
            
            if code in self.positions: 
                logger.info(f"[{stock['name']}] 이미 보유 중이므로 신규 진입 생략")
                continue
            
            if self.risk_manager.can_buy_more(len(self.positions)):
                
                # 현재 가격 1주 조회 (시장가 매수라도 수량을 구하기 위해 조회가 필요)
                try:
                    price_df = inquire_price(env_dv="demo" if config.IS_PAPER_TRADING else "real", 
                                             fid_cond_mrkt_div_code="J", fid_input_iscd=code)
                    curr_price = float(price_df.iloc[0]['stck_prpr'])
                except Exception as e:
                    logger.error(f"[{stock['name']}] 현재가 조회 실패. 매수 스킵: {e}")
                    continue
                
                # 주문 금액 및 수량 계산 (1단계 피라미딩 비중 적용)
                max_order_amount = self.risk_manager.get_max_order_amount(config.TOTAL_CAPITAL, config.CASH_BUFFER_RATIO, config.MAX_PER_STOCK_RATIO)
                target_amount = max_order_amount * config.PYRAMID_STAGE_1
                
                order_qty = int(target_amount // curr_price)
                if order_qty <= 0:
                    logger.warning(f"[{stock['name']}] 주문 가능 수량이 0주입니다. (주가: {curr_price}, 예산: {target_amount})")
                    continue
                
                # 실제 주문 실행 (시장가 '01')
                logger.info(f"[{stock['name']}] 시장가 매수 주문 요청 (수량: {order_qty}주)")
                
                success = False
                for attempt in range(3): # 최대 3회 재시도
                    try:
                        res = order_cash(
                            env_dv="demo" if config.IS_PAPER_TRADING else "real",
                            ord_dv="buy",
                            cano=self.trenv.my_acct,
                            acnt_prdt_cd=self.trenv.my_prod,
                            pdno=code,
                            ord_dvsn="01", # 시장가
                            ord_qty=str(order_qty),
                            ord_unpr="0",
                            excg_id_dvsn_cd="KRX"
                        )
                        if not res.empty and res.iloc[0].get('rt_cd') == '0':
                            success = True
                            
                            # 포지션 데이터 바로 등록 (정확한 단가는 동기화 시 잡히겠지만, 우선 가승인 처리)
                            self.positions[code] = {
                                "name": stock['name'],
                                "quantity": order_qty,
                                "avg_price": curr_price,
                                "stop_loss": curr_price * (1 + config.STOP_LOSS_RATE),
                                "target_price": curr_price * (1 + config.TAKE_PROFIT_FULL),
                                "pyramid_stage": 1, 
                                "highest_price": curr_price,
                                "consecutive_down_days": 0
                            }
                            
                            send_telegram_msg(
                                f"✅ <b>신규 매수 성공</b>\n"
                                f"종목: {stock['name']}({code})\n"
                                f"수량: {order_qty}주\n"
                                f"예상단가: ₩{curr_price:,.0f}\n"
                                f"매수이유: {stock['analysis'].get('reason', 'AI 선정')}"
                            )
                            time.sleep(1) # 연속 주문 시 부하 방지
                            break
                        else:
                            logger.warning(f"[{stock['name']}] 매수 주문 응답 비정상: {res}")
                    except Exception as e:
                        logger.error(f"[{stock['name']}] 매수 시도 {attempt+1}회차 실패: {e}")
                        time.sleep(2)
                
                if not success:
                    logger.error(f"[{stock['name']}] 3회 재시도에도 매수 실패")

    # --------------------------------------------------------------------------
    # Step 4: 실시간 모니터링 (장중 반복 실행)
    # --------------------------------------------------------------------------
    def monitoring_routine(self):
        """Step 4: 매 10분마다 보유 종목 상태 체크 (손절/익절/피라미딩/트레일링스탑)"""
        logger.info("Step 4: 장중 실시간 상태 모니터링 중...")
        codes_to_remove = []
        codes_to_pyramid = []
        
        for code, pos in self.positions.items():
            try:
                # 현재가 조회
                price_df = inquire_price(env_dv="demo" if config.IS_PAPER_TRADING else "real", 
                                         fid_cond_mrkt_div_code="J", fid_input_iscd=code)
                curr_price = float(price_df.iloc[0]['stck_prpr'])
                
                # 최고가 갱신 로직 (트레일링 스탑용)
                if curr_price > pos['highest_price']:
                    pos['highest_price'] = curr_price
                    
                profit_rate = (curr_price - pos['avg_price']) / pos['avg_price']
                
                # 1. 트레일링 스탑 체크 (고점 대비 -2% 하락)
                trailing_stop_price = pos['highest_price'] * 0.98
                if curr_price <= trailing_stop_price and profit_rate > 0.02: 
                    # 최소 2% 이상 익절 구간에서만 트레일링 스탑 가동
                    logger.info(f"[{pos['name']}] 고점 이탈 트레일링 스탑 조건 만족")
                    if self.sell_stock(code, pos['quantity'], f"트레일링 스탑(고점: {pos['highest_price']}원)"):
                        codes_to_remove.append(code)
                    continue

                # 2. 손절 체크 (7거래일 유예 로직 - Tit-for-Two-Tat 변형)
                if curr_price <= pos['stop_loss']:
                    # 만약 시스템이 매일 1회만 카운트한다면 이 위치에 횟수 누적은 너무 빠를 수 있음
                    # 10분마다 실행되므로 여기서는 당일 하락 확인만 하고, 실제 카운팅은 장 마감 시에 하는 것이 정석
                    # 지금은 즉각 대응으로 구현 (다만 카운트 룰을 엄격하게 제한)
                    if not pos.get("loss_triggered_today"):
                        pos['consecutive_down_days'] += 1
                        pos['loss_triggered_today'] = True # 오늘 한 번만 카운트
                        
                    if pos['consecutive_down_days'] >= config.FORGIVENESS_COUNT:
                        if self.sell_stock(code, pos['quantity'], "손절 (유예 기간 만료)"):
                            codes_to_remove.append(code)
                        continue
                else:
                    pos['consecutive_down_days'] = 0 # 회복 시 카운트 리셋
                    
                # 3. 익절 체크
                if profit_rate >= config.TAKE_PROFIT_FULL:
                    if self.sell_stock(code, pos['quantity'], f"전량 익절 (+{profit_rate*100:.2f}%)"):
                        codes_to_remove.append(code)
                    continue
                elif profit_rate >= config.TAKE_PROFIT_HALF and not pos.get('half_sold'):
                    sell_qty = pos['quantity'] // 2
                    if sell_qty > 0:
                        if self.sell_stock(code, sell_qty, f"1차 절반 익절 (+{profit_rate*100:.2f}%)"):
                            pos['quantity'] -= sell_qty
                            pos['half_sold'] = True
                    # 절반 매도 후엔 계속 보유하므로 continue 하지 않음

                # 4. 피라미딩(추가 매수) 룰 확인
                # 2단계 피라미딩 조건 (+2% 이상 & 1단계인 경우)
                if profit_rate >= config.PYRAMID_TRIGGER_2 and pos['pyramid_stage'] == 1:
                    logger.info(f"[{pos['name']}] 피라미딩 2단계 진입 조건 달성")
                    codes_to_pyramid.append((code, 2))
                # 3단계 피라미딩 조건 (+4% 이상 & 2단계인 경우)
                elif profit_rate >= config.PYRAMID_TRIGGER_3 and pos['pyramid_stage'] == 2:
                    logger.info(f"[{pos['name']}] 피라미딩 3단계 진입 조건 달성")
                    codes_to_pyramid.append((code, 3))

            except Exception as e:
                logger.error(f"[{pos['name']}] 모니터링 중 에러 발생: {e}")
                
        # 포지션 삭제 안전 제거
        for code in codes_to_remove:
            if code in self.positions:
                del self.positions[code]
                
        # 피라미딩 추가매수 실행
        for code, stage in codes_to_pyramid:
            self.execute_pyramiding(code, stage)

    def sell_stock(self, code, qty, reason):
        """매도 실행 주체 (성공 여부 True/False 리턴)"""
        stock_name = self.positions.get(code, {}).get('name', code)
        success = False
        
        for attempt in range(3):
            try:
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
                if not res.empty and res.iloc[0].get('rt_cd') == '0':
                    success = True
                    send_telegram_msg(f"🚨 <b>매도 완료</b>\n종목: {stock_name}\n사유: {reason}\n매도수량: {qty}주")
                    time.sleep(1)
                    break
                else:
                    logger.warning(f"[{stock_name}] 매도 응답 비정상: {res}")
            except Exception as e:
                logger.error(f"[{stock_name}] 매도 시도 {attempt+1}회차 에러: {e}")
                time.sleep(2)
                
        return success

    def execute_pyramiding(self, code, target_stage):
        """피라미딩(추가 매수) 수행"""
        pos = self.positions.get(code)
        if not pos: return
        
        try:
            price_df = inquire_price(env_dv="demo" if config.IS_PAPER_TRADING else "real", 
                                     fid_cond_mrkt_div_code="J", fid_input_iscd=code)
            curr_price = float(price_df.iloc[0]['stck_prpr'])
            
            # 피라미딩 비율 설정 (2단계 30%, 3단계 40%)
            stage_ratio = config.PYRAMID_STAGE_2 if target_stage == 2 else config.PYRAMID_STAGE_3
            
            # 총 예산에서 추가 투입할 금액 계산
            max_order_amount = self.risk_manager.get_max_order_amount(config.TOTAL_CAPITAL, config.CASH_BUFFER_RATIO, config.MAX_PER_STOCK_RATIO)
            target_amount = max_order_amount * stage_ratio
            
            order_qty = int(target_amount // curr_price)
            if order_qty <= 0: return
            
            # 시장가 매수
            res = order_cash(
                env_dv="demo" if config.IS_PAPER_TRADING else "real",
                ord_dv="buy", cano=self.trenv.my_acct, acnt_prdt_cd=self.trenv.my_prod,
                pdno=code, ord_dvsn="01", ord_qty=str(order_qty), ord_unpr="0", excg_id_dvsn_cd="KRX"
            )
            
            if not res.empty and res.iloc[0].get('rt_cd') == '0':
                # 평단가 물타기 재계산
                old_qty = pos['quantity']
                old_val = old_qty * pos['avg_price']
                new_val = order_qty * curr_price
                
                pos['quantity'] += order_qty
                pos['avg_price'] = (old_val + new_val) / pos['quantity']
                pos['pyramid_stage'] = target_stage
                
                # 손익 목표가/손절가 리셋(선택사항) 유지
                
                send_telegram_msg(f"🔥 <b>불타기(피라미딩 {target_stage}단계) 성공</b>\n종목: {pos['name']}\n추가수량: {order_qty}주")
            
        except Exception as e:
            logger.error(f"[{pos['name']}] 피라미딩 갱신 에러: {e}")

    # --------------------------------------------------------------------------
    # Step 5: 종료 전략 (15:00)
    # --------------------------------------------------------------------------
    def closing_strategy(self):
        """Step 5: 장 마감 전 현금 비중 확인 및 부족 시 수익률 하위 종목 매도"""
        logger.info("Step 5: 종료 전략(현금 비중 확보) 시작")
        
        # 1. 현재 잔고 재조회 (동기화)
        self.sync_positions()
        
        # 2. 현재 총 자산 및 잔고 현금 계산
        try:
             res1, _ = inquire_balance(
                env_dv="demo" if config.IS_PAPER_TRADING else "real",
                cano=self.trenv.my_acct, acnt_prdt_cd=self.trenv.my_prod, prcs_dvsn="00"
             )
             current_cash = float(res1.iloc[0]['prvs_rcdl_excc_amt']) # 예수금
             stock_value = float(res1.iloc[0]['scts_evlu_amt'])       # 주식평가금액
             total_value = current_cash + stock_value                 # 총 평가금액
             
             # 총 자산 정보를 인스턴스에 저장 (리포트 작성용)
             self._daily_total_value = total_value
        except Exception as e:
             logger.error(f"잔고 조회 실패로 종료 전략 우회: {e}")
             return
             
        # 최소 확보 현금
        required_cash = total_value * config.CASH_BUFFER_RATIO
        
        if current_cash < required_cash:
            shortfall = required_cash - current_cash
            logger.info(f"현금 부족액: {shortfall:,.0f}원. 수익률 하위 종목 매도를 시작합니다.")
            
            # 3. 수익률 계산 및 하위 순 정렬
            profit_list = []
            for code, pos in self.positions.items():
                try:
                    price_df = inquire_price(env_dv="demo" if config.IS_PAPER_TRADING else "real", 
                                             fid_cond_mrkt_div_code="J", fid_input_iscd=code)
                    curr_price = float(price_df.iloc[0]['stck_prpr'])
                    profit = (curr_price - pos['avg_price']) / pos['avg_price']
                    
                    cur_value = curr_price * pos['quantity']
                    profit_list.append({
                        "code": code, "name": pos['name'], "profit": profit, 
                        "value": cur_value, "qty": pos['quantity']
                    })
                except:
                    continue
                    
            profit_list.sort(key=lambda x: x['profit']) # 수익률 오름차순 (하위가 제일 먼저)
            
            secured_cash = 0
            codes_to_remove = []
            
            for item in profit_list:
                if (current_cash + secured_cash) >= required_cash:
                    break
                    
                code = item['code']
                qty = item['qty']
                
                # 전량 매도
                if self.sell_stock(code, qty, "종료 전략 (현금 비중 확보, 오버나이트 리스크 축소)"):
                    secured_cash += item['value']
                    codes_to_remove.append(code)
            
            for code in codes_to_remove:
                if code in self.positions: del self.positions[code]
                    
        else:
            logger.info(f"현금 비중이 충분합니다. ({current_cash/total_value*100:.1f}%) 추가 매도 없이 장을 마감합니다.")

        # 자정에 초기화할 1일 변수 리셋
        for pos in self.positions.values():
            pos['loss_triggered_today'] = False

    # --------------------------------------------------------------------------
    # Step 6: 일일 리포트 (15:30)
    # --------------------------------------------------------------------------
    def finalize_day(self):
        """Step 6: 하루 마무리 및 리포트 발송"""
        logger.info("Step 6: 일일 리포트 생성 및 전송")
        
        total_value = getattr(self, '_daily_total_value', config.TOTAL_CAPITAL)
        
        report = (
            f"📊 <b>오늘의 AI 트레이딩 리포트</b>\n\n"
            f"{self.tracker.get_stats_summary()}\n"
            f"🔹 <b>현재 시장 모드:</b> {self.trading_mode}\n"
            f"🔹 <b>총 보유 종목:</b> {len(self.positions)}개 종목 오버나이트\n"
            f"🔹 <b>추정 총 자산:</b> ₩{total_value:,.0f}\n\n"
        )
        
        if self.positions:
            report += "<b>[ 보유 종목 리스트 ]</b>\n"
            for code, pos in self.positions.items():
                report += f"- {pos['name']} (평단가 ₩{pos['avg_price']:,.0f}, 수량 {pos['quantity']}주, 피라미딩 {pos['pyramid_stage']}단계)\n"
        else:
            report += "보유 종목 없이 모든 포지션을 청산했습니다.\n"
            
        report += "\n내일 아침 다시 활동을 시작합니다!"
        send_telegram_msg(report)
        logger.info("일일 업무를 마치고 다음 날까지 대기 모드에 들어갑니다.")

    # --------------------------------------------------------------------------
    # 메인 엔진 루프
    # --------------------------------------------------------------------------
    def run(self):
        """시스템 실행 루프 (정해진 시간에 루틴 실행)"""
        logger.info("AI Quant 자동매매 시스템이 시작되었습니다.")
        send_telegram_msg("🚀 시스템 가동 시작")
        
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
