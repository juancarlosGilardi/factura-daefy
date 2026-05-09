"""
Generador de XML para Factura (01) y Boleta (03).
Soporta: normal, exportacion, detraccion, percepcion,
         descuentos/cargos globales, descuentos por item,
         gratuitas, contado/credito con cuotas, guia de remision.
"""
from decimal import Decimal
from typing import Dict, Any, Tuple
from lxml import etree

from .namespaces import INVOICE_NS, CAC, CBC, EXT, DS, SAC, BASE_NSMAP
from .helpers import (q2, d, fmt_qty, obtener_tasa_igv, obtener_esquema_tributo,
                      es_gratuita, numero_a_letras, calcular_totales_items)
from ..xml_models.invoice import FacturaRequest
from ..xml_models.common import Item, AllowanceCharge


def calcular_totales(req: FacturaRequest) -> Dict[str, Any]:
    """Calcula todos los totales del comprobante."""
    return calcular_totales_items(
        items=req.items,
        descuentos_globales=req.descuentos_globales,
        detraccion=req.detraccion,
        anticipo=req.anticipo,
    )


def _list_id(req: FacturaRequest) -> str:
    if req.tipo_operacion:
        return req.tipo_operacion
    if req.detraccion:
        return "1001"
    if req.percepcion:
        return "2001"
    serie = req.serie.upper()
    if serie.startswith("FE") or serie.startswith("BE"):
        return "0200"
    if req.items and all(it.afectacion_igv == "40" for it in req.items):
        return "0200"
    if req.cliente.tipo_doc == "0":
        return "0401"
    return "0101"


def _e(parent: etree._Element, tag: str, text: str | None = None,
       attrib: dict | None = None) -> etree._Element:
    el = etree.SubElement(parent, tag, **(attrib or {}))
    if text is not None:
        el.text = text
    return el


def generar_xml_invoice(req: FacturaRequest, totales: Dict[str, Any]) -> etree._Element:
    nsmap = {None: INVOICE_NS, **BASE_NSMAP}
    root = etree.Element(f"{{{INVOICE_NS}}}Invoice", nsmap=nsmap)

    # UBLExtensions (placeholder para firma)
    ext_root = _e(root, f"{{{EXT}}}UBLExtensions")
    ext      = _e(ext_root, f"{{{EXT}}}UBLExtension")
    _e(ext, f"{{{EXT}}}ExtensionContent")

    _e(root, f"{{{CBC}}}UBLVersionID", "2.1")
    _e(root, f"{{{CBC}}}CustomizationID", "2.0")
    numero_str = str(req.numero).zfill(getattr(req, "numero_padding", 8))
    _e(root, f"{{{CBC}}}ID", f"{req.serie}-{numero_str}")
    _e(root, f"{{{CBC}}}IssueDate", req.fecha_emision)
    _e(root, f"{{{CBC}}}IssueTime", req.hora_emision)
    _e(root, f"{{{CBC}}}DueDate", req.fecha_vencimiento or req.fecha_emision)

    _e(root, f"{{{CBC}}}InvoiceTypeCode",
       req.tipo_documento,
       attrib={
           "listAgencyName": "PE:SUNAT",
           "listName": "Tipo de Documento",
           "listURI": "urn:pe:gob:sunat:cpe:see:gem:catalogos:catalogo01",
           "listID": _list_id(req),
           "name": "Tipo de Operacion",
           "listSchemeURI": "urn:pe:gob:sunat:cpe:see:gem:catalogos:catalogo51",
       })

    note_monto = totales["tax_inclusive"] if req.anticipo else totales["payable"]
    _e(root, f"{{{CBC}}}Note", numero_a_letras(float(note_monto), req.moneda),
       attrib={"languageLocaleID": "1000"})

    if req.percepcion:
        _e(root, f"{{{CBC}}}Note", "COMPROBANTE DE PERCEPCION",
           attrib={"languageLocaleID": "2000"})

    if req.detraccion:
        _e(root, f"{{{CBC}}}Note", "Operacion sujeta a detraccion",
           attrib={"languageLocaleID": "2006"})

    if any(i.afectacion_igv == "17" for i in req.items):
        _e(root, f"{{{CBC}}}Note", "Operacion sujeta al IVAP",
           attrib={"languageLocaleID": "2007"})

    if any(es_gratuita(i.afectacion_igv) for i in req.items):
        _e(root, f"{{{CBC}}}Note",
           "TRANSFERENCIA GRATUITA DE UN BIEN Y/O SERVICIO PRESTADO GRATUITAMENTE",
           attrib={"languageLocaleID": "1002"})

    _e(root, f"{{{CBC}}}DocumentCurrencyCode", req.moneda)

    # NO incluir PaymentExchangeRate — SUNAT no lo requiere y causa rechazo
    # El xml_generator.py de produccion (aceptado por SUNAT) no lo usa

    if req.orden_compra:
        adr = _e(root, f"{{{CAC}}}OrderReference")
        _e(adr, f"{{{CBC}}}ID", req.orden_compra)

    if req.guia_remision:
        ddr = _e(root, f"{{{CAC}}}DespatchDocumentReference")
        _e(ddr, f"{{{CBC}}}ID", req.guia_remision.serie_numero)
        _e(ddr, f"{{{CBC}}}DocumentTypeCode", req.guia_remision.tipo_doc)

    if req.incoterm:
        adr_inc = _e(root, f"{{{CAC}}}AdditionalDocumentReference")
        desc_inc = req.incoterm
        if req.puerto_embarque:
            desc_inc = f"{req.incoterm} - {req.puerto_embarque}"
        _e(adr_inc, f"{{{CBC}}}ID", "INCOTERMS")
        _e(adr_inc, f"{{{CBC}}}DocumentDescription", desc_inc)

    if req.anticipo:
        adr_ant = _e(root, f"{{{CAC}}}AdditionalDocumentReference")
        _e(adr_ant, f"{{{CBC}}}ID", req.anticipo.id_comprobante)
        _e(adr_ant, f"{{{CBC}}}DocumentTypeCode", req.anticipo.tipo_doc)
        _e(adr_ant, f"{{{CBC}}}DocumentStatusCode", "1")
        ip = _e(adr_ant, f"{{{CAC}}}IssuerParty")
        pi = _e(ip, f"{{{CAC}}}PartyIdentification")
        _e(pi, f"{{{CBC}}}ID", req.anticipo.ruc_emisor, attrib={"schemeID": "6"})

    _agregar_firma_ref(root, req.ruc_emisor, req.razon_social_emisor)
    _agregar_emisor(root, req)
    _agregar_cliente(root, req)

    if req.detraccion:
        pm = _e(root, f"{{{CAC}}}PaymentMeans")
        _e(pm, f"{{{CBC}}}ID", "Detraccion")
        _e(pm, f"{{{CBC}}}PaymentMeansCode", "001")
        pfa = _e(pm, f"{{{CAC}}}PayeeFinancialAccount")
        _e(pfa, f"{{{CBC}}}ID", req.detraccion.cuenta_banco_nacion)

    if req.detraccion:
        pt_det = _e(root, f"{{{CAC}}}PaymentTerms")
        _e(pt_det, f"{{{CBC}}}ID", "Detraccion")
        _e(pt_det, f"{{{CBC}}}PaymentMeansID", req.detraccion.codigo_bien_servicio)
        _e(pt_det, f"{{{CBC}}}PaymentPercent",
           f"{req.detraccion.porcentaje:.0f}")
        # SUNAT exige monto detraccion en PEN aunque la factura sea USD/EUR
        _monto_det = totales["monto_detraccion"]
        if req.moneda != "PEN" and getattr(req, "tipo_cambio", None) is not None:
            _tc = req.tipo_cambio.tasa if hasattr(req.tipo_cambio, "tasa") else req.tipo_cambio
            _monto_det = str(q2(d(_monto_det) * d(_tc)))
        _e(pt_det, f"{{{CBC}}}Amount", _monto_det,
           attrib={"currencyID": "PEN"})

    if req.percepcion:
        pt_per = _e(root, f"{{{CAC}}}PaymentTerms")
        _e(pt_per, f"{{{CBC}}}ID", "Percepcion")
        _e(pt_per, f"{{{CBC}}}Amount",
           str(q2(d(req.percepcion.monto_total))),
           attrib={"currencyID": req.moneda})

    if req.forma_pago.tipo == "Contado":
        pt = _e(root, f"{{{CAC}}}PaymentTerms")
        _e(pt, f"{{{CBC}}}ID", "FormaPago")
        _e(pt, f"{{{CBC}}}PaymentMeansID", "Contado")
    else:
        pt = _e(root, f"{{{CAC}}}PaymentTerms")
        _e(pt, f"{{{CBC}}}ID", "FormaPago")
        _e(pt, f"{{{CBC}}}PaymentMeansID", "Credito")
        _e(pt, f"{{{CBC}}}Amount",
           str(q2(d(req.forma_pago.monto_pendiente))),
           attrib={"currencyID": req.moneda})
        for cuota in req.forma_pago.cuotas:
            ptc = _e(root, f"{{{CAC}}}PaymentTerms")
            _e(ptc, f"{{{CBC}}}ID", "FormaPago")
            _e(ptc, f"{{{CBC}}}PaymentMeansID", f"Cuota{cuota.numero:03d}")
            _e(ptc, f"{{{CBC}}}Amount", str(q2(d(cuota.monto))),
               attrib={"currencyID": req.moneda})
            _e(ptc, f"{{{CBC}}}PaymentDueDate", cuota.fecha_vencimiento)

    if req.anticipo:
        pp = _e(root, f"{{{CAC}}}PrepaidPayment")
        _e(pp, f"{{{CBC}}}ID", "1")
        _e(pp, f"{{{CBC}}}PaidAmount", str(q2(d(req.anticipo.monto))),
           attrib={"currencyID": req.moneda})

    # Bug A6/H2: factura USD/EUR DEBE incluir PaymentExchangeRate (UBL 2.1).
    # SUNAT lo exige cuando moneda != PEN. Va despues de PaymentTerms y antes de
    # AllowanceCharge.
    if req.moneda != "PEN" and getattr(req, "tipo_cambio", None) is not None:
        tc_obj = req.tipo_cambio
        # Acepta ya sea un TipoCambio dataclass o un float plano
        if hasattr(tc_obj, "tasa"):
            tc_tasa = tc_obj.tasa
            tc_dest = getattr(tc_obj, "moneda_destino", "PEN")
            tc_fecha = getattr(tc_obj, "fecha", req.fecha_emision)
        else:
            tc_tasa = tc_obj
            tc_dest = "PEN"
            tc_fecha = req.fecha_emision
        per = _e(root, f"{{{CAC}}}PaymentExchangeRate")
        _e(per, f"{{{CBC}}}SourceCurrencyCode", req.moneda)
        _e(per, f"{{{CBC}}}TargetCurrencyCode", tc_dest)
        _e(per, f"{{{CBC}}}CalculationRate", str(q2(d(tc_tasa))))
        _e(per, f"{{{CBC}}}MathematicOperatorCode", "Multiply")
        _e(per, f"{{{CBC}}}Date", tc_fecha)

    if req.percepcion:
        ac = _e(root, f"{{{CAC}}}AllowanceCharge")
        _e(ac, f"{{{CBC}}}ChargeIndicator", "true")
        _e(ac, f"{{{CBC}}}AllowanceChargeReasonCode", "51")
        _e(ac, f"{{{CBC}}}MultiplierFactorNumeric",
           str(q2(d(req.percepcion.porcentaje) / Decimal("100"))))
        _e(ac, f"{{{CBC}}}Amount", str(q2(d(req.percepcion.monto_percepcion))),
           attrib={"currencyID": req.moneda})
        _e(ac, f"{{{CBC}}}BaseAmount", str(q2(d(req.percepcion.monto_base))),
           attrib={"currencyID": req.moneda})

    if req.descuentos_globales:
        for dg in req.descuentos_globales:
            ac = _e(root, f"{{{CAC}}}AllowanceCharge")
            _e(ac, f"{{{CBC}}}ChargeIndicator",
               "true" if dg.charge_indicator else "false")
            _e(ac, f"{{{CBC}}}AllowanceChargeReasonCode", dg.codigo_motivo)
            if dg.porcentaje is not None:
                _e(ac, f"{{{CBC}}}MultiplierFactorNumeric",
                   str(q2(d(dg.porcentaje) / Decimal("100"))))
            else:
                _e(ac, f"{{{CBC}}}MultiplierFactorNumeric", "1.00")
            _e(ac, f"{{{CBC}}}Amount", str(q2(d(dg.monto))),
               attrib={"currencyID": req.moneda})
            _e(ac, f"{{{CBC}}}BaseAmount", str(q2(d(dg.monto_base))),
               attrib={"currencyID": req.moneda})

    if req.anticipo:
        ac_ant = _e(root, f"{{{CAC}}}AllowanceCharge")
        _e(ac_ant, f"{{{CBC}}}ChargeIndicator", "false")
        _e(ac_ant, f"{{{CBC}}}AllowanceChargeReasonCode", "04")
        _e(ac_ant, f"{{{CBC}}}MultiplierFactorNumeric", "1.00")
        _e(ac_ant, f"{{{CBC}}}Amount", str(q2(d(req.anticipo.monto))),
           attrib={"currencyID": req.moneda})
        _e(ac_ant, f"{{{CBC}}}BaseAmount", str(q2(d(req.anticipo.monto))),
           attrib={"currencyID": req.moneda})

    _agregar_tax_total(root, req, totales)
    _agregar_monetary_total(root, req, totales)

    for idx, it in enumerate(req.items, 1):
        _agregar_invoice_line(root, req, it, idx)

    return root


def _agregar_firma_ref(root, ruc: str, razon: str):
    sig = _e(root, f"{{{CAC}}}Signature")
    _e(sig, f"{{{CBC}}}ID", ruc)
    sp  = _e(sig, f"{{{CAC}}}SignatoryParty")
    pid = _e(sp, f"{{{CAC}}}PartyIdentification")
    _e(pid, f"{{{CBC}}}ID", ruc)
    pn  = _e(sp, f"{{{CAC}}}PartyName")
    _e(pn, f"{{{CBC}}}Name", razon)
    dsa = _e(sig, f"{{{CAC}}}DigitalSignatureAttachment")
    er  = _e(dsa, f"{{{CAC}}}ExternalReference")
    _e(er, f"{{{CBC}}}URI", "#SignatureSP")


def _agregar_emisor(root, req: FacturaRequest):
    asp  = _e(root, f"{{{CAC}}}AccountingSupplierParty")
    party = _e(asp, f"{{{CAC}}}Party")
    pid  = _e(party, f"{{{CAC}}}PartyIdentification")
    _e(pid, f"{{{CBC}}}ID", req.ruc_emisor, attrib={"schemeID": "6"})
    pn   = _e(party, f"{{{CAC}}}PartyName")
    _e(pn, f"{{{CBC}}}Name", req.razon_social_emisor)
    ple  = _e(party, f"{{{CAC}}}PartyLegalEntity")
    _e(ple, f"{{{CBC}}}RegistrationName", req.razon_social_emisor)
    addr = _e(ple, f"{{{CAC}}}RegistrationAddress")
    _e(addr, f"{{{CBC}}}ID", req.ubigeo_emisor)
    _e(addr, f"{{{CBC}}}AddressTypeCode", req.codigo_local)
    if req.urbanizacion_emisor:
        _e(addr, f"{{{CBC}}}CitySubdivisionName", req.urbanizacion_emisor)
    if req.departamento_emisor:
        _e(addr, f"{{{CBC}}}CityName", req.departamento_emisor)
    if req.provincia_emisor:
        _e(addr, f"{{{CBC}}}CountrySubentity", req.provincia_emisor)
    if req.distrito_emisor:
        _e(addr, f"{{{CBC}}}District", req.distrito_emisor)
    al   = _e(addr, f"{{{CAC}}}AddressLine")
    _e(al, f"{{{CBC}}}Line", req.direccion_emisor)
    cty  = _e(addr, f"{{{CAC}}}Country")
    _e(cty, f"{{{CBC}}}IdentificationCode", "PE")


def _agregar_cliente(root, req: FacturaRequest):
    acp  = _e(root, f"{{{CAC}}}AccountingCustomerParty")
    party = _e(acp, f"{{{CAC}}}Party")
    pid  = _e(party, f"{{{CAC}}}PartyIdentification")
    _e(pid, f"{{{CBC}}}ID", req.cliente.numero,
       attrib={"schemeID": req.cliente.tipo_doc})
    ple  = _e(party, f"{{{CAC}}}PartyLegalEntity")
    _e(ple, f"{{{CBC}}}RegistrationName", req.cliente.razon_social)
    if req.cliente.direccion:
        reg = _e(ple, f"{{{CAC}}}RegistrationAddress")
        al  = _e(reg, f"{{{CAC}}}AddressLine")
        _e(al, f"{{{CBC}}}Line", req.cliente.direccion)
        cty = _e(reg, f"{{{CAC}}}Country")
        _e(cty, f"{{{CBC}}}IdentificationCode", "PE")
    if req.cliente.telefono or req.cliente.email:
        contact = _e(party, f"{{{CAC}}}Contact")
        if req.cliente.telefono:
            _e(contact, f"{{{CBC}}}Telephone", req.cliente.telefono)
        if req.cliente.email:
            _e(contact, f"{{{CBC}}}ElectronicMail", req.cliente.email)


def _agregar_tax_total(root, req: FacturaRequest, totales: Dict[str, Any]):
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


def _agregar_monetary_total(root, req: FacturaRequest, totales: Dict[str, Any]):
    lmt = _e(root, f"{{{CAC}}}LegalMonetaryTotal")
    _e(lmt, f"{{{CBC}}}LineExtensionAmount", totales["valor_venta"],
       attrib={"currencyID": req.moneda})
    _e(lmt, f"{{{CBC}}}TaxInclusiveAmount", totales["tax_inclusive"],
       attrib={"currencyID": req.moneda})
    if totales["allowance_total"]:
        _e(lmt, f"{{{CBC}}}AllowanceTotalAmount", totales["allowance_total"],
           attrib={"currencyID": req.moneda})
    if totales.get("prepaid_amount"):
        _e(lmt, f"{{{CBC}}}PrepaidAmount", totales["prepaid_amount"],
           attrib={"currencyID": req.moneda})
    if totales.get("payable_rounding"):
        _e(lmt, f"{{{CBC}}}PayableRoundingAmount", totales["payable_rounding"],
           attrib={"currencyID": req.moneda})
    # Sumar percepcion al PayableAmount (SUNAT: total a pagar incluye percepcion)
    payable_str = totales["payable"]
    if req.percepcion:
        payable_total = q2(d(payable_str) + d(req.percepcion.monto_percepcion))
        payable_str = str(payable_total)
    _e(lmt, f"{{{CBC}}}PayableAmount", payable_str,
       attrib={"currencyID": req.moneda})


def _agregar_invoice_line(root, req: FacturaRequest, it: Item, idx: int):
    cant = d(it.cantidad)
    vu   = q2(d(it.valor_unitario))
    afec = it.afectacion_igv

    tasa       = obtener_tasa_igv(afec)
    sid, sname, stype = obtener_esquema_tributo(afec)
    gratuita   = es_gratuita(afec)

    vv_bruto = q2(cant * vu)

    # Descuento por item: LineExtensionAmount debe ser NET
    desc_monto_item = Decimal("0.00")
    if it.descuento and not it.descuento.charge_indicator:
        desc_monto_item = q2(d(it.descuento.monto))
    vv = q2(vv_bruto - desc_monto_item)  # NET value for LineExtensionAmount

    isc_l = Decimal("0.00")
    if getattr(it, "isc_tasa", None) is not None:
        isc_l = q2(vv * d(it.isc_tasa) / Decimal("100"))

    igv_base = vv + isc_l
    igv_l    = q2(igv_base * tasa / Decimal("100"))

    icbper_l = Decimal("0.00")
    if getattr(it, "icbper_monto", None) is not None:
        icbper_l = q2(cant * d(it.icbper_monto))

    if gratuita:
        precio_ref  = vu
        price_type  = "02"
        price_unit  = Decimal("0.00")
    else:
        if isc_l > 0:
            precio_ref = q2((vv + isc_l + igv_l) / cant)
        elif tasa > 0:
            precio_ref = q2(vu * (1 + tasa / Decimal("100")))
        else:
            precio_ref = vu
        if isc_l == 0 and it.descuento and not it.descuento.charge_indicator:
            desc_unit  = q2(d(it.descuento.monto) / cant)
            precio_ref = q2(precio_ref - desc_unit)
        price_type = "01"
        price_unit = vu

    line = _e(root, f"{{{CAC}}}InvoiceLine")
    _e(line, f"{{{CBC}}}ID", str(idx))
    _e(line, f"{{{CBC}}}InvoicedQuantity", fmt_qty(cant),
       attrib={"unitCode": it.unidad})
    # Bug B1/H4: para items gratuitos, LineExtensionAmount DEBE ser 0.00.
    # SUNAT 2426: "El valor del tributo es invalido" si vv > 0 con afectacion gratuita.
    line_ext_amount = "0.00" if gratuita else str(vv)
    _e(line, f"{{{CBC}}}LineExtensionAmount", line_ext_amount,
       attrib={"currencyID": req.moneda})

    pr   = _e(line, f"{{{CAC}}}PricingReference")
    acp2 = _e(pr, f"{{{CAC}}}AlternativeConditionPrice")
    _e(acp2, f"{{{CBC}}}PriceAmount", str(q2(precio_ref)),
       attrib={"currencyID": req.moneda})
    _e(acp2, f"{{{CBC}}}PriceTypeCode", price_type)

    if it.descuento and desc_monto_item > 0:
        desc = it.descuento
        ac   = _e(line, f"{{{CAC}}}AllowanceCharge")
        _e(ac, f"{{{CBC}}}ChargeIndicator",
           "true" if desc.charge_indicator else "false")
        _e(ac, f"{{{CBC}}}AllowanceChargeReasonCode", desc.codigo_motivo)
        pct = desc.porcentaje if desc.porcentaje is not None else (d(desc.monto) / d(desc.monto_base) * Decimal("100"))
        factor = q2(d(pct) / Decimal("100"))  # SUNAT expects factor (0-1), not percentage
        _e(ac, f"{{{CBC}}}MultiplierFactorNumeric", str(factor))
        _e(ac, f"{{{CBC}}}Amount", str(q2(desc_monto_item)),
           attrib={"currencyID": req.moneda})
        _e(ac, f"{{{CBC}}}BaseAmount", str(vv_bruto),
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
    # Bug B3/H3: SUNAT espera Percent con decimales (18.00 no 18)
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
    if getattr(it, "codigo_unspsc", None):
        cc = _e(item_tag, f"{{{CAC}}}CommodityClassification")
        _e(cc, f"{{{CBC}}}ItemClassificationCode", it.codigo_unspsc)
    sii  = _e(item_tag, f"{{{CAC}}}SellersItemIdentification")
    _e(sii, f"{{{CBC}}}ID", it.codigo)

    prc  = _e(line, f"{{{CAC}}}Price")
    _e(prc, f"{{{CBC}}}PriceAmount", str(price_unit),
       attrib={"currencyID": req.moneda})
