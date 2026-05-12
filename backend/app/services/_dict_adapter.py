"""Adaptador dict -> objeto con acceso por atributo.

Permite reutilizar el código del comprobante_service (escrito para
modelos SQLAlchemy con acceso por atributo) cuando estamos en modo
MDB/SQLite, donde los repos devuelven ``dict``.

Uso típico::

    from ._dict_adapter import DictNS
    comp = DictNS(repo_dict)
    print(comp.serie, comp.detalles[0].cantidad)

Nota: no es un wrapper recursivo simplemente lazy — instancia los
sub-objetos al construirse. Para los volúmenes que maneja este
proyecto (un comprobante con N items, N << 100) eso está bien.
"""
from __future__ import annotations

from typing import Any


class DictNS:
    """Namespace simple a partir de un dict.

    - Soporta acceso por atributo (``ns.campo``) y por dict (``ns["campo"]``).
    - Convierte recursivamente sub-dicts y listas de dicts en ``DictNS``.
    - Soporta ``getattr(ns, "campo", default)``.
    - Permite agregar / actualizar atributos posteriormente (igual que un
      objeto Python normal).
    """

    def __init__(self, d: dict | None = None) -> None:
        for k, v in (d or {}).items():
            setattr(self, k, self._wrap(v))

    @classmethod
    def _wrap(cls, v: Any) -> Any:
        if isinstance(v, dict):
            return cls(v)
        if isinstance(v, list):
            return [cls(x) if isinstance(x, dict) else x for x in v]
        return v

    # Acceso por dict
    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError as exc:
            raise KeyError(key) from exc

    def __setitem__(self, key: str, value: Any) -> None:
        setattr(self, key, value)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def to_dict(self) -> dict:
        """Devuelve un dict (no recursivo profundo) con los atributos."""
        return {
            k: v for k, v in self.__dict__.items() if not k.startswith("_")
        }

    def __repr__(self) -> str:  # pragma: no cover - solo debug
        return f"DictNS({self.__dict__!r})"
