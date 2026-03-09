from __future__ import annotations

from dataclasses import dataclass
from fastapi import FastAPI

from .domain import WeightService


class SimpleWeightService(WeightService):
    MAX_WEIGHT_KG: float = 25.0

    def __init__(self, per_kg_fee: float = 10.0) -> None:
        self.per_kg_fee = per_kg_fee

    def calculate_overweight_fee(self, total_weight_kg: float) -> float:
        if total_weight_kg <= self.MAX_WEIGHT_KG:
            return 0.0
        excess = total_weight_kg - self.MAX_WEIGHT_KG
        return excess * self.per_kg_fee


app = FastAPI(title="Baggage Service")
weight_service = SimpleWeightService()


@dataclass
class BaggageQuoteRequest:
    flightId: str
    passengerId: str
    totalWeightKg: float


@app.post("/baggage/quote")
def baggage_quote(req: BaggageQuoteRequest):
    fee = weight_service.calculate_overweight_fee(req.totalWeightKg)
    return {
        "flightId": req.flightId,
        "passengerId": req.passengerId,
        "totalWeightKg": req.totalWeightKg,
        "overweightFee": fee,
    }

