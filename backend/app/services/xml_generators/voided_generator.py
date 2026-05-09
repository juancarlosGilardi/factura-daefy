"""Generador de XML para Comunicacion de Baja (VoidedDocuments)."""
from lxml import etree
from ..xml_models.voided import ComunicacionBajaRequest, DocumentoBaja

NSMAP = {
    None: "urn:sunat:names:specification:ubl:peru:schema:xsd:VoidedDocuments-1",
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


def generar_xml_baja(req: ComunicacionBajaRequest) -> etree._Element:
    root = etree.Element("VoidedDocuments", nsmap=NSMAP)

    ue   = _e(root, f"{{{EXT}}}UBLExtensions")
    uext = _e(ue, f"{{{EXT}}}UBLExtension")
    _e(uext, f"{{{EXT}}}ExtensionContent")

    _e(root, f"{{{CBC}}}UBLVersionID", "2.0")
    _e(root, f"{{{CBC}}}CustomizationID", "1.0")

    fecha_str = req.fecha_comunicacion.replace("-", "")
    _e(root, f"{{{CBC}}}ID", f"RA-{fecha_str}-{req.correlativo.zfill(5)}")
    _e(root, f"{{{CBC}}}ReferenceDate", req.fecha_documentos)
    _e(root, f"{{{CBC}}}IssueDate", req.fecha_comunicacion)

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

    supplier = _e(root, f"{{{CAC}}}AccountingSupplierParty")
    _e(supplier, f"{{{CBC}}}CustomerAssignedAccountID", req.ruc_emisor)
    _e(supplier, f"{{{CBC}}}AdditionalAccountID", "6")
    party = _e(supplier, f"{{{CAC}}}Party")
    ple   = _e(party, f"{{{CAC}}}PartyLegalEntity")
    _e(ple, f"{{{CBC}}}RegistrationName", req.razon_social_emisor)

    for idx, doc in enumerate(req.documentos, 1):
        _agregar_linea(root, doc, idx)

    return root


def _agregar_linea(root, doc: DocumentoBaja, line_id: int):
    line = _e(root, f"{{{SAC}}}VoidedDocumentsLine")
    _e(line, f"{{{CBC}}}LineID", str(line_id))
    _e(line, f"{{{CBC}}}DocumentTypeCode", doc.tipo_doc)
    # SUNAT requiere estos 3 campos bajo sac: (no cbc:)
    # Fix: error 0306 "No se puede leer (parsear) el archivo XML"
    _e(line, f"{{{SAC}}}DocumentSerialID", doc.serie)
    _e(line, f"{{{SAC}}}DocumentNumberID", doc.correlativo)
    _e(line, f"{{{SAC}}}VoidReasonDescription", doc.motivo)
