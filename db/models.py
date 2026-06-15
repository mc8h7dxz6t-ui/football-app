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
