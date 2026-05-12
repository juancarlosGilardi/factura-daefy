; ============================================================================
;  Daefy Facturación — instalador Windows (Inno Setup)
; ============================================================================
;  Genera: dist/DaefyFacturacion-Setup-1.0.0.exe
;
;  Wizard:
;    1. Pantalla de bienvenida.
;    2. Carpeta de instalación (default: C:\Program Files\Daefy-Facturacion).
;    3. Pantalla EXTRA: el cliente elige la carpeta de sus DBFs (file picker).
;       — esa ruta se guarda en config.json al final.
;    4. Tareas: shortcut en escritorio (sí/no), shortcut en menú inicio.
;    5. Instala todo, genera config.json con la ruta DBF del cliente.
;    6. Ofrece lanzar Daefy Facturación al terminar.
;
;  Al desinstalar borra solo binarios — preserva config.json, certs/, data/,
;  storage/, logs/, backups/ por si el cliente reinstala.
;
;  Uso (asume que ya corriste PyInstaller y existe dist/daefy-facturacion/):
;      "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" scripts\installer.iss
;
;  El script busca los archivos relativos a su propia carpeta (scripts/).
; ============================================================================

#define AppName        "Daefy Facturación"
#define AppShortName   "DaefyFacturacion"
#define AppVersion     "1.0.0"
#define AppPublisher   "Daefy S.A.C."
#define AppURL         "https://daefy.com"
#define AppExeName     "daefy-facturacion.exe"
#define AppId          "{{A91F2C5E-7B12-4E9A-9F1C-DAEFY-FACTURA-2026}"

; Estructura asumida (relativa a este .iss):
;   ../dist/daefy-facturacion/   ← salida de PyInstaller
;   ../certs/daefy.pfx           ← cert real del cliente
;   ../data/logo_demo.png        ← logo (si existe)
;   ../config.example.json       ← plantilla
;   icon.ico                     ← opcional (Inno Setup usa default si no)

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\Daefy-Facturacion
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
OutputDir=..\dist
OutputBaseFilename={#AppShortName}-Setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
LanguageDetectionMethod=none
ShowLanguageDialog=no
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
; Si existe icon.ico junto a este script, lo usamos como icono del instalador.
#if FileExists(AddBackslash(SourcePath) + "icon.ico")
SetupIconFile=icon.ico
#endif
; Imagen del wizard (opcional, deja default si no existe).
WizardSmallImageFile=
WizardImageFile=

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el &Escritorio"; GroupDescription: "Accesos directos:"; Flags: checkedonce

; ============================================================================
;  Archivos a copiar
; ============================================================================
[Files]
; Carpeta completa generada por PyInstaller (one-folder).
Source: "..\dist\daefy-facturacion\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Certificado del cliente (precargado en el repo). Si querés que cada cliente
; pegue el suyo, comenta esta línea y agrega un paso al wizard que pida el .pfx.
Source: "..\certs\daefy.pfx"; DestDir: "{app}\certs"; Flags: ignoreversion uninsneveruninstall onlyifdoesntexist

; Logo demo (opcional)
Source: "..\data\logo_demo.png"; DestDir: "{app}\data"; Flags: ignoreversion uninsneveruninstall onlyifdoesntexist; Check: FileExists(ExpandConstant('{src}\..\data\logo_demo.png'))

; Plantilla de config.json — solo se copia si NO existe (no sobreescribe el
; del cliente en una reinstalación).
Source: "..\config.example.json"; DestDir: "{app}"; DestName: "config.example.json"; Flags: ignoreversion

; ============================================================================
;  Iconos (Menú Inicio + Escritorio + Quick Launch)
; ============================================================================
[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Comment: "Iniciar {#AppName} (abre el navegador en http://127.0.0.1:9876)"
Name: "{autoprograms}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Comment: "Iniciar {#AppName}"; Tasks: desktopicon

; ============================================================================
;  Ejecutar al finalizar (opcional, el usuario decide)
; ============================================================================
[Run]
Filename: "{app}\{#AppExeName}"; Description: "Iniciar {#AppName} ahora"; Flags: nowait postinstall skipifsilent unchecked

; ============================================================================
;  Limpieza al desinstalar — preserva config y datos del cliente.
; ============================================================================
[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\app"
Type: files; Name: "{app}\{#AppExeName}"
Type: files; Name: "{app}\config.example.json"
; NO borramos: config.json, certs\, data\, storage\, logs\, backups\

; ============================================================================
;  Code: pantalla extra para elegir la carpeta de DBFs + escritura de config.json
; ============================================================================
[Code]

var
  DbfPage: TInputDirWizardPage;

procedure InitializeWizard();
begin
  // Pantalla custom: pide la carpeta donde están los DBFs del cliente.
  // Aparece después de la selección de carpeta de instalación.
  DbfPage := CreateInputDirPage(
    wpSelectDir,
    'Carpeta de datos GECOPE (DBFs)',
    'Selecciona la carpeta donde están los archivos .dbf del sistema GECOPE',
    'Daefy Facturación necesita acceso a los archivos .dbf de GECOPE para leer ' +
      'clientes, productos y registrar las ventas. Esta carpeta debe contener al ' +
      'menos: cliente.dbf, ventas.dbf, ventas_detalle.dbf y articulo.dbf.' + #13#10 + #13#10 +
      'Si no estás seguro, pregunta a tu administrador. Podrás cambiar esta ruta ' +
      'después editando config.json.',
    False,
    ''
  );
  DbfPage.Add('Ruta a la carpeta de DBFs:');
  // Default razonable: ruta típica de GECOPE.
  DbfPage.Values[0] := 'C:\GECOPE\DATA';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  DbfDir: string;
  Required: array[0..3] of string;
  i: Integer;
  Missing: string;
begin
  Result := True;
  if (CurPageID = DbfPage.ID) then
  begin
    DbfDir := DbfPage.Values[0];
    if (Trim(DbfDir) = '') then
    begin
      MsgBox('Debes seleccionar una carpeta para los DBFs.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    if not DirExists(DbfDir) then
    begin
      if MsgBox(
           'La carpeta "' + DbfDir + '" no existe.' + #13#10 +
           '¿Quieres continuar de todos modos? (puedes corregirlo después en config.json)',
           mbConfirmation, MB_YESNO) = IDNO then
      begin
        Result := False;
        Exit;
      end;
      Exit;
    end;

    // Verifica que existan los DBFs claves; si faltan, advierte sin bloquear.
    Required[0] := 'cliente.dbf';
    Required[1] := 'ventas.dbf';
    Required[2] := 'ventas_detalle.dbf';
    Required[3] := 'articulo.dbf';
    Missing := '';
    for i := 0 to 3 do
    begin
      if not FileExists(AddBackslash(DbfDir) + Required[i]) then
      begin
        if Missing <> '' then Missing := Missing + ', ';
        Missing := Missing + Required[i];
      end;
    end;
    if Missing <> '' then
    begin
      if MsgBox(
           'En "' + DbfDir + '" faltan estos archivos: ' + Missing + #13#10 + #13#10 +
           '¿Quieres continuar igual? (puedes corregirlo después en config.json)',
           mbConfirmation, MB_YESNO) = IDNO then
      begin
        Result := False;
      end;
    end;
  end;
end;

// Escapa una ruta Windows para serializar a JSON (\ -> \\).
// Nota: usamos Chr(N) en vez de literales #N para que el preprocesador
// de Inno Setup no los confunda con directivas (#define, #if, ...).
function JsonEscape(const S: string): string;
var
  I, Code: Integer;
  Ch: Char;
  Buf: string;
begin
  Buf := '';
  for I := 1 to Length(S) do
  begin
    Ch := S[I];
    Code := Ord(Ch);
    if Ch = '\' then
      Buf := Buf + '\\'
    else if Ch = '"' then
      Buf := Buf + '\"'
    else if Code = 8 then
      Buf := Buf + '\b'
    else if Code = 9 then
      Buf := Buf + '\t'
    else if Code = 10 then
      Buf := Buf + '\n'
    else if Code = 12 then
      Buf := Buf + '\f'
    else if Code = 13 then
      Buf := Buf + '\r'
    else
      Buf := Buf + Ch;
  end;
  Result := Buf;
end;

procedure WriteConfigJson(const InstallDir, DbfDir: string);
var
  ConfigPath: string;
  Lines: TArrayOfString;
begin
  ConfigPath := AddBackslash(InstallDir) + 'config.json';

  // Si ya existe (reinstalación), no lo sobreescribimos para preservar
  // los ajustes del cliente (cert pass, sunat env, etc).
  if FileExists(ConfigPath) then
    Exit;

  SetArrayLength(Lines, 39);
  Lines[0]  := '{';
  Lines[1]  := '  "_comentario": "Configuración generada por el instalador Daefy Facturación. SUNAT en BETA por default — NO cambies a producción sin confirmar con tu contador.",';
  Lines[2]  := '';
  Lines[3]  := '  "dbf": {';
  Lines[4]  := '    "_comentario": "Carpeta donde GECOPE guarda sus archivos .dbf — definida durante la instalación.",';
  Lines[5]  := '    "path": "' + JsonEscape(DbfDir) + '"';
  Lines[6]  := '  },';
  Lines[7]  := '';
  Lines[8]  := '  "mdb": {';
  Lines[9]  := '    "mode": "dbf",';
  Lines[10] := '    "path": ""';
  Lines[11] := '  },';
  Lines[12] := '';
  Lines[13] := '  "empresa": {';
  Lines[14] := '    "ruc": "20615413071",';
  Lines[15] := '    "razon_social": "DAEFY S.A.C.",';
  Lines[16] := '    "nombre_comercial": "DAEFY",';
  Lines[17] := '    "direccion": "",';
  Lines[18] := '    "ubigeo": "150122",';
  Lines[19] := '    "departamento": "LIMA",';
  Lines[20] := '    "provincia": "LIMA",';
  Lines[21] := '    "distrito": "MIRAFLORES",';
  Lines[22] := '    "telefono": null,';
  Lines[23] := '    "email": null,';
  Lines[24] := '    "logo_path": "data/logo_demo.png"';
  Lines[25] := '  },';
  Lines[26] := '';
  Lines[27] := '  "sunat": {';
  Lines[28] := '    "_comentario": "BETA por default. Producción: cambiar a \"produccion\" SOLO tras confirmar con SUNAT y el contador.",';
  Lines[29] := '    "ambiente": "beta",';
  Lines[30] := '    "sol_user": "DAEFY123",';
  Lines[31] := '    "sol_pass": "Udenthol123"';
  Lines[32] := '  },';
  Lines[33] := '';
  Lines[34] := '  "certificado": {';
  Lines[35] := '    "path": "certs/daefy.pfx",';
  Lines[36] := '    "password": "Udenthol123"';
  Lines[37] := '  }';
  Lines[38] := '}';

  if not SaveStringsToUTF8File(ConfigPath, Lines, False) then
    MsgBox(
      'No se pudo escribir config.json en ' + ConfigPath + '.' + #13#10 +
      'Tendrás que crearlo manualmente copiando config.example.json.',
      mbError, MB_OK
    );
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  DbfDir: string;
begin
  if CurStep = ssPostInstall then
  begin
    DbfDir := DbfPage.Values[0];
    WriteConfigJson(ExpandConstant('{app}'), DbfDir);
  end;
end;
