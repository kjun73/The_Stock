import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
import logging
from typing import Dict, List, Optional
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from broker.kis_client import KisClient
from broker.toss_client import TossClient
from core.config import settings
from core.firebase_bridge import FirebaseBridge
from core.ledger import SqliteLedger
from core.models import DailySettlement, SlotAccount, SlotPosition, TickData, TradeRecord
from core.orchestrator import TradingOrchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s"
)
logger = logging.getLogger(__name__)

# 싱글톤 컴포넌트 인스턴스
ledger = SqliteLedger()

# 브로커 클라이언트 분기 (토스증권 vs 한국투자증권)
if settings.BROKER_TYPE == "TOSS":
    logger.info("⚡ [Broker] 토스증권 Open API 브로커 선택됨")
    broker_client = TossClient()
else:
    logger.info("⚡ [Broker] 한국투자증권(KIS) 브로커 선택됨")
    broker_client = KisClient()

orchestrator = TradingOrchestrator(ledger=ledger, kis_client=broker_client)
firebase_bridge = FirebaseBridge(orchestrator=orchestrator)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 생명주기 매니저 (기동 및 안전 종료)"""
    logger.info("==================================================")
    logger.info("🌟 The Stock: AI & JEV 주식 자동매매 시스템 서버 시작")
    logger.info("==================================================")
    
    # 1. 원장 초기화 및 오케스트레이터 시작
    await orchestrator.start()
    
    # 2. Firebase RTDB 중계 브리지 시작
    await firebase_bridge.start()
    
    # 3. 브로커 클라이언트 초기화 (토큰 발급 및 시세 수신 시작)
    await broker_client.get_access_token()
    if hasattr(broker_client, "start"):
        await broker_client.start()
    
    yield
    
    logger.info("==================================================")
    logger.info("🛑 The Stock: 시스템 안전 종료 시퀀스 가동")
    logger.info("==================================================")
    await firebase_bridge.stop()
    await orchestrator.stop()


app = FastAPI(
    title="The Stock Trading Core API",
    description="AI(Gemini)와 JEV 기반 4대 전략 병렬 주식 자동매매 및 안드로이드 관제 코어 서버",
    version="3.0.0",
    lifespan=lifespan
)

# CORS 허용 (웹 대시보드 및 로컬 테스트 지원)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Request Models ---
class ToggleSlotRequest(BaseModel):
    is_active: bool


class ClosePositionRequest(BaseModel):
    slot_id: str
    ticker: str


class MarketFilterUpdateRequest(BaseModel):
    kospi_index: float
    kospi_20ma: float
    daily_change_pct: float


# --- REST API Endpoints ---

@app.get("/")
@app.get("/api/status")
async def get_system_status():
    """전체 시스템 현황 및 요약 대시보드 데이터 조회"""
    accounts = await ledger.get_accounts()
    positions = await ledger.get_positions()
    recent_trades = await ledger.get_recent_trades(limit=50)
    settlements = await ledger.get_daily_settlements(limit=14)

    total_asset = sum(a.total_asset for a in accounts)
    total_cash = sum(a.cash_balance for a in accounts)
    total_profit = sum(a.total_profit for a in accounts)
    initial_capital = sum(a.initial_capital for a in accounts)
    total_profit_rate = (total_profit / initial_capital) * 100.0 if initial_capital > 0 else 0.0

    return {
        "status": "ONLINE" if orchestrator.is_running else "OFFLINE",
        "firebase_connected": firebase_bridge.is_connected,
        "firebase_mock_mode": firebase_bridge.mock_mode,
        "portfolio": {
            "initial_capital": initial_capital,
            "total_asset": total_asset,
            "total_cash": total_cash,
            "total_stock": total_asset - total_cash,
            "total_stock_value": total_asset - total_cash,
            "total_profit": total_profit,
            "total_profit_rate": round(total_profit_rate, 2),
            "open_positions_count": len(positions)
        },
        "market_filter": {
            "kospi_index": orchestrator.market_filter.kospi_index,
            "kospi_20ma": orchestrator.market_filter.kospi_20ma,
            "kospi_daily_change_pct": orchestrator.market_filter.kospi_daily_change_pct,
            "is_bear_market": orchestrator.market_filter.is_bear_market,
            "is_panic_sell_day": orchestrator.market_filter.is_panic_sell_day,
            "is_strong_rebound_day": orchestrator.market_filter.is_strong_rebound_day,
            "is_super_rally_day": orchestrator.market_filter.is_super_rally_day
        },
        "slots_summary": [
            {
                "slot_id": a.slot_id,
                "name": a.name,
                "total_asset": a.total_asset,
                "profit_rate": a.profit_rate,
                "is_active": a.is_active,
                "positions_count": a.active_positions_count
            }
            for a in accounts
        ],
        "recent_trades": [t.model_dump() for t in recent_trades],
        "settlement_history": [s.model_dump() for s in settlements],
        "intelligence": {
            "daily_review": getattr(orchestrator, "latest_daily_review", ""),
            "morning_briefing": getattr(orchestrator, "latest_morning_briefing", ""),
            "screening_candidates": getattr(orchestrator, "latest_screening_candidates", {}),
            "updated_at": datetime.now().isoformat()
        }
    }


@app.get("/api/slots", response_model=List[SlotAccount])
async def list_slots():
    """4대 슬롯 가상 계좌 상세 목록 조회"""
    return await ledger.get_accounts()


@app.get("/api/positions", response_model=List[SlotPosition])
async def list_positions(slot_id: Optional[str] = Query(None, description="슬롯 ID 필터 (예: SLOT_A)")):
    """보유 포지션 목록 조회"""
    return await ledger.get_positions(slot_id=slot_id)


@app.get("/api/trades", response_model=List[TradeRecord])
async def list_trades(
    slot_id: Optional[str] = Query(None, description="슬롯 ID 필터"),
    limit: int = Query(20, ge=1, le=100)
):
    """체결 및 청산 기록 조회"""
    return await ledger.get_recent_trades(slot_id=slot_id, limit=limit)


@app.post("/api/kill-switch")
async def trigger_kill_switch():
    """
    🚨 긴급 전량 매도 (Master Kill Switch):
    전 슬롯 전 보유 포지션을 최불리 시장가로 즉시 청산하고 모든 슬롯을 비활성화
    """
    results = await orchestrator.master_kill_switch()
    return {
        "message": "마스터 킬스위치가 성공적으로 집행되었습니다. 모든 슬롯이 동결되었습니다.",
        "closed_positions": results
    }


@app.post("/api/slots/{slot_id}/toggle")
async def toggle_slot(slot_id: str, body: ToggleSlotRequest):
    """개별 슬롯 On/Off 토글"""
    accounts = await ledger.get_accounts()
    if not any(a.slot_id == slot_id for a in accounts):
        raise HTTPException(status_code=404, detail=f"존재하지 않는 슬롯 ID: {slot_id}")

    await orchestrator.toggle_slot(slot_id, body.is_active)
    return {"slot_id": slot_id, "is_active": body.is_active}


@app.post("/api/positions/close")
async def close_position(body: ClosePositionRequest):
    """단일 보유 포지션 수동 청산"""
    trade = await orchestrator.close_slot_position(body.slot_id, body.ticker)
    if not trade:
        raise HTTPException(status_code=404, detail="청산 대상 포지션을 찾을 수 없거나 이미 청산되었습니다.")
    return {"message": "포지션 청산 완료", "trade": trade.model_dump()}


@app.post("/api/market-filter/update")
async def update_market_filter(body: MarketFilterUpdateRequest):
    """마켓 필터 (코스피 지수/이동평균) 갱신"""
    orchestrator.market_filter.update_kospi_status(
        current_index=body.kospi_index,
        ma20=body.kospi_20ma,
        daily_change_pct=body.daily_change_pct
    )
    return {
        "kospi_index": body.kospi_index,
        "kospi_20ma": body.kospi_20ma,
        "is_bear_market": orchestrator.market_filter.is_bear_market,
        "is_panic_sell_day": orchestrator.market_filter.is_panic_sell_day
    }


@app.post("/api/ticks/mock")
async def inject_mock_tick(tick: TickData):
    """모의 틱 데이터 수동 주입 (테스트 및 시뮬레이션용)"""
    await orchestrator.on_tick_received(tick)
    return {"message": "틱 수신 완료", "ticker": tick.ticker, "price": tick.price}


@app.get("/api/version")
async def get_app_version():
    """
    모바일 C2 앱 인앱 자동 업데이트 버전 메타데이터
    """
    apk_path = Path(__file__).resolve().parent.parent / "android-app" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
    return {
        "versionCode": 7,
        "versionName": "1.0.9",
        "apkUrl": "https://github.com/kjun73/The_Stock/releases/download/v1.0.9/app-debug.apk",
        "notes": "v1.0.9 KRX 공식 연동, 14일 자산 곡선(Equity Curve), 본전/트레일링 배지, 청산 손익비 요약 및 날짜별 복기 뷰어 탑재",
        "isForce": False,
        "hasApk": apk_path.is_file()
    }


@app.get("/api/download/apk")
async def download_apk():
    """
    최신 빌드된 안드로이드 C2 관제 APK 스트리밍 다운로드
    """
    apk_path = Path(__file__).resolve().parent.parent / "android-app" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
    if not apk_path.is_file():
        raise HTTPException(status_code=404, detail="최신 APK 파일이 빌드되지 않았거나 존재하지 않습니다.")
    return FileResponse(
        path=str(apk_path),
        filename="The_Stock_C2.apk",
        media_type="application/vnd.android.package-archive"
    )


@app.get("/api/settlements")
async def get_daily_settlements(limit: int = Query(default=30, ge=1, le=365)):
    """최근 일일 결산/복기 히스토리 목록 조회"""
    settlements = await ledger.get_daily_settlements(limit=limit)
    return [s.model_dump() for s in settlements]


@app.get("/api/settlements/{date}")
async def get_daily_settlement(date: str):
    """특정 일자의 결산/복기 조회 (형식: YYYY-MM-DD)"""
    settlement = await ledger.get_daily_settlement(date=date)
    if not settlement:
        raise HTTPException(status_code=404, detail=f"{date} 일자의 결산 기록이 존재하지 않습니다.")
    return settlement.model_dump()


@app.get("/api/reports/weekly")
async def get_weekly_report():
    """최신 주간 퀀트 손익비 진단 리포트 조회"""
    report = getattr(orchestrator, "latest_weekly_review", "")
    if not report:
        w_path = Path("reports/weekly_review.md")
        if w_path.exists():
            report = w_path.read_text(encoding="utf-8").strip()
    return {"weekly_review": report}


