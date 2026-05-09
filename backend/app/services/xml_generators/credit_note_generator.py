"""Generador de XML para Nota de Credito (07) — CreditNote UBL 2.1."""
from decimal import Decimal
from typing import Dict, Any
from lxml import etree

from .namespaces import CREDIT_NOTE_NS, CAC, CBC, EXT, BASE_NSMAP
from .helpers import (q2, d, fmt_qty, obtener_tasa_igv, obtener_esquema_tributo,
                      es_gratuita, numero_a_letras, calcular_totales_items)
from ..xml_models.credit_debit_note import NotaCreditoRequest
from ..xml_models.common import Item, AllowanceCharge


def calcular_totales(req: NotaCreditoRequest) -> Dict[str, Any]:
    return calcular_totales_items(
        items=req.items,
        descuentos_globales=req.descuentos_globales,
    )



def _es_ruc_peruano_nc(numero):
    if not numero or len(numero) != 11:
        return False
    return numero.startswith(("10", "15", "17", "20"))


def _es_no_domiciliado_cliente(cliente):
    """Cliente no-domiciliado: tipo 0/4/7/B siempre, o tipo 6 con numero no-RUC peruano."""
    tipo = cliente.tipo_doc
    numero = cliente.numero or ""
    if tipo in ("0", "4", "7", "B"):
        return True
    if tipo == "6" and not _es_ruc_peruano_nc(numero):
        return True
    return False


def _e(parent, tag, text=None, attrib=None):
    el = etree.SubElement(parent, tag, **(attrib or {}))
    if text is not None:
        el.text = text
    return el


def generar_xml_credit_note(req: NotaCreditoRequest,
                             totales: Dict[str, Any]) -> etree._Element:
    nsmap = {None: CREDIT_NOTE_NS, **BASE_NSMAP}
    root  = etree.Element(f"{{{CREDIT_NOTE_NS}}}CreditNote", nsmap=nsmap)

    ext_root = _e(root, f"{{{EXT}}}UBLExtensions")
    ext      = _e(ext_root, f"{{{EXT}}}UBLExtension")
    _e(ext, f"{{{EXT}}}ExtensionContent")

    _e(root, f"{{{CBC}}}UBLVersionID", "2.1")
    _e(root, f"{{{CBC}}}CustomizationID", "2.0")
    numero_str = str(req.numero).zfill(getattr(req, "numero_padding", 8))
    _e(root, f"{{{CBC}}}ID", f"{req.serie}-{numero_str}")
    _e(root, f"{{{CBC}}}IssueDate", req.fecha_emision)
    _e(root, f"{{{CBC}}}IssueTime", req.hora_emision)

    _e(root, f"{{{CBC}}}Note",
       numero_a_letras(float(totales["payable"]), req.moneda),
       attrib={"languageLocaleID": "1000"})

    if any(es_gratuita(i.afectacion_igv) for i in req.items):
        _e(root, f"{{{CBC}}}Note",
           "TRANSFERENCIA GRATUITA DE UN BIEN Y/O SERVICIO PRESTADO GRATUITAMENTE",
           attrib={"languageLocaleID": "1002"})

    _e(root, f"{{{CBC}}}DocumentCurrencyCode", req.moneda)

    # UBL 2.1: PaymentExchangeRate va DESPUÉS de AccountingCustomerParty (más abajo).
    # Si lo ponemos aquí, SUNAT rechaza con código -1: "found PaymentExchangeRate, but next item should be AccountingSupplierParty"

    dr = req.documento_referencia
    disc = _e(root, f"{{{CAC}}}DiscrepancyResponse")
    _e(disc, f"{{{CBC}}}ReferenceID", dr.id)
    _e(disc, f"{{{CBC}}}ResponseCode", dr.codigo_motivo)
    _e(disc, f"{{{CBC}}}Description", dr.descripcion_motivo)

    bref = _e(root, f"{{{CAC}}}BillingReference")
    idr  = _e(bref, f"{{{CAC}}}InvoiceDocumentReference")
    _e(idr, f"{{{CBC}}}ID", dr.id)
    _e(idr, f"{{{CBC}}}DocumentTypeCode", dr.tipo_doc)

    if req.guia_remision:
        ddr = _e(root, f"{{{CAC}}}DespatchDocumentReference")
        _e(ddr, f"{{{CBC}}}ID", req.guia_remision.serie_numero)
        _e(ddr, f"{{{CBC}}}DocumentTypeCode", req.guia_remision.tipo_doc)

    sig = _e(root, f"{{{CAC}}}Signature")
    _e(sig, f"{{{CBC}}}ID", req.ruc_emisor)
    sp  = _e(sig, f"{{{CAC}}}SignatoryParty")
    pid = _e(sp, f"{{{CAC}}}PartyIdentification")
    _e(pid, f"{{{CBC}}}ID", req.ruc_emisor)
    pn  = _e(sp, f"{{{CAC}}}PartyName")
    _e(pn, f"{{{CBC}}}Name", req.razon_social_emisor)
    dsa = _e(sig, f"{{{CAC}}}DigitalSignatureAttachment")
    er  = _e(dsa, f"{{{CAC}}}ExternalReference")
    _e(er, f"{{{CBC}}}URI", "#SignatureSP")

    asp   = _e(root, f"{{{CAC}}}AccountingSupplierParty")
    party = _e(asp, f"{{{CAC}}}Party")
    pid2  = _e(party, f"{{{CAC}}}PartyIdentification")
    _e(pid2, f"{{{CBC}}}ID", req.ruc_emisor, attrib={"schemeID": "6"})
    pn2   = _e(party, f"{{{CAC}}}PartyName")
    _e(pn2, f"{{{CBC}}}Name", req.razon_social_emisor)
    ple   = _e(party, f"{{{CAC}}}PartyLegalEntity")
    _e(ple, f"{{{CBC}}}RegistrationName", req.razon_social_emisor)
    addr  = _e(ple, f"{{{CAC}}}RegistrationAddress")
    _e(addr, f"{{{CBC}}}ID", req.ubigeo_emisor)
    _e(addr, f"{{{CBC}}}AddressTypeCode", req.codigo_local)
    al    = _e(addr, f"{{{CAC}}}AddressLine")
    _e(al, f"{{{CBC}}}Line", req.direccion_emisor)
    cty   = _e(addr, f"{{{CAC}}}Country")
    _e(cty, f"{{{CBC}}}IdentificationCode", "PE")

    acp   = _e(root, f"{{{CAC}}}AccountingCustomerParty")
    party2 = _e(acp, f"{{{CAC}}}Party")
    pid3  = _e(party2, f"{{{CAC}}}PartyIdentification")
    _scheme_cli = "0" if _es_no_domiciliado_cliente(req.cliente) else req.cliente.tipo_doc
    _e(pid3, f"{{{CBC}}}ID", req.cliente.numero,
       attrib={"schemeID": _scheme_cli})
    ple2  = _e(party2, f"{{{CAC}}}PartyLegalEntity")
    _e(ple2, f"{{{CBC}}}RegistrationName", req.cliente.razon_social)
    # Bug C13/H10: incluir RegistrationAddress del cliente si tiene direccion
    if getattr(req.cliente, "direccion", None):
        reg2 = _e(ple2, f"{{{CAC}}}RegistrationAddress")
        al2  = _e(reg2, f"{{{CAC}}}AddressLine")
        _e(al2, f"{{{CBC}}}Line", req.cliente.direccion)
        cty2 = _e(reg2, f"{{{CAC}}}Country")
        _e(cty2, f"{{{CBC}}}IdentificationCode", "PE")

    # NC no requiere PaymentTerms segun SUNAT

    # UBL 2.1: PaymentExchangeRate aquí (después de AccountingCustomerParty/PayeeParty/TaxRepresentativeParty/PaymentMeans/PaymentTerms)
    if req.tipo_cambio:
        tc = req.tipo_cambio
        per = _e(root, f"{{{CAC}}}PaymentExchangeRate")
        _e(per, f"{{{CBC}}}SourceCurrencyCode", req.moneda)
        _e(per, f"{{{CBC}}}TargetCurrencyCode", tc.moneda_destino)
        _e(per, f"{{{CBC}}}CalculationRate", str(q2(d(tc.tasa))))
        _e(per, f"{{{CBC}}}MathematicOperatorCode", "Multiply")
        _e(per, f"{{{CBC}}}Date", tc.fecha)

    if req.descuentos_globales:
        for dg in req.descuentos_globales:
            ac = _e(root, f"{{{CAC}}}AllowanceCharge")
            _e(ac, f"{{{CBC}}}ChargeIndicator",
               "true" if dg.charge_indicator else "false")
            _e(ac, f"{{{CBC}}}AllowanceChargeReasonCode", dg.codigo_motivo)
            pct = dg.porcentaje if dg.porcentaje is not None else (
                d(dg.monto) / d(dg.monto_base) * Decimal("100"))
            _e(ac, f"{{{CBC}}}MultiplierFactorNumeric", str(q2(d(pct) / Decimal("100"))))
            _e(ac, f"{{{CBC}}}Amount", str(q2(d(dg.monto))),
               attrib={"currencyID": req.moneda})
            _e(ac, f"{{{CBC}}}BaseAmount", str(q2(d(dg.monto_base))),
               attrib={"currencyID": req.moneda})

    tt = _e(root, f"{{{CAC}}}TaxTotal")
    _e(tt, f"{{{CBC}}}TaxAmount", totales["tributos_total"],
       attrib={"currencyID": req.moneda})
    for tid in ["2000", "1000", "1016", "9997", "9998", "9995", "9996", "7152"]:
        if tid not in totales["taxes"]:
            continue
        data = totales["taxes"][tid]
        sub  = _e(tt, f"{{{CAC}}}TaxSubtotal")
        if data["taxable"] is not None:
            _e(sub, f"{{{CBC}}}TaxableAmount", data["taxable"],
               attrib={"currencyID": req.moneda})
        _e(sub, f"{{{CBC}}}TaxAmount", data["amount"],
           attrib={"currencyID": req.moneda})
        tc   = _e(sub, f"{{{CAC}}}TaxCategory")
        ts   = _e(tc, f"{{{CAC}}}TaxScheme")
        _e(ts, f"{{{CBC}}}ID", tid)
        _e(ts, f"{{{CBC}}}Name", data["name"])
        _e(ts, f"{{{CBC}}}TaxTypeCode", data["type"])

    lmt = _e(root, f"{{{CAC}}}LegalMonetaryTotal")
    _e(lmt, f"{{{CBC}}}LineExtensionAmount", totales["valor_venta"],
       attrib={"currencyID": req.moneda})
    _e(lmt, f"{{{CBC}}}TaxInclusiveAmount", totales["tax_inclusive"],
       attrib={"currencyID": req.moneda})
    if totales["allowance_total"]:
        _e(lmt, f"{{{CBC}}}AllowanceTotalAmount", totales["allowance_total"],
           attrib={"currencyID": req.moneda})
    if totales.get("payable_rounding"):
        _e(lmt, f"{{{CBC}}}PayableRoundingAmount", totales["payable_rounding"],
           attrib={"currencyID": req.moneda})
    _e(lmt, f"{{{CBC}}}PayableAmount", totales["payable"],
       attrib={"currencyID": req.moneda})

    for idx, it in enumerate(req.items, 1):
        _agregar_cn_line(root, req, it, idx)

    return root


def _agregar_cn_line(root, req: NotaCreditoRequest, it: Item, idx: int):
    cant = d(it.cantidad)
    vu   = q2(d(it.valor_unitario))
    afec = it.afectacion_igv

    tasa       = obtener_tasa_igv(afec)
    sid, sname, stype = obtener_esquema_tributo(afec)
    gratuita   = es_gratuita(afec)

    vv = q2(cant * vu)

    isc_l = Decimal("0.00")
    if getattr(it, "isc_tasa", None) is not None:
        isc_l = q2(vv * d(it.isc_tasa) / Decimal("100"))

    igv_base = vv + isc_l
    igv_l    = q2(igv_base * tasa / Decimal("100"))

    icbper_l = Decimal("0.00")
    if getattr(it, "icbper_monto", None) is not None:
        icbper_l = q2(cant * d(it.icbper_monto))

    if gratuita:
        precio_ref = vu; price_type = "02"; price_unit = Decimal("0.00")
    else:
        if isc_l > 0:
            precio_ref = q2((vv + isc_l + igv_l) / cant)
        elif tasa > 0:
            precio_ref = q2(vu * (1 + tasa / Decimal("100")))
        else:
            precio_ref = vu
        if isc_l == 0 and it.descuento and not it.descuento.charge_indicator:
            precio_ref = q2(precio_ref - d(it.descuento.monto) / cant)
        price_type = "01"; price_unit = vu

    line = _e(root, f"{{{CAC}}}CreditNoteLine")
    _e(line, f"{{{CBC}}}ID", str(idx))
    _e(line, f"{{{CBC}}}CreditedQuantity", fmt_qty(cant),
       attrib={"unitCode": it.unidad})
    # Bug B1/H4: para items gratuitos, LineExtensionAmount DEBE ser 0.00
    line_ext_amount = "0.00" if gratuita else str(vv)
    _e(line, f"{{{CBC}}}LineExtensionAmount", line_ext_amount,
       attrib={"currencyID": req.moneda})

    pr   = _e(line, f"{{{CAC}}}PricingReference")
    acp2 = _e(pr, f"{{{CAC}}}AlternativeConditionPrice")
    _e(acp2, f"{{{CBC}}}PriceAmount", str(q2(precio_ref)),
       attrib={"currencyID": req.moneda})
    _e(acp2, f"{{{CBC}}}PriceTypeCode", price_type)

    if it.descuento:
        desc = it.descuento
        ac   = _e(line, f"{{{CAC}}}AllowanceCharge")
        _e(ac, f"{{{CBC}}}ChargeIndicator",
           "true" if desc.charge_indicator else "false")
        _e(ac, f"{{{CBC}}}AllowanceChargeReasonCode", desc.codigo_motivo)
        pct = desc.porcentaje if desc.porcentaje is not None else (
            d(desc.monto) / d(desc.monto_base) * Decimal("100"))
        factor = q2(d(pct) / Decimal("100"))
        _e(ac, f"{{{CBC}}}MultiplierFactorNumeric", str(factor))
        _e(ac, f"{{{CBC}}}Amount", str(q2(d(desc.monto))),
           attrib={"currencyID": req.moneda})
        _e(ac, f"{{{CBC}}}BaseAmount", str(q2(d(desc.monto_base))),
           attrib={"currencyID": req.moneda})

    ltt  = _e(line, f"{{{CAC}}}TaxTotal")
    _e(ltt, f"{{{CBC}}}TaxAmount", str(q2(isc_l + igv_l + icbper_l)),
       attrib={"currencyID": req.moneda})
    if isc_l > 0:
        lts_isc  = _e(ltt, f"{{{CAC}}}TaxSubtotal")
        _e(lts_isc, f"{{{CBC}}}TaxableAmount", str(vv),
           attrib={"currencyID": req.moneda})
        _e(lts_isc, f"{{{CBC}}}TaxAmount", str(isc_l),
           attrib={"currencyID": req.moneda})
        ltc_isc  = _e(lts_isc, f"{{{CAC}}}TaxCategory")
        # Bug B3/H3: SUNAT espera Percent con decimales
        _e(ltc_isc, f"{{{CBC}}}Percent", f"{float(d(it.isc_tasa)):.2f}")
        _e(ltc_isc, f"{{{CBC}}}TierRange", it.isc_sistema)
        ltsch_isc = _e(ltc_isc, f"{{{CAC}}}TaxScheme")
        _e(ltsch_isc, f"{{{CBC}}}ID", "2000")
        _e(ltsch_isc, f"{{{CBC}}}Name", "ISC")
        _e(ltsch_isc, f"{{{CBC}}}TaxTypeCode", "EXC")
    lts  = _e(ltt, f"{{{CAC}}}TaxSubtotal")
    _e(lts, f"{{{CBC}}}TaxableAmount", str(q2(vv + isc_l)),
       attrib={"currencyID": req.moneda})
    _e(lts, f"{{{CBC}}}TaxAmount", str(igv_l),
       attrib={"currencyID": req.moneda})
    ltc  = _e(lts, f"{{{CAC}}}TaxCategory")
    # Bug B3/H3: SUNAT espera Percent con decimales
    _e(ltc, f"{{{CBC}}}Percent", f"{float(tasa):.2f}")
    _e(ltc, f"{{{CBC}}}TaxExemptionReasonCode", afec)
    ltsch = _e(ltc, f"{{{CAC}}}TaxScheme")
    _e(ltsch, f"{{{CBC}}}ID", sid)
    _e(ltsch, f"{{{CBC}}}Name", sname)
    _e(ltsch, f"{{{CBC}}}TaxTypeCode", stype)
    if icbper_l > 0:
        lts2  = _e(ltt, f"{{{CAC}}}TaxSubtotal")
        _e(lts2, f"{{{CBC}}}TaxAmount", str(icbper_l),
           attrib={"currencyID": req.moneda})
        _e(lts2, f"{{{CBC}}}BaseUnitMeasure", fmt_qty(cant),
           attrib={"unitCode": it.unidad})
        ltc2  = _e(lts2, f"{{{CAC}}}TaxCategory")
        _e(ltc2, f"{{{CBC}}}PerUnitAmount", str(d(it.icbper_monto)),
           attrib={"currencyID": req.moneda})
        ltsch2 = _e(ltc2, f"{{{CAC}}}TaxScheme")
        _e(ltsch2, f"{{{CBC}}}ID", "7152")
        _e(ltsch2, f"{{{CBC}}}Name", "ICBPER")
        _e(ltsch2, f"{{{CBC}}}TaxTypeCode", "OTH")

    item_tag = _e(line, f"{{{CAC}}}Item")
    _e(item_tag, f"{{{CBC}}}Description", it.descripcion)
    sii  = _e(item_tag, f"{{{CAC}}}SellersItemIdentification")
    _e(sii, f"{{{CBC}}}ID", it.codigo)

    prc  = _e(line, f"{{{CAC}}}Price")
    _e(prc, f"{{{CBC}}}PriceAmount", str(price_unit),
       attrib={"currencyID": req.moneda})
