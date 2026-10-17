import asyncio
from datetime import datetime
import logging
from pathlib import Path
import sys

# 프로젝트 루트 경로 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.executor import SqlPaperExecutor
from core.ledger import SqliteLedger
from core.models import TickData
from core.orchestrator import TradingOrchestrator
from broker.kis_client import KisClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Simulator")


async def run_simulation():
    logger.info("🎬 [시뮬레이터] The Stock 4대 전략 통합 시뮬레이션 시작")
    
    # 1. 테스트용 로컬 원장 및 오케스트레이터 생성
    test_db = "d:/Jun_Project_D/The_Stock/test_simulation.db"
    ledger = SqliteLedger(db_path=test_db)
    kis_client = KisClient()
    orchestrator = TradingOrchestrator(ledger=ledger, kis_client=kis_client)
    
    await orchestrator.start()
    
    # 2. 초기 잔고 출력
    accounts = await ledger.get_accounts()
    logger.info(f"💰 초기 슬롯 계좌 {len(accounts)}개 확인:")
    for acc in accounts:
        logger.info(f"   - {acc.slot_id} ({acc.name}): 자본금 {acc.total_asset:,.0f}원")

    # 3. 마켓 필터: 상승장 설정 (코스피 2650 > 20일선 2600)
    orchestrator.market_filter.update_kospi_status(2650.0, 2600.0, 1.2)
    logger.info("📈 마켓필터 상태: 상승장 (코스피 2650 > 20MA 2600) -> 4개 슬롯 진입 허용")

    # 4. 슬롯 B (ETF 볼린저 그리드) 시뮬레이션
    logger.info("🔄 [시뮬레이션 1] 슬롯 B: KODEX 200(069500) 하단 이탈 1차 분할 매수 틱 주입")
    slot_b = orchestrator.strategies["SLOT_B"]
    slot_b.update_bands("069500", upper=37000.0, middle=36000.0, lower=35000.0)

    # 하단 밴드 35000 이하로 틱 발생
    tick_b_buy = TickData(ticker="069500", price=34800.0, bid_price=34750.0, ask_price=34800.0)
    await orchestrator.on_tick_received(tick_b_buy)

    # 5. 슬롯 C (단타 변동성 돌파) 시뮬레이션
    logger.info("🔄 [시뮬레이션 2] 슬롯 C: 에코프로(086520) 변동성 돌파 틱 주입")
    slot_c = orchestrator.strategies["SLOT_C"]
    slot_c.update_watchlist(["086520"], {"086520": 80000.0})
    slot_c.avg_volumes["086520"] = 1000.0
    slot_c.recent_1m_volumes["086520"] = 3000.0  # 300% 거래량 급증 (정량필터 통과)
    slot_c.bypass_time_filter = True

    # 돌파가 초과 틱 주입
    tick_c_buy = TickData(
        ticker="086520",
        price=80500.0,
        bid_price=80400.0,
        ask_price=80500.0,
        volume=15000,
        exec_strength=125.0
    )
    await orchestrator.on_tick_received(tick_c_buy)

    # 현재 포지션 확인
    positions = await ledger.get_positions()
    logger.info(f"📦 현재 보유 포지션 수: {len(positions)}개")
    for p in positions:
        logger.info(f"   - {p.slot_id}: {p.ticker} {p.quantity}주 @ {p.entry_price:,.0f}원")

    # 6. 마스터 킬스위치 테스트
    logger.info("🚨 [시뮬레이션 3] 마스터 킬스위치 호출 (긴급 전량 매도)")
    kill_results = await orchestrator.master_kill_switch()
    logger.info(f"🛑 킬스위치 집행 결과: {kill_results}")

    # 청산 후 포지션 및 거래기록 확인
    positions_after = await ledger.get_positions()
    trades = await ledger.get_recent_trades(limit=10)
    logger.info(f"📦 킬스위치 후 잔여 포지션 수: {len(positions_after)}개 (0개 정상)")
    logger.info(f"📜 기록된 체결 내역 수: {len(trades)}건")
    for t in trades:
        logger.info(
            f"   - {t.slot_id} {t.ticker}: 매수가 {t.buy_price:,.0f}원 -> 매도가 {t.sell_price:,.0f}원 | "
            f"순손익: {t.net_pnl:+,.0f}원 (수수료·세금: {t.total_cost:,.0f}원 차감) | 사유: {t.exit_reason}"
        )

    await orchestrator.stop()
    logger.info("🎉 [시뮬레이터] 모든 파이프라인 시뮬레이션 성공적 완료!")


if __name__ == "__main__":
    asyncio.run(run_simulation())
