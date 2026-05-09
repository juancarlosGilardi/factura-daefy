"""Generador de XML para Resumen Diario (SummaryDocuments)."""
from lxml import etree
from ..xml_models.summary import ResumenDiarioRequest, DocumentoResumen

NSMAP = {
    None: "urn:sunat:names:specification:ubl:peru:schema:xsd:SummaryDocuments-1",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2",
    "sac": "urn:sunat:names:specification:ubl:peru:schema:xsd:SunatAggregateComponents-1",
    "ds":  "http://www.w3.org/2000/09/xmldsig#",
}

EXT = NSMAP["ext"]
CBC = NSMAP["cbc"]
CAC = NSMAP["cac"]
SAC = NSMAP["sac"]


def _e(parent, tag, text=None, attrib=None):
    el = etree.SubElement(parent, tag, **(attrib or {}))
    if text is not None:
        el.text = text
    return el


def generar_xml_resumen(req: ResumenDiarioRequest) -> etree._Element:
    root = etree.Element("SummaryDocuments", nsmap=NSMAP)

    ue   = _e(root, f"{{{EXT}}}UBLExtensions")
    uext = _e(ue, f"{{{EXT}}}UBLExtension")
    _e(uext, f"{{{EXT}}}ExtensionContent")

    _e(root, f"{{{CBC}}}UBLVersionID", "2.0")
    _e(root, f"{{{CBC}}}CustomizationID", "1.1")

    fecha_str = req.fecha_documentos.replace("-", "")
    _e(root, f"{{{CBC}}}ID", f"RC-{fecha_str}-{str(req.correlativo).zfill(3)}")
    _e(root, f"{{{CBC}}}ReferenceDate", req.fecha_documentos)
    _e(root, f"{{{CBC}}}IssueDate", req.fecha_comunicacion)

    sig = _e(root, f"{{{CAC}}}Signature")
    _e(sig, f"{{{CBC}}}ID", f"RC-{fecha_str}-{str(req.correlativo).zfill(3)}")
    sp  = _e(sig, f"{{{CAC}}}SignatoryParty")
    pid = _e(sp, f"{{{CAC}}}PartyIdentification")
    _e(pid, f"{{{CBC}}}ID", req.ruc_emisor)
    pn  = _e(sp, f"{{{CAC}}}PartyName")
    _e(pn, f"{{{CBC}}}Name", req.razon_social_emisor)
    dsa = _e(sig, f"{{{CAC}}}DigitalSignatureAttachment")
    er  = _e(dsa, f"{{{CAC}}}ExternalReference")
    _e(er, f"{{{CBC}}}URI", "#SignatureSP")

    supplier = _e(root, f"{{{CAC}}}AccountingSupplierParty")
    _e(supplier, f"{{{CBC}}}CustomerAssignedAccountID", req.ruc_emisor)
    _e(supplier, f"{{{CBC}}}AdditionalAccountID", "6")
    party = _e(supplier, f"{{{CAC}}}Party")
    ple   = _e(party, f"{{{CAC}}}PartyLegalEntity")
    _e(ple, f"{{{CBC}}}RegistrationName", req.razon_social_emisor)

    for idx, doc in enumerate(req.documentos, 1):
        _agregar_linea(root, doc, idx)

    return root


def _agregar_linea(root, doc: DocumentoResumen, line_id: int):
    line = _e(root, f"{{{SAC}}}SummaryDocumentsLine")
    _e(line, f"{{{CBC}}}LineID", str(line_id))
    _e(line, f"{{{CBC}}}DocumentTypeCode", doc.tipo_doc)
    _e(line, f"{{{CBC}}}ID", doc.serie_numero)

    if doc.tipo_doc in ("07", "08") and doc.doc_referencia:
        bref = _e(line, f"{{{CAC}}}BillingReference")
        idr  = _e(bref, f"{{{CAC}}}InvoiceDocumentReference")
        _e(idr, f"{{{CBC}}}ID", doc.doc_referencia)
        _e(idr, f"{{{CBC}}}DocumentTypeCode", doc.tipo_doc_referencia)

    acp   = _e(line, f"{{{CAC}}}AccountingCustomerParty")
    _e(acp, f"{{{CBC}}}CustomerAssignedAccountID", doc.num_doc_cliente)
    _e(acp, f"{{{CBC}}}AdditionalAccountID", doc.tipo_doc_cliente)

    status = _e(line, f"{{{CAC}}}Status")
    _e(status, f"{{{CBC}}}ConditionCode", doc.condicion)

    if doc.condicion != "3":
        _e(line, f"{{{SAC}}}TotalAmount", f"{doc.total:.2f}",
           attrib={"currencyID": doc.moneda})

        _agregar_monto(line, "01", doc.gravada, doc.moneda)
        if doc.exonerada:
            _agregar_monto(line, "02", doc.exonerada, doc.moneda)
        if doc.inafecta:
            _agregar_monto(line, "03", doc.inafecta, doc.moneda)
        if doc.exportacion:
            _agregar_monto(line, "04", doc.exportacion, doc.moneda)
        if doc.gratuita:
            _agregar_monto(line, "05", doc.gratuita, doc.moneda)

        if doc.igv:
            _agregar_tributo(line, "1000", "IGV", "VAT", doc.igv, doc.gravada, doc.moneda)
        if doc.isc:
            _agregar_tributo(line, "2000", "ISC", "EXC", doc.isc, 0.0, doc.moneda)
        if doc.otros_tributos:
            _agregar_tributo(line, "9999", "OTROS", "OTH", doc.otros_tributos, 0.0, doc.moneda)


def _agregar_monto(line, instruction_id: str, monto: float, moneda: str):
    if monto == 0:
        return
    am = _e(line, f"{{{SAC}}}BillingPayment")
    _e(am, f"{{{CBC}}}PaidAmount", f"{monto:.2f}",
       attrib={"currencyID": moneda})
    _e(am, f"{{{CBC}}}InstructionID", instruction_id)


def _agregar_tributo(line, scheme_id: str, name: str, type_code: str,
                     amount: float, taxable: float, moneda: str):
    tt  = _e(line, f"{{{CAC}}}TaxTotal")
    _e(tt, f"{{{CBC}}}TaxAmount", f"{amount:.2f}",
       attrib={"currencyID": moneda})
    sub = _e(tt, f"{{{CAC}}}TaxSubtotal")
    _e(sub, f"{{{CBC}}}TaxAmount", f"{amount:.2f}",
       attrib={"currencyID": moneda})
    tc  = _e(sub, f"{{{CAC}}}TaxCategory")
    # Tasa del tributo (requerida por SUNAT)
    if scheme_id == "1000":
        _e(tc, f"{{{CBC}}}Percent", "18.00")
    elif scheme_id == "2000":
        _e(tc, f"{{{CBC}}}Percent", "0.00")
    ts  = _e(tc, f"{{{CAC}}}TaxScheme")
    _e(ts, f"{{{CBC}}}ID", scheme_id)
    _e(ts, f"{{{CBC}}}Name", name)
    _e(ts, f"{{{CBC}}}TaxTypeCode", type_code)
