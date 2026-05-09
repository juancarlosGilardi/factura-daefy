# Namespaces UBL 2.1 y SUNAT
INVOICE_NS = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
CREDIT_NOTE_NS = "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"
DEBIT_NOTE_NS = "urn:oasis:names:specification:ubl:schema:xsd:DebitNote-2"
SUMMARY_NS = "urn:sunat:names:specification:ubl:peru:schema:xsd:SummaryDocuments-1"
VOIDED_NS = "urn:sunat:names:specification:ubl:peru:schema:xsd:VoidedDocuments-1"

CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
EXT = "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"
DS  = "http://www.w3.org/2000/09/xmldsig#"
SAC = "urn:sunat:names:specification:ubl:peru:schema:xsd:SunatAggregateComponents-1"

# nsmap base para Invoice/CreditNote/DebitNote
BASE_NSMAP = {
    "cac": CAC,
    "cbc": CBC,
    "ext": EXT,
    "ds":  DS,
    "sac": SAC,
}
