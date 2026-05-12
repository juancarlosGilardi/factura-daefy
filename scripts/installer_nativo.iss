; ============================================================================
;  Daefy Facturación (Nativo) — instalador Windows (Inno Setup)
; ============================================================================
;  Variante con UI nativa pywebview (sin abrir navegador del sistema).
;  Genera: dist/DaefyFacturacion-Setup-Nativo-1.0.0.exe
;
;  COEXISTE con `installer.iss` (versión browser-based) — usa AppId distinto,
;  carpeta de instalación distinta y puerto 9877 (vs 9876).
;
;  Wizard:
;    1. Pantalla de bienvenida.
;    2. Carpeta de instalación (default: C:\Program Files\Daefy-Facturacion-Nativo).
;    3. Pantalla EXTRA: el cliente elige la carpeta de sus DBFs (file picker).
;       — esa ruta se guarda en config.json al final.
;    4. Tareas: shortcut en escritorio (sí/no), shortcut en menú inicio.
;    5. Instala todo, genera config.json con la ruta DBF + port=9877.
;    6. Ofrece lanzar Daefy Facturación (Nativo) al terminar.
;
;  Al desinstalar borra solo binarios — preserva config.json, certs/, data/,
;  storage/, logs/, backups/ por si el cliente reinstala.
;
;  Uso (asume que ya corriste PyInstaller y existe dist/daefy-facturacion-nativo/):
;      "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" scripts\installer_nativo.iss
; ============================================================================

#define AppName        "Daefy Facturación (Nativo)"
#define AppShortName   "DaefyFacturacion-Nativo"
#define AppVersion     "1.0.0"
#define AppPublisher   "Daefy S.A.C."
#define AppURL         "https://daefy.com"
#define AppExeName     "daefy-facturacion-nativo.exe"
; AppId DIFERENTE del installer.iss (browser-based) para que Inno Setup permita
; ambas instalaciones lado a lado en la misma máquina.
#define AppId          "{{B1234567-AAAA-BBBB-CCCC-NATIVOA5F2C9D}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\Daefy-Facturacion-Nativo
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
OutputDir=..\dist
; Nombre del .exe instalador conforme a lo solicitado: DaefyFacturacion-Setup-Nativo-1.0.0.exe
OutputBaseFilename=DaefyFacturacion-Setup-Nativo-{#AppVersion}
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
#if FileExists(AddBackslash(SourcePath) + "icon.ico")
SetupIconFile=icon.ico
#endif
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
; Carpeta completa generada por PyInstaller (one-folder, build NATIVO).
Source: "..\dist\daefy-facturacion-nativo\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Certificado del cliente (precargado en el repo).
Source: "..\certs\daefy.pfx"; DestDir: "{app}\certs"; Flags: ignoreversion uninsneveruninstall onlyifdoesntexist

; Logo demo (opcional)
Source: "..\data\logo_demo.png"; DestDir: "{app}\data"; Flags: ignoreversion uninsneveruninstall onlyifdoesntexist; Check: FileExists(ExpandConstant('{src}\..\data\logo_demo.png'))

; Plantilla de config.json — solo se copia si NO existe.
Source: "..\config.example.json"; DestDir: "{app}"; DestName: "config.example.json"; Flags: ignoreversion

; ============================================================================
;  Iconos (Menú Inicio + Escritorio)
; ============================================================================
[Icons]
Name: "{autoprograms}\Daefy Facturación (Nativo)"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Comment: "Iniciar Daefy Facturación (UI nativa, sin navegador)"
Name: "{autoprograms}\Desinstalar Daefy Facturación (Nativo)"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Daefy Facturación (Nativo)"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Comment: "Iniciar Daefy Facturación (UI nativa)"; Tasks: desktopicon

; ============================================================================
;  Ejecutar al finalizar (opcional, el usuario decide)
; ============================================================================
[Run]
Filename: "{app}\{#AppExeName}"; Description: "Iniciar Daefy Facturación (Nativo) ahora"; Flags: nowait postinstall skipifsilent unchecked

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

// Devuelve el valor de /DBFPATH=... pasado por linea de comandos al
// instalador (modo silencioso o GUI). Vacio si no se paso.
function GetDbfPathParam(): string;
begin
  Result := ExpandConstant('{param:dbfpath|}');
end;

procedure InitializeWizard();
var
  CmdDbfPath: string;
begin
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
  // Si se paso /DBFPATH=... en la linea de comandos, ese valor manda y
  // pre-rellena el wizard (en modo silencioso ni siquiera se ve).
  CmdDbfPath := GetDbfPathParam();
  if CmdDbfPath <> '' then
    DbfPage.Values[0] := CmdDbfPath
  else
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

  if FileExists(ConfigPath) then
    Exit;

  // Igual que installer.iss pero con `"port": 9877` para diferenciarse.
  SetArrayLength(Lines, 41);
  Lines[0]  := '{';
  Lines[1]  := '  "_comentario": "Configuración generada por el instalador Daefy Facturación (Nativo). SUNAT en BETA por default — NO cambies a producción sin confirmar con tu contador.",';
  Lines[2]  := '';
  Lines[3]  := '  "port": 9877,';
  Lines[4]  := '';
  Lines[5]  := '  "dbf": {';
  Lines[6]  := '    "_comentario": "Carpeta donde GECOPE guarda sus archivos .dbf — definida durante la instalación.",';
  Lines[7]  := '    "path": "' + JsonEscape(DbfDir) + '"';
  Lines[8]  := '  },';
  Lines[9]  := '';
  Lines[10] := '  "mdb": {';
  Lines[11] := '    "mode": "dbf",';
  Lines[12] := '    "path": ""';
  Lines[13] := '  },';
  Lines[14] := '';
  Lines[15] := '  "empresa": {';
  Lines[16] := '    "ruc": "20615413071",';
  Lines[17] := '    "razon_social": "DAEFY S.A.C.",';
  Lines[18] := '    "nombre_comercial": "DAEFY",';
  Lines[19] := '    "direccion": "",';
  Lines[20] := '    "ubigeo": "150122",';
  Lines[21] := '    "departamento": "LIMA",';
  Lines[22] := '    "provincia": "LIMA",';
  Lines[23] := '    "distrito": "MIRAFLORES",';
  Lines[24] := '    "telefono": null,';
  Lines[25] := '    "email": null,';
  Lines[26] := '    "logo_path": "data/logo_demo.png"';
  Lines[27] := '  },';
  Lines[28] := '';
  Lines[29] := '  "sunat": {';
  Lines[30] := '    "_comentario": "BETA por default. Producción: cambiar a \"produccion\" SOLO tras confirmar con SUNAT y el contador.",';
  Lines[31] := '    "ambiente": "beta",';
  Lines[32] := '    "sol_user": "DAEFY123",';
  Lines[33] := '    "sol_pass": "Udenthol123"';
  Lines[34] := '  },';
  Lines[35] := '';
  Lines[36] := '  "certificado": {';
  Lines[37] := '    "path": "certs/daefy.pfx",';
  Lines[38] := '    "password": "Udenthol123"';
  Lines[39] := '  }';
  Lines[40] := '}';

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
  CmdDbfPath: string;
begin
  if CurStep = ssPostInstall then
  begin
    // /DBFPATH=... siempre gana sobre el valor del wizard (es la unica
    // forma confiable de configurar instalaciones silenciosas).
    CmdDbfPath := GetDbfPathParam();
    if CmdDbfPath <> '' then
      DbfDir := CmdDbfPath
    else
      DbfDir := DbfPage.Values[0];
    WriteConfigJson(ExpandConstant('{app}'), DbfDir);
  end;
end;
