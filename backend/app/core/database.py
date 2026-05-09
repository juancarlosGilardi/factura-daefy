"""Database setup STUB — Factura-mdb opera SOLO contra MDB.

Este módulo existe SOLO para no romper imports heredados:
- `Base` lo siguen referenciando los modelos SQLAlchemy
  (`app/models/*.py`), aunque nunca se materializan tablas.
- `get_db()` se inyecta como dependency en routers; los routers reales
  redireccionan a los repositorios MDB cuando `is_mdb_mode()` es True
  (siempre en este proyecto).

NO se crea engine real ni se abren conexiones SQLite. La sesión que
yield `get_db()` es un stub que no soporta operaciones de query — si
algún flujo intenta usarla en Factura-mdb, se considera bug y debe
migrarse al adapter MDB correspondiente.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base — usada por los models heredados; las tablas
    NUNCA se materializan en este proyecto."""


class _StubSession:
    """Stub muy ligero — implementa solo lo necesario para que `Depends(get_db)`
    no rompa al inyectarse. Cualquier intento de query es un bug que debe
    migrarse al adapter MDB."""

    def query(self, *_args, **_kwargs):  # noqa: D401
        raise RuntimeError(
            "Sesión SQLite no disponible en Factura-mdb. Usa los repos MDB "
            "(ClienteRepoMDB, ProductoRepoMDB, ComprobanteRepoMDB)."
        )

    def get(self, *_args, **_kwargs):
        raise RuntimeError(
            "Sesión SQLite no disponible en Factura-mdb. Usa los repos MDB."
        )

    def add(self, *_args, **_kwargs):  # noqa: D401
        raise RuntimeError("Sesión SQLite no disponible en Factura-mdb.")

    def commit(self) -> None:  # noqa: D401
        return None

    def rollback(self) -> None:  # noqa: D401
        return None

    def refresh(self, *_args, **_kwargs) -> None:  # noqa: D401
        return None

    def close(self) -> None:  # noqa: D401
        return None


# `engine` se define para no romper imports defensivos en código heredado;
# nunca se conecta nada con él en Factura-mdb.
engine = None  # type: ignore[assignment]


@contextmanager
def _session() -> Iterator[_StubSession]:
    s = _StubSession()
    try:
        yield s
    finally:
        s.close()


def SessionLocal() -> _StubSession:  # noqa: N802 — preserva nombre legado
    """Factory de sesiones — devuelve un stub. Ver advertencia de módulo."""
    return _StubSession()


def get_db() -> Iterator[_StubSession]:
    """Dependency FastAPI — devuelve un stub para que los `Depends(get_db)`
    en routers heredados no rompan al inyectarse."""
    db = _StubSession()
    try:
        yield db
    finally:
        db.close()
