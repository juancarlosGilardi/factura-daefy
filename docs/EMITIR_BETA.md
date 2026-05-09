# Probar emisión SUNAT BETA

Guía para probar la emisión de comprobantes a SUNAT en ambiente BETA con AFISCA.

> **Sprint 3 pendiente.** El flujo end-to-end (firmar + enviar + persistir CDR) en modo MDB **aún no está implementado**. Esta guía describe cómo dejarlo listo para cuando se implemente, y los comandos para probarlo manualmente cuando se cierre el sprint.

## Pre-requisitos

1. Setup completo según [`ARRANCAR.md`](ARRANCAR.md) — `verificar_setup.py` debe dar OK en todo.
2. Cert AFISCA en `certs/20518470591.pfx` (ya copiado del original `CT2505125697.pfx`).
3. Password en `config.json -> certificado.password = "AfiscA205184"`.
4. SUNAT credentials BETA: `MODDATOS / MODDATOS` (en `config.json -> sunat.sol_user/sol_pass`).
5. `config.json -> sunat.ambiente = "beta"` (default seguro).

## Verificación previa

```bash
python scripts/verificar_setup.py
```

Debe mostrar:
- `[ OK ]  Password del .pfx correcta (cert se abre)`
- `[ OK ]  Subject del cert contiene el RUC declarado (20518470591)`
- `[ OK ]  SUNAT en BETA (default seguro).`

## Conexión BETA

Aunque la emisión no está implementada todavía, puedes verificar la conectividad SOAP con SUNAT BETA:

```python
from app.services.sunat_test_service import probar_conexion
print(probar_conexion(
    sol_user="MODDATOS",
    sol_pass="MODDATOS",
    ambiente="beta",
))
```

Debe imprimir un dict con `ok=True` y un mensaje del servicio.

## Smoke test (cuando Sprint 3 esté listo)

### 1. Backup del .mdb

```bash
cp /ruta/al/db_afisca.mdb /ruta/al/db_afisca.BACKUP_$(date +%Y%m%d).mdb
```

O trabaja contra una copia en `data/`:

```bash
cp /ruta/al/db_afisca.mdb data/test_emit.mdb
# Edita config.json -> mdb.path = "data/test_emit.mdb"
```

### 2. Crear comprobante de prueba

Desde la UI (`/comprobantes/emitir`) o por API:

```bash
curl -X POST http://127.0.0.1:9876/api/comprobantes/emitir \
  -H "Content-Type: application/json" \
  -d '{
    "tipo_documento": "01",
    "serie": "F999",
    "fecha_emision": "2026-05-09",
    "moneda": "PEN",
    "tipo_cambio": 1.0,
    "cliente_tipo_doc": "6",
    "cliente_numero_doc": "20100070970",
    "cliente_razon_social": "TEST CLIENTE SAC",
    "cliente_direccion": "AV. TEST 123",
    "items": [
      {
        "orden": 1,
        "codigo": "TST-001",
        "descripcion": "Servicio de prueba",
        "cantidad": 1,
        "valor_unitario": 100.00,
        "tipo_afectacion_igv": "10",
        "unidad_medida": "ZZ"
      }
    ]
  }'
```

Respuesta esperada (201):

```json
{
  "id": <synth_id>,
  "tipo_documento": "01",
  "serie": "F999",
  "correlativo": <MAX+1>,
  "numero_completo": "F999-00000001",
  "estado": "P",
  "total_venta": 118.0,
  "...": "..."
}
```

Verificar en el `.mdb`:

```python
import pyodbc
cn = pyodbc.connect('DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=data/test_emit.mdb;')
cur = cn.cursor()
cur.execute("SELECT F4SERDOC, F4NUMDOC, F4ESTNUL, F4ENVIADO, F4CDR FROM TBVENTA_CAB WHERE F4SERDOC='F999'")
for r in cur: print(r)
# Espera: ('F999', '0000001', False, False, None)
```

### 3. Enviar a SUNAT BETA

```bash
curl -X POST http://127.0.0.1:9876/api/comprobantes/<id>/enviar-sunat
```

Respuesta esperada (200):

```json
{
  "ok": true,
  "cdr_codigo": "0",
  "cdr_descripcion": "La Factura numero F999-00000001, ha sido aceptada",
  "xml_path": "storage/xml/20518470591-01-F999-00000001.xml",
  "cdr_path": "storage/cdr/R-20518470591-01-F999-00000001.xml",
  "pdf_path": "storage/pdf/F999-00000001.pdf"
}
```

Verificar en el `.mdb`:

```python
cur.execute("SELECT F4ENVIADO, F4ESTEMI, F4CDR, F4CDRFECHA, F4CODEHASH FROM TBVENTA_CAB WHERE F4SERDOC='F999'")
for r in cur: print(r)
# Espera: (True, True, '0', <datetime>, '<hash 28+ chars>')
```

Verificar archivos generados:

```bash
ls -la storage/xml/20518470591-01-F999-00000001.xml
ls -la storage/cdr/R-20518470591-01-F999-00000001.xml
ls -la storage/pdf/F999-00000001.pdf
```

El XML firmado debe tener una sección `<ds:Signature>...</ds:Signature>` con el cert AFISCA. El CDR debe contener `<cbc:ResponseCode>0</cbc:ResponseCode>`.

### 4. Descargar artefactos

```bash
curl http://127.0.0.1:9876/api/comprobantes/<id>/xml -o factura.xml
curl http://127.0.0.1:9876/api/comprobantes/<id>/cdr -o cdr.xml
curl http://127.0.0.1:9876/api/comprobantes/<id>/pdf -o factura.pdf
```

## Códigos CDR comunes (SUNAT)

| Código | Significado | Acción |
|---|---|---|
| `0` | Aceptado | OK. Persistir y mostrar al usuario. |
| `2335` | RUC del emisor no es contribuyente activo | Verificar SUNAT. |
| `2400` | Documento ya enviado anteriormente | Marcar como enviado y no reintentar. |
| `2800-2999` | Errores de validación de datos | Revisar payload. |
| `3000+` | Errores de firma o transporte | Revisar cert + conexión. |

## NUNCA producción sin aprobación

> **Crítico.** Los comprobantes en producción son legales — una vez emitidos no se pueden eliminar, solo anular con comunicación de baja.
>
> **NO** cambies `sunat.ambiente` a `"produccion"` hasta que:
>
> 1. AFISCA (o el cliente correspondiente) lo apruebe expresamente por escrito.
> 2. Hayas hecho >= 5 emisiones exitosas en BETA con todos los casos de uso del cliente (factura, boleta, NC, ND, con/sin detracción, PEN/USD).
> 3. Hayas verificado que `F4USEGRA="FMDB"` correctamente para no contaminar series del SIAP.

## Troubleshooting

| Síntoma | Causa | Fix |
|---|---|---|
| `cdr_codigo: "0156"` "Documento ya existe en el sistema" | El correlativo ya se usó (probable race con SIAP). | Cambiar de serie (ej F999 → F998), reintentar. |
| `cdr_codigo: "1078"` "El RUC del emisor no esta autorizado" | RUC en XML no coincide con cert. | Verificar `empresa.ruc` y subject del cert. |
| Error 502 "SUNAT respondió: timeout" | SUNAT BETA caído o lento. | Reintentar en 1-2 minutos. |
| `firma_digital` falla con "could not deserialize key data" | Password del `.pfx` incorrecta. | Re-verificar `certificado.password`. |
| PDF se genera vacío | weasyprint sin GTK runtime. | `pip install weasyprint[full]` o instalar GTK3. |
