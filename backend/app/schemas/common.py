"""Schemas comunes y utilidades compartidas."""
from typing import Optional, Literal
from pydantic import BaseModel, Field


# Tipo de afectación IGV (SUNAT cat. 07) — usado por productos y detalles de comprobante.
TipoAfectacionIGV = Literal[
    "10", "11", "12", "13", "14", "15", "16", "17",  # Gravadas
    "20", "21",                                      # Exoneradas
    "30", "31", "32", "33", "34", "35", "36",        # Inafectas
    "40",                                            # Exportación
]


# SUNAT cat 09 — códigos válidos para nota de crédito
MOTIVOS_NC_VALIDOS = {
    "01", "02", "03", "04", "05", "06", "07",
    "08", "09", "10", "11", "12", "13",
}

# SUNAT cat 10 — códigos válidos para nota de débito
MOTIVOS_ND_VALIDOS = {"01", "02", "03", "10", "11"}

# SUNAT cat 54 — detracciones válidas (códigos comunes vigentes).
DETRACCIONES_VALIDAS = {
    "001", "002", "003", "004", "005", "007", "008", "009", "010", "011",
    "012", "013", "014", "015", "016", "017", "018", "019", "020", "021",
    "022", "023", "024", "025", "026", "027", "028", "029", "030", "031",
    "034", "035", "036", "037", "039", "040", "041", "042", "043", "044",
    "045", "046", "047", "048", "049",
}

# SUNAT cat 17 — tipo de operación (los más comunes).
TipoOperacion = Literal[
    "0101", "0112", "0113", "0200", "0201", "0202", "0203", "0204", "0205",
    "0206", "0207", "0208",
    "0301", "0302", "0303", "0304", "0305",
    "0401", "0402",
    "0501", "0502", "0503", "0504",
    "1001", "1002", "1003", "1004", "1005",
    "2001",
]

MotivoNcCodigo = Literal[
    "01", "02", "03", "04", "05", "06", "07",
    "08", "09", "10", "11", "12", "13",
]
MotivoNdCodigo = Literal["01", "02", "03", "10", "11"]


class MessageResponse(BaseModel):
    """Respuesta genérica simple."""
    ok: bool = True
    mensaje: str = ""


class TicketConsultaOut(BaseModel):
    """Resultado de consultar un ticket SUNAT."""
    ticket: str
    estado: str = Field(..., description="P/A/R")
    cdr_codigo: Optional[str] = None
    cdr_descripcion: Optional[str] = None
    cdr_path: Optional[str] = None


def validar_ruc_modulo11(ruc: str) -> bool:
    """Valida un RUC peruano de 11 dígitos con dígito verificador módulo 11."""
    if not ruc or len(ruc) != 11 or not ruc.isdigit():
        return False
    pesos = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
    suma = sum(int(d) * p for d, p in zip(ruc[:10], pesos))
    resto = suma % 11
    digito = (11 - resto) % 10
    return digito == int(ruc[10])


def validar_dni(dni: str) -> bool:
    return bool(dni) and len(dni) == 8 and dni.isdigit()


def validar_documento_identidad(tipo_documento: str, numero_documento: str) -> None:
    """Valida (tipo_documento, numero_documento) segun catalogo SUNAT 06.

    Lanza ValueError si la combinacion es invalida. No retorna nada
    (uso para llamar desde @model_validator de Pydantic).

    Tipos:
      0/A/B/C: sin validacion dura (solo no-vacio)
      1: DNI (8 digitos)
      4: Carnet de extranjeria (max 12 chars)
      6: RUC (11 digitos, prefijo 10/15/17/20, modulo 11)
      7: Pasaporte (max 12 chars)
    """
    td = tipo_documento
    nd = (numero_documento or "").strip()
    if not nd:
        raise ValueError("numero_documento es obligatorio")

    if td == "6":  # RUC
        if len(nd) != 11 or not nd.isdigit():
            raise ValueError("RUC debe tener 11 dígitos numéricos")
        if not nd.startswith(("10", "15", "17", "20")):
            raise ValueError("RUC debe empezar con 10, 15, 17 o 20")
        if not validar_ruc_modulo11(nd):
            raise ValueError("RUC inválido (dígito verificador módulo 11)")
    elif td == "1":  # DNI
        if not validar_dni(nd):
            raise ValueError("DNI debe ser de 8 dígitos numéricos")
    elif td == "4":  # CE
        if len(nd) > 12:
            raise ValueError("Carnet de extranjería: máximo 12 caracteres")
    elif td == "7":  # Pasaporte
        if len(nd) > 12:
            raise ValueError("Pasaporte: máximo 12 caracteres")
    elif td in ("0", "A", "B", "C"):  # No domiciliado / otros documentos
        if len(nd) < 3 or not any(c.isalnum() for c in nd):
            raise ValueError(
                "Número de documento de no domiciliado debe tener al menos 3 caracteres alfanuméricos"
            )
