import codecs

with codecs.open('ai_quant_manager.py', 'r', 'utf-8') as f:
    text = f.read()

# 1. Imports
text = text.replace('from utils import TelegramNotifier, save_log, save_data, load_data', 'from utils import send_telegram_msg, save_json, load_json, setup_logger')

text = text.replace('''logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(config.LOG_DIR, "trading.log"), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)''', 'logger = setup_logger(__name__)')

# 2. Initialization modifications
text = text.replace('        self.notifier = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)\n', '')
text = text.replace('self.risk_manager = RiskManager(config.STOP_LOSS_RATE, config.MAX_STOCK_COUNT)', 'self.risk_manager = RiskManager(config.STOP_LOSS_RATE, config.MAX_POSITIONS)')

# 3. Message sending
text = text.replace('self.notifier.send_message', 'send_telegram_msg')

# 4. execute_buying 6-stage check
old_exec = '''            if self.risk_manager.can_buy_more(len(self.positions)):
                
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
                target_amount = max_order_amount * config.PYRAMID_STAGE_1'''
                
new_exec = '''            # 현재 가격 1주 조회
            try:
                price_df = inquire_price(env_dv="demo" if config.IS_PAPER_TRADING else "real", 
                                         fid_cond_mrkt_div_code="J", fid_input_iscd=code)
                curr_price = float(price_df.iloc[0]['stck_prpr'])
            except Exception as e:
                logger.error(f"[{stock['name']}] 현재가 조회 실패. 매수 스킵: {e}")
                continue
                
            # 리스크 6단계 매수 체크 및 예산 산출
            max_order_amount = self.risk_manager.get_max_order_amount(config.TOTAL_CAPITAL, config.CASH_BUFFER_RATIO, config.MAX_PER_STOCK_RATIO)
            target_amount = max_order_amount * config.PYRAMID_STAGE_1
            
            sector = next((item['sector'] for item in config.UNIVERSE if item['code'] == code), "기타")
            can_buy, reason = self.risk_manager.can_buy(code, target_amount, sector, self.positions, self.trading_mode)
            
            if can_buy:'''
text = text.replace(old_exec, new_exec)

# 5. execute_buying record_trade
old_rec = """                            send_telegram_msg(
                                f"✅ <b>신규 매수 성공</b>\\n"
                                f"종목: {stock['name']}({code})\\n"
                                f"수량: {order_qty}주\\n"
                                f"예상단가: ₩{curr_price:,.0f}\\n"
                                f"매수이유: {stock['analysis'].get('reason', 'AI 선정')}"
                            )
                            time.sleep(1)"""
                            
new_rec = """                            send_telegram_msg(
                                f"✅ <b>신규 매수 성공</b>\\n"
                                f"종목: {stock['name']}({code})\\n"
                                f"수량: {order_qty}주\\n"
                                f"예상단가: ₩{curr_price:,.0f}\\n"
                                f"매수이유: {stock['analysis'].get('reason', 'AI 선정')}"
                            )
                            self.risk_manager.record_trade(0, 'BUY')
                            time.sleep(1)"""
text = text.replace(old_rec, new_rec)

# 6. Pyramiding check
old_pyr = '''                # 4. 피라미딩(추가 매수) 룰 확인
                # 2단계 피라미딩 조건 (+2% 이상 & 1단계인 경우)
                if profit_rate >= config.PYRAMID_TRIGGER_2 and pos['pyramid_stage'] == 1:
                    logger.info(f"[{pos['name']}] 피라미딩 2단계 진입 조건 달성")
                    codes_to_pyramid.append((code, 2))
                # 3단계 피라미딩 조건 (+4% 이상 & 2단계인 경우)
                elif profit_rate >= config.PYRAMID_TRIGGER_3 and pos['pyramid_stage'] == 2:
                    logger.info(f"[{pos['name']}] 피라미딩 3단계 진입 조건 달성")
                    codes_to_pyramid.append((code, 3))'''
                    
new_pyr = '''                # 4. 피라미딩(추가 매수) 룰 확인
                can_pyr, next_stage, pyr_reason = self.risk_manager.can_pyramid(code, pos['pyramid_stage'], profit_rate, self.positions)
                if can_pyr:
                    logger.info(f"[{pos['name']}] 피라미딩 {next_stage}단계 인가: {pyr_reason}")
                    codes_to_pyramid.append((code, next_stage))'''
text = text.replace(old_pyr, new_pyr)

# 7. Reset counters in init routine
old_init = '''        # 2. 거래일 확인
        today = datetime.datetime.now().strftime("%Y%m%d")'''
new_init = '''        # 서킷브레이커 및 일일 카운터 리셋
        self.risk_manager.reset_daily_counters()
        
        # 2. 거래일 확인
        today = datetime.datetime.now().strftime("%Y%m%d")'''
text = text.replace(old_init, new_init)

# 8. Circuit breaker
old_mon = '''        logger.info("Step 4: 장중 실시간 상태 모니터링 중...")
        codes_to_remove = []'''
new_mon = '''        logger.info("Step 4: 장중 실시간 상태 모니터링 중...")
        
        if self.risk_manager.check_daily_circuit_breaker():
            logger.error("서킷브레이커 발동. 포지션 강제 점검 필요.")
            return

        codes_to_remove = []'''
text = text.replace(old_mon, new_mon)

# 9. P&L tracking
old_sell = '''                    send_telegram_msg(f"🚨 <b>매도 완료</b>\\n종목: {stock_name}\\n사유: {reason}\\n매도수량: {qty}주")
                    time.sleep(1)
                    break'''
new_sell = '''                    send_telegram_msg(f"🚨 <b>매도 완료</b>\\n종목: {stock_name}\\n사유: {reason}\\n매도수량: {qty}주")
                    # 실현 손익 기록
                    pos = self.positions.get(code)
                    if pos:
                        try:
                            # market_price=0 for market order in kis, but we assume current price as rough estimate
                            price_df = inquire_price(env_dv="demo" if config.IS_PAPER_TRADING else "real", fid_cond_mrkt_div_code="J", fid_input_iscd=code)
                            sell_price = float(price_df.iloc[0]['stck_prpr'])
                            real_pnl = self.risk_manager.calculate_real_profit(pos['avg_price'], sell_price, qty)
                            self.risk_manager.record_trade(real_pnl, 'SELL')
                        except Exception as pnl_err:
                            logger.error(f"실현 손익 계산 오류: {pnl_err}")
                    time.sleep(1)
                    break'''
text = text.replace(old_sell, new_sell)


with codecs.open('ai_quant_manager.py', 'w', 'utf-8') as f:
    f.write(text)
