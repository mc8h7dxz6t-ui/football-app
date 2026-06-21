from __future__ import annotations

from sqlalchemy import Boolean, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class LineSnapshot(Base):
    __tablename__ = "line_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_key: Mapped[str] = mapped_column(String(256), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    tick_count: Mapped[int] = mapped_column(Integer, default=0)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[float] = mapped_column(Float, index=True)


class PaperPick(Base):
    """Prematch value-scan paper ledger — SHA-256 verification (inst++ parity)."""

    __tablename__ = "paper_picks"

    pick_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fixture_key: Mapped[str] = mapped_column(String(256), index=True)
    market: Mapped[str] = mapped_column(String(32), index=True)
    odds: Mapped[float] = mapped_column(Float)
    stake: Mapped[float] = mapped_column(Float)
    verification_hash: Mapped[str] = mapped_column(String(64), index=True)
    model_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    edge_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    won: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    closing_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    clv_beat: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    clv_benchmark_tier: Mapped[str | None] = mapped_column(String(32), nullable=True)
    clv_benchmark_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[float] = mapped_column(Float, index=True)
    updated_at: Mapped[float] = mapped_column(Float)
    settled_at: Mapped[float | None] = mapped_column(Float, nullable=True)
