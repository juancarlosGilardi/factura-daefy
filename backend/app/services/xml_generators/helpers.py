"""Funciones auxiliares para generacion de XML SUNAT."""
from __future__ import annotations
from decimal import Decimal, ROUND_HALF_UP
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple


def q2(x: Decimal) -> Decimal:
    """Redondea a 2 decimales con ROUND_HALF_UP."""
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def d(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def fmt_qty(x: Decimal) -> str:
    """Formatea cantidad: entero si no tiene decimales, sino recorta ceros."""
    x = d(x)
    if x == x.to_integral_value():
        return str(int(x))
    s = format(x, "f").rstrip("0").rstrip(".")
    return s


def obtener_tasa_igv(afectacion: str) -> Decimal:
    """Tasa IGV segun codigo de afectacion."""
    if afectacion in ("10", "11", "12", "13", "14", "15", "16"):
        return Decimal("18")
    if afectacion == "17":  # IVAP
        return Decimal("4")
    return Decimal("0")


def obtener_esquema_tributo(afectacion: str) -> Tuple[str, str, str]:
    """Devuelve (scheme_id, name, type_code) segun afectacion IGV."""
    if afectacion == "10":
        return "1000", "IGV", "VAT"
    if afectacion == "17":
        return "1016", "IVAP", "VAT"
    if afectacion == "20":
        return "9997", "EXO", "VAT"
    if afectacion == "30":
        return "9998", "INA", "FRE"
    if afectacion == "40":
        return "9995", "EXP", "FRE"
    # 11-16, 31-36 -> GRA
    return "9996", "GRA", "FRE"


def es_gratuita(afectacion: str) -> bool:
    """True si la afectacion corresponde a operacion gratuita."""
    return afectacion not in ("10", "17", "20", "30", "40")


def calcular_totales_items(items: List[Any],
                           descuentos_globales: Optional[List[Any]] = None,
                           detraccion: Optional[Any] = None,
                           anticipo: Optional[Any] = None) -> Dict[str, Any]:
    """
    Calcula totales a partir de items y descuentos opcionales.
    Compartido entre Invoice, CreditNote y DebitNote.
    """
    valor_venta_oneroso = Decimal("0.00")
    igv_oneroso         = Decimal("0.00")
    isc_total           = Decimal("0.00")
    icbper_total        = Decimal("0.00")
    taxes: Dict[str, Any] = {}

    for it in items:
        cant = d(it.cantidad)
        vu   = q2(d(it.valor_unitario))
        afec = it.afectacion_igv

        tasa           = obtener_tasa_igv(afec)
        sid, sname, st = obtener_esquema_tributo(afec)
        gratuita       = es_gratuita(afec)

        vv_bruto = q2(cant * vu)
        # Item discount: use NET value (matching LineExtensionAmount in XML)
        desc_item = Decimal("0.00")
        if it.descuento and not it.descuento.charge_indicator:
            desc_item = q2(d(it.descuento.monto))
        vv = q2(vv_bruto - desc_item)

        # ISC — Impuesto Selectivo al Consumo (TaxScheme 2000, TierRange 01)
        isc_l = Decimal("0.00")
        if getattr(it, "isc_tasa", None) is not None:
            isc_l = q2(vv * d(it.isc_tasa) / Decimal("100"))
            if not gratuita:
                isc_total += isc_l
            if "2000" not in taxes:
                taxes["2000"] = {"name": "ISC", "type": "EXC",
                                  "taxable": Decimal("0.00"), "amount": Decimal("0.00")}
            taxes["2000"]["taxable"] += vv
            taxes["2000"]["amount"]  += isc_l

        # IGV: base = valor_venta + ISC
        igv_base = vv + isc_l
        igv_l    = q2(igv_base * tasa / Decimal("100"))

        if not gratuita:
            valor_venta_oneroso += vv
            if tasa > 0:
                igv_oneroso += igv_l

        if sid not in taxes:
            taxes[sid] = {"name": sname, "type": st,
                          "taxable": Decimal("0.00"), "amount": Decimal("0.00")}
        taxes[sid]["taxable"] += vv
        taxes[sid]["amount"]  += igv_l

        # ICBPER — impuesto a bolsas plasticas (TaxScheme 7152)
        if getattr(it, "icbper_monto", None) is not None:
            icbper_item = q2(cant * d(it.icbper_monto))
            icbper_total += icbper_item
            if "7152" not in taxes:
                taxes["7152"] = {"name": "ICBPER", "type": "OTH",
                                  "taxable": None, "amount": Decimal("0.00")}
            taxes["7152"]["amount"] += icbper_item

    # Guardar IGV bruto (antes de ajuste por anticipo) para TaxInclusiveAmount
    igv_bruto = igv_oneroso

    # Anticipo: reducir base y monto IGV en TaxTotal (solo para display)
    if anticipo:
        ant_monto = q2(d(anticipo.monto))
        if "1000" in taxes:
            new_taxable = q2(max(Decimal("0.00"), taxes["1000"]["taxable"] - ant_monto))
            new_igv = q2(new_taxable * Decimal("18") / Decimal("100"))
            taxes["1000"]["taxable"] = new_taxable
            taxes["1000"]["amount"] = new_igv
            igv_oneroso = new_igv

    # TaxInclusiveAmount = siempre bruto (sin deducir anticipo)
    total_payable_base = valor_venta_oneroso + isc_total + igv_bruto + icbper_total

    total_descuentos  = Decimal("0.00")
    if descuentos_globales:
        for ac in descuentos_globales:
            if not ac.charge_indicator:
                total_descuentos += q2(d(ac.monto))

    # Descuento global: se resta del valor_venta (LineExtensionAmount total)
    # SUNAT UBL 2.1: el descuento global reduce el valor_venta total, NO se usa AllowanceTotalAmount
    # TODO Bug B2/H5: implementacion estricta UBL 2.1 separa LineExt (suma bruta), AllowanceTotalAmount
    # (descuento) y TaxInclusive (lineExt - allowance + tax). Lo actual absorbe el descuento en
    # valor_venta y deja allowance_total=0; SUNAT lo acepta pero no es estrictamente conforme.
    if total_descuentos > 0:
        valor_venta_oneroso = q2(valor_venta_oneroso - total_descuentos)
        # Recalcular IGV sobre el valor neto
        if "1000" in taxes:
            taxes["1000"]["taxable"] = q2(valor_venta_oneroso)
            taxes["1000"]["amount"] = q2(valor_venta_oneroso * Decimal("18") / Decimal("100"))
            igv_oneroso = taxes["1000"]["amount"]
        # Recalcular TaxInclusiveAmount
        igv_bruto = igv_oneroso
        total_payable_base = valor_venta_oneroso + isc_total + igv_bruto + icbper_total

    allowance_total = Decimal("0.00")  # No AllowanceTotalAmount — discount absorbed in valor_venta

    prepaid_amount = q2(d(anticipo.monto)) if anticipo else Decimal("0.00")
    payable_exact = q2(total_payable_base - allowance_total - prepaid_amount)

    # PayableAmount: usar el valor exacto con centavos (NO redondear al entero).
    # SUNAT acepta ambos formatos, pero redondear causa inconsistencia entre
    # el XML (redondeado) y la BD/PDF (exacto). Mantener exacto para consistencia.
    # Si se necesita redondeo, debe hacerse también en motor_tributario.total_venta.
    payable = payable_exact
    rounding_str = None

    monto_detraccion = Decimal("0.00")
    if detraccion:
        monto_detraccion = q2(payable * d(detraccion.porcentaje) / Decimal("100"))

    return {
        "valor_venta":      str(q2(valor_venta_oneroso)),
        "igv":              str(q2(igv_oneroso)),
        "tributos_total":   str(q2(igv_oneroso + isc_total + icbper_total)),
        "tax_inclusive":    str(q2(total_payable_base)),
        "allowance_total":  str(q2(allowance_total)) if allowance_total else None,
        "payable":          str(q2(payable)),
        "payable_rounding": rounding_str,
        "prepaid_amount":   str(prepaid_amount) if anticipo else None,
        "isc_total":        str(q2(isc_total)) if isc_total else None,
        "icbper_total":     str(q2(icbper_total)) if icbper_total else None,
        "monto_detraccion": str(monto_detraccion),
        "taxes": {
            tid: {
                "name":    data["name"],
                "type":    data["type"],
                "taxable": str(q2(data["taxable"])) if data["taxable"] is not None else None,
                "amount":  str(q2(data["amount"])),
            }
            for tid, data in taxes.items()
        },
    }


def numero_a_letras(monto: float, moneda: str = "PEN") -> str:
    """Convierte numero a letras para leyenda del comprobante."""
    unidades = ["", "UNO", "DOS", "TRES", "CUATRO", "CINCO",
                "SEIS", "SIETE", "OCHO", "NUEVE"]
    especiales = ["DIEZ", "ONCE", "DOCE", "TRECE", "CATORCE",
                  "QUINCE", "DIECISEIS", "DIECISIETE", "DIECIOCHO", "DIECINUEVE"]
    decenas = ["", "", "VEINTE", "TREINTA", "CUARENTA", "CINCUENTA",
               "SESENTA", "SETENTA", "OCHENTA", "NOVENTA"]
    centenas = ["", "CIENTO", "DOSCIENTOS", "TRESCIENTOS", "CUATROCIENTOS",
                "QUINIENTOS", "SEISCIENTOS", "SETECIENTOS", "OCHOCIENTOS", "NOVECIENTOS"]

    def convertir_grupo(n: int) -> str:
        if n == 0:
            return ""
        if n == 100:
            return "CIEN"
        resultado = ""
        c = n // 100
        if c:
            resultado += centenas[c] + " "
        du = n % 100
        if 10 <= du < 20:
            resultado += especiales[du - 10]
        else:
            dec = du // 10
            uni = du % 10
            if dec == 2 and uni:
                resultado += "VEINTI" + unidades[uni]
            else:
                if dec:
                    resultado += decenas[dec]
                if dec and uni:
                    resultado += " Y "
                if uni:
                    resultado += unidades[uni]
        return resultado.strip()

    partes = f"{monto:.2f}".split(".")
    entero = int(partes[0])
    decimal = int(partes[1])

    sufijo = "SOLES" if moneda == "PEN" else "DOLARES AMERICANOS"

    # Bug C5/F7: para >= 10 mil millones, devolver numerico para evitar bug en convertir
    if entero >= 10_000_000_000:
        return f"SON {entero:,.0f} CON {decimal:02d}/100 {sufijo}"

    if entero == 0:
        letras = "CERO"
    elif entero == 1:
        letras = "UN"
    else:
        # Bug C5/F7: agregar rama "MIL MILLONES" para 1.000.000.000 - 9.999.999.999
        mil_millones = entero // 1_000_000_000
        restante1 = entero % 1_000_000_000
        millones = restante1 // 1_000_000
        miles = (restante1 % 1_000_000) // 1_000
        cen_val = restante1 % 1_000
        letras = ""
        if mil_millones:
            letras += ("MIL MILLONES " if mil_millones == 1
                       else convertir_grupo(mil_millones) + " MIL MILLONES ")
        if millones:
            letras += ("UN MILLON " if (millones == 1 and not mil_millones)
                       else convertir_grupo(millones) + " MILLONES ")
        if miles:
            letras += ("MIL " if miles == 1
                       else convertir_grupo(miles) + " MIL ")
        if cen_val:
            letras += convertir_grupo(cen_val)

    return f"SON {letras.strip()} CON {decimal:02d}/100 {sufijo}"
