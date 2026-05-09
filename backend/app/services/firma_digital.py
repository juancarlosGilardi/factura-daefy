"""
Firma XML UBL para SUNAT.
Acepta certificado como bytes (base64 decodificado) o como ruta a .pfx.
Algoritmo: RSA-SHA1 (requerido por SUNAT).

Bug C2/F4: NO migrar a SHA256 sin probar contra SUNAT beta.
Aunque SHA1 está deprecated en general, SUNAT actualmente lo exige y rechaza
firmas SHA256 con validación 2335. Si en el futuro SUNAT publica soporte SHA256,
migrar reemplazando los algoritmos en _firmar_xml() (lineas ~120, 130, 144, 150).
"""
import base64
import hashlib
import os
import re
from lxml import etree
from cryptography.hazmat.primitives.serialization import pkcs12, Encoding
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend
from cryptography.x509 import NameOID


class CertificateError(Exception):
    """Error al cargar o validar el certificado digital."""


class SignatureError(Exception):
    """Error durante el proceso de firma del XML."""


NS_EXT = "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"
NS_DS  = "http://www.w3.org/2000/09/xmldsig#"


def _cargar_certificado(pfx_data: bytes, password: str):
    """
    Carga clave privada y certificado desde bytes PFX.
    Devuelve (private_key, cert_single_line, subject_name).
    """
    try:
        private_key, certificate, _ = pkcs12.load_key_and_certificates(
            pfx_data, password.encode(), backend=default_backend()
        )
    except Exception as e:
        raise CertificateError(f"No se pudo cargar el certificado PFX: {e}") from e

    if not certificate or not private_key:
        raise CertificateError("El archivo PFX no contiene clave privada o certificado")

    subject = certificate.subject

    def get_attr(oid, default=""):
        try:
            attrs = subject.get_attributes_for_oid(oid)
            return attrs[0].value if attrs else default
        except Exception:
            return default

    country  = get_attr(NameOID.COUNTRY_NAME, "PE")
    locality = get_attr(NameOID.LOCALITY_NAME, "LIMA")
    org      = get_attr(NameOID.ORGANIZATION_NAME, "")
    cn       = get_attr(NameOID.COMMON_NAME, "")
    email    = get_attr(NameOID.EMAIL_ADDRESS, "")
    street   = get_attr(NameOID.STREET_ADDRESS, "")

    ruc = ""
    if cn:
        nums = re.findall(r"\d{11}", cn)
        if nums:
            ruc = nums[0]

    parts = []
    if country:
        parts.append(f"C={country}")
    if locality:
        parts.append(f"L={locality}")
    if org:
        parts.append(f"O={org}")
    if ruc:
        parts.append(f"OU=FACTURA ELECTRONICA RUC {ruc}")
    if cn:
        parts.append(f"CN={cn}")
    if email:
        parts.append(f"E={email}")
    if street:
        parts.append(f"STREET={street}")
    subject_name = ", ".join(parts)

    cert_pem = certificate.public_bytes(Encoding.PEM).decode("utf-8")
    cert_single = (cert_pem
                   .replace("-----BEGIN CERTIFICATE-----", "")
                   .replace("-----END CERTIFICATE-----", "")
                   .replace("\n", "").replace("\r", "").strip())

    return private_key, cert_single, subject_name


def firmar_xml(xml_root: etree._Element,
               pfx_data: bytes,
               password: str) -> etree._Element:
    """
    Firma un arbol XML lxml en memoria.
    Modifica xml_root en el lugar y lo devuelve.
    """
    private_key, cert_single, subject_name = _cargar_certificado(pfx_data, password)

    ns = {"ext": NS_EXT, "ds": NS_DS}

    extension_content = xml_root.find(".//ext:ExtensionContent", namespaces=ns)
    if extension_content is None:
        ubl_ext = xml_root.find(f".//{{{NS_EXT}}}UBLExtensions")
        if ubl_ext is None:
            ubl_ext = etree.Element(f"{{{NS_EXT}}}UBLExtensions")
            xml_root.insert(0, ubl_ext)
        ubl_extension = ubl_ext.find(f"{{{NS_EXT}}}UBLExtension")
        if ubl_extension is None:
            ubl_extension = etree.SubElement(ubl_ext, f"{{{NS_EXT}}}UBLExtension")
        extension_content = etree.SubElement(ubl_extension, f"{{{NS_EXT}}}ExtensionContent")

    signature = etree.Element(f"{{{NS_DS}}}Signature", nsmap={"ds": NS_DS})
    signature.set("Id", "SignatureSP")

    signed_info = etree.SubElement(signature, f"{{{NS_DS}}}SignedInfo")
    cm = etree.SubElement(signed_info, f"{{{NS_DS}}}CanonicalizationMethod")
    cm.set("Algorithm", "http://www.w3.org/TR/2001/REC-xml-c14n-20010315")
    sm = etree.SubElement(signed_info, f"{{{NS_DS}}}SignatureMethod")
    sm.set("Algorithm", "http://www.w3.org/2000/09/xmldsig#rsa-sha1")

    reference = etree.SubElement(signed_info, f"{{{NS_DS}}}Reference")
    reference.set("URI", "")
    transforms = etree.SubElement(reference, f"{{{NS_DS}}}Transforms")
    transform = etree.SubElement(transforms, f"{{{NS_DS}}}Transform")
    transform.set("Algorithm", "http://www.w3.org/2000/09/xmldsig#enveloped-signature")
    digest_method = etree.SubElement(reference, f"{{{NS_DS}}}DigestMethod")
    digest_method.set("Algorithm", "http://www.w3.org/2000/09/xmldsig#sha1")
    digest_value = etree.SubElement(reference, f"{{{NS_DS}}}DigestValue")

    signature_value = etree.SubElement(signature, f"{{{NS_DS}}}SignatureValue")

    key_info = etree.SubElement(signature, f"{{{NS_DS}}}KeyInfo")
    x509_data = etree.SubElement(key_info, f"{{{NS_DS}}}X509Data")
    x509_subject = etree.SubElement(x509_data, f"{{{NS_DS}}}X509SubjectName")
    x509_subject.text = subject_name
    x509_cert = etree.SubElement(x509_data, f"{{{NS_DS}}}X509Certificate")
    x509_cert.text = cert_single

    try:
        canonical_xml = etree.tostring(xml_root, method="c14n", exclusive=False)
        digest = hashlib.sha1(canonical_xml).digest()
        digest_value.text = base64.b64encode(digest).decode("utf-8")

        extension_content.append(signature)

        signed_info_canonical = etree.tostring(signed_info, method="c14n", exclusive=False)
        sig_bytes = private_key.sign(signed_info_canonical, padding.PKCS1v15(), hashes.SHA1())
        signature_value.text = base64.b64encode(sig_bytes).decode("utf-8")
    except Exception as e:
        raise SignatureError(f"Error durante la firma digital: {e}") from e

    return xml_root


def firmar_xml_desde_b64(xml_root: etree._Element,
                          cert_b64: str,
                          cert_password: str) -> etree._Element:
    """Firma XML usando certificado PFX codificado en Base64."""
    try:
        pfx_data = base64.b64decode(cert_b64)
    except Exception as e:
        raise CertificateError(f"cert_b64 no es Base64 valido: {e}") from e
    return firmar_xml(xml_root, pfx_data, cert_password)


def firmar_desde_archivo(xml_root: etree._Element,
                          cert_path: str,
                          cert_password: str) -> etree._Element:
    """Carga PFX desde archivo y firma el XML."""
    if not os.path.exists(cert_path):
        raise CertificateError(f"Certificado no encontrado: {cert_path}")
    with open(cert_path, "rb") as f:
        pfx_data = f.read()
    return firmar_xml(xml_root, pfx_data, cert_password)


def xml_to_string(xml_root: etree._Element) -> str:
    """Serializa arbol XML a string con declaracion UTF-8.
    IMPORTANTE: NO usar pretty_print=True porque altera el XML
    despues de firmado y rompe el digest (Incorrect reference digest value).
    """
    result = etree.tostring(
        xml_root, xml_declaration=True, encoding="UTF-8"
    ).decode("UTF-8")
    return result.replace("<?xml version='1.0' encoding='UTF-8'?>",
                          '<?xml version="1.0" encoding="UTF-8"?>')


def calcular_hash(xml_str: str) -> str:
    """Calcula hash SHA-256 del XML firmado."""
    digest = hashlib.sha256(xml_str.encode("utf-8")).digest()
    return base64.b64encode(digest).decode("utf-8")
