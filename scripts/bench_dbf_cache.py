"""Benchmark + regresión de la caché DBF.

Mide tiempos de la primera llamada (MISS) vs subsecuentes (HIT) y
verifica que los filtros sigan devolviendo la misma cantidad de items
que sin caché. Pensado para correrse manualmente:

    python scripts/bench_dbf_cache.py
"""
from __future__ import annotations
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

# Habilitar logs INFO de la caché para ver HIT/MISS
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from app.core.db_adapter import dbf_repo  # noqa: E402
from app.core.db_adapter.dbf_repo import (  # noqa: E402
    ClienteRepoDBF,
    ProductoRepoDBF,
    ComprobanteRepoDBF,
    invalidate_cache,
)


def cronometra(label: str, fn):
    t0 = time.perf_counter()
    out = fn()
    dt = time.perf_counter() - t0
    print(f"  {label:35s} {dt*1000:8.1f} ms")
    return out, dt


def bench_clientes():
    print("\n=== Clientes ===")
    invalidate_cache("clientes")

    items, miss_dt = cronometra(
        "listar() MISS (full-scan)",
        lambda: ClienteRepoDBF.listar(limit=50, offset=0),
    )
    items_miss, total_miss = items
    print(f"     items={len(items_miss)}  total={total_miss}")

    _, hit_dt = cronometra(
        "listar() HIT (cache)",
        lambda: ClienteRepoDBF.listar(limit=50, offset=0),
    )

    # Verificar que filtros siguen funcionando
    print("\n  Regresión de filtros:")
    (items_q, total_q), _ = cronometra(
        "listar(q='a')",
        lambda: ClienteRepoDBF.listar(q="a", limit=50, offset=0),
    )
    print(f"     total con q='a': {total_q}")

    (items_t, total_t), _ = cronometra(
        "listar(tipo_documento='6')",
        lambda: ClienteRepoDBF.listar(tipo_documento="6", limit=50, offset=0),
    )
    print(f"     total con tipo_documento='6': {total_t}")

    (items_a, total_a), _ = cronometra(
        "listar(activo=True)",
        lambda: ClienteRepoDBF.listar(activo=True, limit=50, offset=0),
    )
    print(f"     total con activo=True: {total_a}")

    # Validar invalidación
    print("\n  Invalidación:")
    invalidate_cache("clientes")
    _, miss2_dt = cronometra(
        "listar() después de invalidate",
        lambda: ClienteRepoDBF.listar(limit=50, offset=0),
    )
    assert miss2_dt > hit_dt, "Después de invalidar debería ser MISS (más lento)"

    print(f"\n  Speedup HIT/MISS: {miss_dt/hit_dt:.0f}x")
    return total_miss, miss_dt, hit_dt


def bench_productos():
    print("\n=== Productos ===")
    invalidate_cache("productos")

    (items, total), miss_dt = cronometra(
        "listar() MISS",
        lambda: ProductoRepoDBF.listar(limit=50, offset=0),
    )
    print(f"     items={len(items)}  total={total}")

    _, hit_dt = cronometra(
        "listar() HIT",
        lambda: ProductoRepoDBF.listar(limit=50, offset=0),
    )
    print(f"  Speedup HIT/MISS: {miss_dt/hit_dt:.0f}x")
    return total, miss_dt, hit_dt


def bench_comprobantes():
    print("\n=== Comprobantes ===")
    invalidate_cache("comprobantes_cab")

    (items, total), miss_dt = cronometra(
        "listar() MISS",
        lambda: ComprobanteRepoDBF.listar(limit=50, offset=0),
    )
    print(f"     items={len(items)}  total={total}")

    _, hit_dt = cronometra(
        "listar() HIT",
        lambda: ComprobanteRepoDBF.listar(limit=50, offset=0),
    )

    print("\n  proximo_correlativo (usa caché):")
    series_data = ComprobanteRepoDBF.listar_series()
    if series_data:
        s = series_data[0]["serie"]
        cronometra(
            f"proximo_correlativo({s})",
            lambda: ComprobanteRepoDBF.proximo_correlativo(s),
        )
    print(f"  Speedup HIT/MISS: {miss_dt/hit_dt:.0f}x")
    return total, miss_dt, hit_dt


def main():
    print("Benchmark caché DBF")
    print("=" * 50)
    print(f"TTL clientes: {dbf_repo.CACHE_TTL_CLIENTES}s")
    print(f"TTL productos: {dbf_repo.CACHE_TTL_PRODUCTOS}s")
    print(f"TTL comprobantes: {dbf_repo.CACHE_TTL_COMPROBANTES}s")

    try:
        cli_total, cli_miss, cli_hit = bench_clientes()
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR clientes: {exc}")
        cli_total = cli_miss = cli_hit = None

    try:
        prod_total, prod_miss, prod_hit = bench_productos()
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR productos: {exc}")
        prod_total = prod_miss = prod_hit = None

    try:
        comp_total, comp_miss, comp_hit = bench_comprobantes()
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR comprobantes: {exc}")
        comp_total = comp_miss = comp_hit = None

    print("\n" + "=" * 50)
    print("RESUMEN")
    print("=" * 50)
    if cli_miss is not None:
        ok_miss = "OK" if cli_miss < 5 else "WARN"
        ok_hit = "OK" if cli_hit < 0.5 else "WARN"
        print(f"Clientes ({cli_total}): MISS {cli_miss:.2f}s [{ok_miss}<5s] / HIT {cli_hit*1000:.0f}ms [{ok_hit}<500ms]")
    if prod_miss is not None:
        print(f"Productos ({prod_total}): MISS {prod_miss:.2f}s / HIT {prod_hit*1000:.0f}ms")
    if comp_miss is not None:
        print(f"Comprobantes ({comp_total}): MISS {comp_miss:.2f}s / HIT {comp_hit*1000:.0f}ms")


if __name__ == "__main__":
    main()
