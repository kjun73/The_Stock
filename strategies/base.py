from abc import ABC, abstractmethod
from typing import List, Optional
from core.models import Signal, SlotPosition, TickData


class BaseStrategy(ABC):
    """
    모든 매매 전략이 상속하는 기본 추상 클래스 (전략 패턴)
    """
    def __init__(self, slot_id: str, name: str, universe: List[str]):
        self.slot_id = slot_id
        self.name = name
        self.universe = universe

    @abstractmethod
    async def evaluate_market(
        self,
        tick: TickData,
        current_position: Optional[SlotPosition] = None
    ) -> Optional[Signal]:
        """
        시세 유입 시 단일 평가 메서드:
        - 신규 매수, 분할 매수, 익절, 손절 시그널을 생성하여 반환 (조건 미충족 시 None)
        """
        pass
