import argparse
import logging
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import numpy as np
import pandas as pd

from pathlib import Path

# 프로젝트 루트 경로 추가 (core 모듈 import 보장)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.executor import SqlPaperExecutor
from core.models import AssetType

# 윈도우 cp949 콘솔 인코딩 방어
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Backtester")

try:
    from pykrx import stock
    PYKRX_AVAILABLE = True
except ImportError:
    PYKRX_AVAILABLE = False


def fetch_historical_ohlcv(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    """pykrx를 활용한 과거 일봉 데이터 수집"""
    if PYKRX_AVAILABLE:
        try:
            df = stock.get_market_ohlcv_by_date(start_date, end_date, ticker)
            if df is not None and not df.empty:
                df.columns = [c.lower() for c in df.columns]
                # pykrx 컬럼: 시가, 고가, 저가, 종가, 거래량
                rename_map = {"시가": "open", "고가": "high", "저가": "low", "종가": "close", "거래량": "volume"}
                df = df.rename(columns=rename_map)
                return df
        except Exception as e:
            logger.warning(f"pykrx 데이터 조회 실패 ({ticker}): {e}")

    # pykrx 실패 또는 오프라인 환경 대비 모의 데이터 생성
    dates = pd.date_range(start=start_date, end=end_date, freq="B")
    data = []
    base_price = 100000.0 if ticker == "005930" else (13000.0 if ticker == "229200" else 50000.0)
    np.random.seed(42)
    for _ in dates:
        change = np.random.normal(0.001, 0.02)
        close = round(base_price * (1 + change), 0)
        high = round(close * (1 + abs(np.random.normal(0.005, 0.01))), 0)
        low = round(close * (1 - abs(np.random.normal(0.005, 0.01))), 0)
        open_p = round((high + low) / 2, 0)
        vol = int(np.random.uniform(50000, 300000))
        data.append({"open": open_p, "high": high, "low": low, "close": close, "volume": vol})
        base_price = close
    return pd.DataFrame(data, index=dates)


def compute_trade_costs(
    entry_price: float,
    exit_price: float,
    quantity: int,
    is_etf: bool = False,
    apply_slippage: bool = True
) -> tuple[float, float, float, float]:
    """
    SqlPaperExecutor와 100% 동일한 수수료, 세금 및 슬리피지 산출
    - 주식: 매수 수수료 0.015%, 매도 수수료 0.015% + 증권거래세 0.20% (합계 0.23%)
    - ETF: 매수 수수료 0.015%, 매도 수수료 0.015% + 거래세 0.0% 면제 (합계 0.03%)
    - 슬리피지: 진입/청산 각각 0.10% 페널티 적용
    """
    asset_type: AssetType = "ETF" if is_etf else "STOCK"
    adj_entry = entry_price * (1.0 + SqlPaperExecutor.SLIPPAGE_PENALTY_RATE) if apply_slippage else entry_price
    adj_exit = exit_price * (1.0 - SqlPaperExecutor.SLIPPAGE_PENALTY_RATE) if apply_slippage else exit_price

    return SqlPaperExecutor.calculate_costs_and_net_pnl(
        adj_entry, adj_exit, quantity, asset_type
    )


def compute_metrics(trades: List[Dict], capital: float, param_meta: Dict) -> Dict:
    """백테스트 체결 결과로부터 퀀트 손익 지표 산출"""
    if not trades:
        res = {"total_trades": 0, "win_rate": 0.0, "payoff_ratio": 0.0, "profit_factor": 0.0, "total_return_pct": 0.0, "net_total_pnl": 0.0}
        res.update(param_meta)
        return res

    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] < 0]

    win_count = len(wins)
    loss_count = len(losses)
    total_trades = len(trades)
    win_rate = (win_count / total_trades) * 100.0 if total_trades > 0 else 0.0

    total_gain = sum(t["net_pnl"] for t in wins)
    total_loss = abs(sum(t["net_pnl"] for t in losses))

    avg_gain = (total_gain / win_count) if win_count > 0 else 0.0
    avg_loss = (total_loss / loss_count) if loss_count > 0 else 0.0
    payoff_ratio = (avg_gain / avg_loss) if avg_loss > 0 else 999.0
    profit_factor = (total_gain / total_loss) if total_loss > 0 else 999.0
    net_total_pnl = total_gain - total_loss
    total_return_pct = (net_total_pnl / capital) * 100.0

    res = {
        "total_trades": total_trades,
        "win_rate": round(win_rate, 2),
        "payoff_ratio": round(payoff_ratio, 2),
        "profit_factor": round(profit_factor, 2),
        "net_total_pnl": round(net_total_pnl, 0),
        "total_return_pct": round(total_return_pct, 2)
    }
    res.update(param_meta)
    return res


# ==============================================================================
# 1. 슬롯 A: 외인·기관 수급 스윙 백테스트 (분할 익절 + 본전보존 손절)
# ==============================================================================
def backtest_slot_a_swing(
    df: pd.DataFrame,
    target_tp: float = 3.0,
    final_tp: float = 6.0,
    stop_loss_rate: float = -2.5,
    breakeven_buffer: float = 0.3
) -> Dict:
    """
    슬롯 A 스윙 매매:
    - 20일 이평선 지지 반등 시 매수 진입
    - 1차 목표가(+3.0%) 도달 시 50% 분할 익절 및 잔여 수량 손절선 본전(+0.3% 수수료 보정) 상향
    - 2차 목표가(+6.0%) 도달 시 전량 청산
    - 손절선(-2.5%) 도달 시 손절 청산
    """
    capital = 10_000_000.0
    pos_capital = 2_500_000.0
    df = df.copy()
    df["ma20"] = df["close"].rolling(20).mean()

    trades = []
    in_pos = False
    entry_price = 0.0
    qty = 0
    stage = 0  # 0: 미진입, 1: 1차 진입, 2: 1차 익절 완료(잔여 50%)
    current_sl = stop_loss_rate

    for i in range(20, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]

        if not in_pos:
            # 20일 이평 골든크로스 / 지지 확인 시 익일 시초가 매수
            if prev["close"] >= prev["ma20"] and df.iloc[i - 2]["close"] < df.iloc[i - 2]["ma20"]:
                entry_price = row["open"]
                qty = int(pos_capital // entry_price)
                if qty >= 2:
                    in_pos = True
                    stage = 1
                    current_sl = stop_loss_rate
            continue

        # 포지션 보유 중인 경우
        high_pct = ((row["high"] - entry_price) / entry_price) * 100.0
        low_pct = ((row["low"] - entry_price) / entry_price) * 100.0

        if stage == 1:
            # 1. 손절 여부 체크
            if low_pct <= current_sl:
                exit_price = round(entry_price * (1.0 + current_sl / 100.0), 0)
                _, _, net_pnl, _ = compute_trade_costs(entry_price, exit_price, qty, is_etf=False)
                trades.append({"net_pnl": net_pnl, "reason": "STOP_LOSS"})
                in_pos = False
                stage = 0
            # 2. 1차 분할 익절 (+3.0%)
            elif high_pct >= target_tp:
                exit_price = round(entry_price * (1.0 + target_tp / 100.0), 0)
                sold_qty = qty // 2
                qty -= sold_qty
                _, _, net_pnl, _ = compute_trade_costs(entry_price, exit_price, sold_qty, is_etf=False)
                trades.append({"net_pnl": net_pnl, "reason": "PARTIAL_TP"})
                stage = 2
                current_sl = breakeven_buffer  # 본전 보존 손절선 상향 (+0.3%)
        elif stage == 2:
            # 1. 본전 보존 손절 (+0.3%)
            if low_pct <= current_sl:
                exit_price = round(entry_price * (1.0 + current_sl / 100.0), 0)
                _, _, net_pnl, _ = compute_trade_costs(entry_price, exit_price, qty, is_etf=False)
                trades.append({"net_pnl": net_pnl, "reason": "BREAKEVEN_STOP"})
                in_pos = False
                stage = 0
            # 2. 최종 2차 익절 (+6.0%)
            elif high_pct >= final_tp:
                exit_price = round(entry_price * (1.0 + final_tp / 100.0), 0)
                _, _, net_pnl, _ = compute_trade_costs(entry_price, exit_price, qty, is_etf=False)
                trades.append({"net_pnl": net_pnl, "reason": "FINAL_TP"})
                in_pos = False
                stage = 0

    return compute_metrics(trades, capital, {
        "target_tp": target_tp,
        "final_tp": final_tp,
        "stop_loss_rate": stop_loss_rate
    })


# ==============================================================================
# 2. 슬롯 B: KODEX 코스닥150 볼린저 밴드 그리드 백테스트 (ETF 거래세 0%)
# ==============================================================================
def backtest_slot_b_bollinger(
    df: pd.DataFrame,
    window: int = 20,
    num_std: float = 2.0,
    stop_loss_rate: float = -3.5
) -> Dict:
    """
    슬롯 B 볼린저 밴드 그리드 매매 (ETF 전용, 증권거래세 0.0% 면제):
    - 밴드 하단(20MA - 2σ) 도달 시 분할 매수
    - 20MA(중심선) 도달 시 50% 분할 익절
    - 밴드 상단(20MA + 2σ) 도달 시 전량 청산
    - -3.5% 도달 시 칼손절
    """
    capital = 10_000_000.0
    pos_capital = 2_000_000.0
    df = df.copy()
    df["ma"] = df["close"].rolling(window).mean()
    df["std"] = df["close"].rolling(window).std()
    df["upper"] = df["ma"] + (num_std * df["std"])
    df["lower"] = df["ma"] - (num_std * df["std"])

    trades = []
    in_pos = False
    entry_price = 0.0
    qty = 0
    half_exited = False

    for i in range(window, len(df)):
        row = df.iloc[i]
        lower_band = row["lower"]
        center_ma = row["ma"]
        upper_band = row["upper"]

        if not in_pos:
            # 저가가 볼린저 하단 밴드 터치 시 매수
            if row["low"] <= lower_band:
                entry_price = lower_band
                qty = int(pos_capital // entry_price)
                if qty >= 2:
                    in_pos = True
                    half_exited = False
            continue

        # 보유 중
        loss_pct = ((row["low"] - entry_price) / entry_price) * 100.0
        if loss_pct <= stop_loss_rate:
            exit_price = round(entry_price * (1.0 + stop_loss_rate / 100.0), 0)
            _, _, net_pnl, _ = compute_trade_costs(entry_price, exit_price, qty, is_etf=True)
            trades.append({"net_pnl": net_pnl, "reason": "STOP_LOSS"})
            in_pos = False
            continue

        if not half_exited and row["high"] >= center_ma:
            exit_price = center_ma
            sold_qty = qty // 2
            qty -= sold_qty
            _, _, net_pnl, _ = compute_trade_costs(entry_price, exit_price, sold_qty, is_etf=True)
            trades.append({"net_pnl": net_pnl, "reason": "CENTER_50PCT_TP"})
            half_exited = True

        if half_exited and row["high"] >= upper_band:
            exit_price = upper_band
            _, _, net_pnl, _ = compute_trade_costs(entry_price, exit_price, qty, is_etf=True)
            trades.append({"net_pnl": net_pnl, "reason": "UPPER_BAND_TP"})
            in_pos = False

    return compute_metrics(trades, capital, {
        "window": window,
        "num_std": num_std,
        "stop_loss_rate": stop_loss_rate
    })


# ==============================================================================
# 3. 슬롯 C: 래리 윌리엄스 변동성 돌파 + 동적 트레일링 스탑 단타
# ==============================================================================
def backtest_slot_c_breakout(
    df: pd.DataFrame,
    k_factor: float = 0.5,
    trailing_activation: float = 1.5,
    trailing_drop: float = 1.0,
    stop_loss_rate: float = -2.8,
    is_etf: bool = False
) -> Dict:
    """
    슬롯 C (변동성 돌파 + 트레일링 스탑 + 5일선 추세 필터) 당일 청산 단타
    - 일봉 고가/저가 선후관계 반영: 양봉 마감일은 시초 저점 형성 후 돌파 상승, 음봉 역전일은 돌파 후 저점 하락으로 정밀 판정
    """
    trades = []
    capital = 10_000_000.0
    pos_capital = 2_500_000.0
    df = df.copy()
    df["ma5"] = df["close"].rolling(5).mean()

    for i in range(5, len(df)):
        prev_row = df.iloc[i - 1]
        curr_row = df.iloc[i]

        if prev_row["close"] < prev_row["ma5"]:
            continue

        prev_range = prev_row["high"] - prev_row["low"]
        target_price = curr_row["open"] + (prev_range * k_factor)

        if curr_row["high"] >= target_price:
            entry_price = target_price
            qty = int(pos_capital // entry_price)
            if qty <= 0:
                continue

            day_high = curr_row["high"]
            day_low = curr_row["low"]
            day_close = curr_row["close"]

            # 돌파 진입 이후 발생한 실질 저가 추정 (음봉 마감 시에만 일중 저가까지 밀린 것으로 산정)
            post_entry_low = day_low if day_close < curr_row["open"] else min(entry_price, day_close)
            max_pnl_rate = ((day_high - entry_price) / entry_price) * 100.0
            min_pnl_rate = ((post_entry_low - entry_price) / entry_price) * 100.0

            if min_pnl_rate <= stop_loss_rate:
                exit_price = round(entry_price * (1.0 + stop_loss_rate / 100.0), 0)
                reason = "STOP_LOSS"
            elif max_pnl_rate >= trailing_activation:
                exit_price = round(day_high * (1.0 - trailing_drop / 100.0), 0)
                reason = "TRAILING_STOP"
            else:
                exit_price = day_close
                reason = "TIME_EXIT"

            # SqlPaperExecutor와 100% 동일한 수수료/세금 적용
            _, _, net_pnl, pnl_pct = compute_trade_costs(entry_price, exit_price, qty, is_etf=is_etf)
            trades.append({"net_pnl": net_pnl, "pnl_pct": pnl_pct, "reason": reason})

    return compute_metrics(trades, capital, {
        "k_factor": k_factor,
        "trailing_activation": trailing_activation,
        "stop_loss_rate": stop_loss_rate
    })


# ==============================================================================
# 4. 슬롯 D: 신고가 눌림목 지지 반등 스윙 백테스트
# ==============================================================================
def backtest_slot_d_retest(
    df: pd.DataFrame,
    trailing_activation: float = 3.5,
    trailing_drop: float = 1.5,
    stop_loss_rate: float = -2.8
) -> Dict:
    """
    슬롯 D (전고점 돌파 첫 눌림목 스윙):
    - 20일 신고가 달성 후 5일 이평선 눌림목 반등 시 매수 진입
    - +3.5% 도달 시 트레일링 스탑 활성화 (-1.5% 반락 시 익절)
    - -2.8% 도달 시 절대 손절
    """
    capital = 10_000_000.0
    pos_capital = 2_500_000.0
    df = df.copy()
    df["high20"] = df["high"].rolling(20).max()
    df["ma5"] = df["close"].rolling(5).mean()

    trades = []
    in_pos = False
    entry_price = 0.0
    qty = 0
    highest_after_entry = 0.0

    for i in range(21, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]

        if not in_pos:
            # 최근 20일 신고가 근처에서 5일선 지지 반등
            near_high = prev["high"] >= df["high20"].iloc[i - 5:i].max() * 0.98
            bounced_ma5 = row["low"] <= row["ma5"] and row["close"] >= row["ma5"]
            if near_high and bounced_ma5:
                entry_price = row["close"]
                qty = int(pos_capital // entry_price)
                if qty > 0:
                    in_pos = True
                    highest_after_entry = row["high"]
            continue

        # 보유 중
        if row["high"] > highest_after_entry:
            highest_after_entry = row["high"]

        max_pct = ((highest_after_entry - entry_price) / entry_price) * 100.0
        low_pct = ((row["low"] - entry_price) / entry_price) * 100.0

        # 1. 절대 손절
        if low_pct <= stop_loss_rate:
            exit_price = round(entry_price * (1.0 + stop_loss_rate / 100.0), 0)
            _, _, net_pnl, _ = compute_trade_costs(entry_price, exit_price, qty, is_etf=False)
            trades.append({"net_pnl": net_pnl, "reason": "STOP_LOSS"})
            in_pos = False
        # 2. 트레일링 익절 (+3.5% 돌파 후 고점 대비 -1.5% 반락)
        elif max_pct >= trailing_activation and row["low"] <= highest_after_entry * (1.0 - trailing_drop / 100.0):
            exit_price = round(highest_after_entry * (1.0 - trailing_drop / 100.0), 0)
            _, _, net_pnl, _ = compute_trade_costs(entry_price, exit_price, qty, is_etf=False)
            trades.append({"net_pnl": net_pnl, "reason": "TRAILING_STOP"})
            in_pos = False

    return compute_metrics(trades, capital, {
        "trailing_activation": trailing_activation,
        "trailing_drop": trailing_drop,
        "stop_loss_rate": stop_loss_rate
    })


# ==============================================================================
# 그리드 서치 및 랭킹 리포트 출력
# ==============================================================================
def print_ranking_table(title: str, results_df: pd.DataFrame, param_cols: List[str]):
    print("\n" + "=" * 90)
    print(f"📊 [BEST] {title} 파라미터 최적화 (Top 5 랭킹)")
    print("=" * 90)
    param_header = " | ".join(f"{c:<14}" for c in param_cols)
    print(f"{'순위':<4} | {param_header} | {'체결':<5} | {'승률':<7} | {'손익비':<7} | {'수익팩터':<7} | {'총수익률':<9}")
    print("-" * 90)

    for rank, (_, row) in enumerate(results_df.head(5).iterrows(), start=1):
        param_vals = " | ".join(f"{row[c]:<14}" for c in param_cols)
        print(f"{rank:<4} | {param_vals} | {int(row['total_trades']):<5} | {row['win_rate']:<6.1f}% | {row['payoff_ratio']:<7.2f} | {row['profit_factor']:<7.2f} | {row['total_return_pct']:+8.2f}%")
    print("=" * 90 + "\n")


def run_grid_search_slot_a(ticker: str = "005930", days: int = 180):
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    end_date = datetime.now().strftime("%Y%m%d")
    logger.info(f"📊 [슬롯 A: 수급 스윙] {ticker} 최근 {days}일 ({start_date}~{end_date}) 백테스트 가동...")
    df = fetch_historical_ohlcv(ticker, start_date, end_date)

    results = []
    for tp in [2.5, 3.0, 3.5]:
        for final in [5.0, 6.0, 7.0]:
            for sl in [-2.0, -2.5, -3.0]:
                results.append(backtest_slot_a_swing(df, target_tp=tp, final_tp=final, stop_loss_rate=sl))

    df_res = pd.DataFrame(results).sort_values(by=["payoff_ratio", "total_return_pct"], ascending=False)
    print_ranking_table("슬롯 A (수급 스윙)", df_res, ["target_tp", "final_tp", "stop_loss_rate"])
    return df_res.iloc[0].to_dict()


def run_grid_search_slot_b(ticker: str = "229200", days: int = 180):
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    end_date = datetime.now().strftime("%Y%m%d")
    logger.info(f"📊 [슬롯 B: 볼린저 그리드] {ticker} 최근 {days}일 ({start_date}~{end_date}) 백테스트 가동...")
    df = fetch_historical_ohlcv(ticker, start_date, end_date)

    results = []
    for window in [15, 20, 25]:
        for std in [1.8, 2.0, 2.2]:
            for sl in [-3.0, -3.5, -4.0]:
                results.append(backtest_slot_b_bollinger(df, window=window, num_std=std, stop_loss_rate=sl))

    df_res = pd.DataFrame(results).sort_values(by=["profit_factor", "total_return_pct"], ascending=False)
    print_ranking_table("슬롯 B (볼린저 그리드 ETF)", df_res, ["window", "num_std", "stop_loss_rate"])
    return df_res.iloc[0].to_dict()


def run_grid_search_slot_c(ticker: str = "233740", days: int = 180):
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    end_date = datetime.now().strftime("%Y%m%d")
    is_etf = ticker in {"069500", "229200", "122630", "233740", "114800", "252670"}
    logger.info(f"📊 [슬롯 C: 변동성 돌파] {ticker} (ETF={is_etf}) 최근 {days}일 ({start_date}~{end_date}) 백테스트 가동...")
    df = fetch_historical_ohlcv(ticker, start_date, end_date)

    results = []
    for k in [0.4, 0.5, 0.6]:
        for act in [1.2, 1.5, 2.0]:
            for sl in [-2.5, -2.8, -3.2]:
                results.append(backtest_slot_c_breakout(df, k_factor=k, trailing_activation=act, stop_loss_rate=sl, is_etf=is_etf))

    df_res = pd.DataFrame(results).sort_values(by=["profit_factor", "total_return_pct"], ascending=False)
    print_ranking_table("슬롯 C (변동성 돌파 + 트레일링)", df_res, ["k_factor", "trailing_activation", "stop_loss_rate"])
    return df_res.iloc[0].to_dict()


def run_grid_search_slot_d(ticker: str = "000660", days: int = 180):
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    end_date = datetime.now().strftime("%Y%m%d")
    logger.info(f"📊 [슬롯 D: 신고가 눌림목] {ticker} 최근 {days}일 ({start_date}~{end_date}) 백테스트 가동...")
    df = fetch_historical_ohlcv(ticker, start_date, end_date)

    results = []
    for act in [3.0, 3.5, 4.0]:
        for drop in [1.0, 1.5, 2.0]:
            for sl in [-2.5, -2.8, -3.2]:
                results.append(backtest_slot_d_retest(df, trailing_activation=act, trailing_drop=drop, stop_loss_rate=sl))

    df_res = pd.DataFrame(results).sort_values(by=["payoff_ratio", "total_return_pct"], ascending=False)
    print_ranking_table("슬롯 D (신고가 눌림목)", df_res, ["trailing_activation", "trailing_drop", "stop_loss_rate"])
    return df_res.iloc[0].to_dict()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="The Stock 4대 슬롯 통합 백테스터 및 파라미터 최적화 엔진")
    parser.add_argument("--slot", type=str, default="ALL", choices=["A", "B", "C", "D", "ALL"], help="백테스트 대상 슬롯 (기본: ALL)")
    parser.add_argument("--ticker", type=str, default="", help="대상 종목코드 (미지정 시 슬롯 기본 종목)")
    parser.add_argument("--days", type=int, default=180, help="백테스트 기간 (기본: 180일)")
    args = parser.parse_args()

    target_slot = args.slot.upper()
    days = args.days

    if target_slot in ("A", "ALL"):
        ticker_a = args.ticker if args.ticker else "005930"
        run_grid_search_slot_a(ticker=ticker_a, days=days)

    if target_slot in ("B", "ALL"):
        ticker_b = args.ticker if args.ticker else "229200"
        run_grid_search_slot_b(ticker=ticker_b, days=days)

    if target_slot in ("C", "ALL"):
        ticker_c = args.ticker if args.ticker else "233740"
        run_grid_search_slot_c(ticker=ticker_c, days=days)

    if target_slot in ("D", "ALL"):
        ticker_d = args.ticker if args.ticker else "000660"
        run_grid_search_slot_d(ticker=ticker_d, days=days)
