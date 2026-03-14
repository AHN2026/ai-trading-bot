import codecs

with open('ai_quant_manager.py', 'r', encoding='utf-8') as f:
    text = f.read()

# 1. Step 0: update_results 호출 및 브로커 넘기기
old_init_rt = '''        # 서킷브레이커 및 일일 카운터 리셋
        self.risk_manager.reset_daily_counters()
        
        # 2. 거래일 확인'''
new_init_rt = '''        # 서킷브레이커 및 일일 카운터 리셋
        self.risk_manager.reset_daily_counters()
        
        # AI 적중률 업데이트 (5일 경과 종목 평가)
        def get_current_price(code):
            try:
                price_df = inquire_price(env_dv="demo" if config.IS_PAPER_TRADING else "real", 
                                         fid_cond_mrkt_div_code="J", fid_input_iscd=code)
                return float(price_df.iloc[0]['stck_prpr'])
            except:
                return 0
        
        updated = self.tracker.update_results(broker=get_current_price)
        if updated > 0:
            logger.info(f"AI 적중률 업데이트 완료: {updated}건 평가됨.")
        
        # 2. 거래일 확인'''
text = text.replace(old_init_rt, new_init_rt)

# 2. Step 2: 동적 점수 적용
old_ai_score = '''                    if score >= config.MIN_AI_SCORE:
                        logger.info(f"[{name}] API 분석 점수: {score} - 매수 대상 선정")'''
new_ai_score = '''                    # 동적 최소 점수 (적중률 기반)
                    dynamic_min_score = self.tracker.get_dynamic_min_score()
                    
                    if score >= dynamic_min_score:
                        logger.info(f"[{name}] API 분석 점수: {score} (기준: {dynamic_min_score}) - 매수 대상 선정")'''
text = text.replace(old_ai_score, new_ai_score)

# 3. Step 3: 동적 비중 산출 및 추천 종목 기록
old_exec_buy = '''            # 리스크 6단계 매수 체크 및 예산 산출
            max_order_amount = self.risk_manager.get_max_order_amount(config.TOTAL_CAPITAL, config.CASH_BUFFER_RATIO, config.MAX_PER_STOCK_RATIO)
            target_amount = max_order_amount * config.PYRAMID_STAGE_1
            
            sector = next((item['sector'] for item in config.UNIVERSE if item['code'] == code), "기타")
            can_buy, reason = self.risk_manager.can_buy(code, target_amount, sector, self.positions, self.trading_mode)
            
            if can_buy:'''
new_exec_buy = '''            # 리스크 6단계 매수 체크 및 예산 산출
            dynamic_ratio = self.tracker.get_dynamic_position_size()
            max_order_amount = self.risk_manager.get_max_order_amount(config.TOTAL_CAPITAL, config.CASH_BUFFER_RATIO, config.MAX_PER_STOCK_RATIO)
            
            # 동적 비중 적용
            target_amount = config.TOTAL_CAPITAL * dynamic_ratio
            
            sector = next((item['sector'] for item in config.UNIVERSE if item['code'] == code), "기타")
            can_buy, reason = self.risk_manager.can_buy(code, target_amount, sector, self.positions, self.trading_mode)
            
            if can_buy:
                # 주문 전 AI 예측 기록 (장기 성과 추적용)
                self.tracker.record_prediction(code, stock['name'], curr_price, stock['score'])'''
text = text.replace(old_exec_buy, new_exec_buy)

# 4. Step 6: 텔레그램 리포트에 적중률 포함
old_report = '''        report = (
            f"📊 <b>오늘의 AI 트레이딩 리포트</b>\\n\\n"
            f"🔹 <b>현재 시장 모드:</b> {self.trading_mode}\\n"'''
new_report = '''        report = (
            f"📊 <b>오늘의 AI 트레이딩 리포트</b>\\n\\n"
            f"{self.tracker.get_stats_summary()}\\n"
            f"🔹 <b>현재 시장 모드:</b> {self.trading_mode}\\n"'''
text = text.replace(old_report, new_report)

with open('ai_quant_manager.py', 'w', encoding='utf-8') as f:
    f.write(text)
