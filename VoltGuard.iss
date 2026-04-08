[Setup]
; Información básica de la aplicación
AppName=VoltGuard
AppVersion=1.0.0
AppPublisher=Yev / VoltGuard
AppSupportURL=https://github.com/tu-usuario/voltguard

; Directorios y nombres de salida
DefaultDirName={autopf}\VoltGuard
DefaultGroupName=VoltGuard
OutputDir=Release
OutputBaseFilename=VoltGuard_Installer
SetupIconFile=logo.ico

; Apariencia y permisos
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
; PrivilegesRequired=admin ; Descomenta si necesitas instalar para todos los usuarios en Program Files

[Tasks]
Name: "startup"; Description: "Start VoltGuard automatically at PC boot"; GroupDescription: "Startup:"
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional options:"

[Files]
; ---- SI ESTÁ COMPILADO COMO --onefile (un solo .exe) ----
Source: "dist\VoltGuard.exe"; DestDir: "{app}"; Flags: ignoreversion

; ---- SI ESTÁ COMPILADO COMO --onedir (una carpeta) MODO RECOMENDADO ----
; Comenta la línea de arriba y usa estas de abajo si no usas --onefile:
; Source: "dist\VoltGuard\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Accesos directos: menú de inicio y escritorio
Name: "{group}\VoltGuard"; Filename: "{app}\VoltGuard.exe"; IconFilename: "{app}\VoltGuard.exe"
Name: "{autodesktop}\VoltGuard"; Filename: "{app}\VoltGuard.exe"; IconFilename: "{app}\VoltGuard.exe"; Tasks: desktopicon

[Registry]
; Autoarranque - Este es el registry key mágico para arrancar con Windows
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "VoltGuard"; ValueData: """{app}\VoltGuard.exe"""; Tasks: startup

[Run]
; Lanzar la aplicación automáticamente tras finalizar la instalación
Filename: "{app}\VoltGuard.exe"; Description: "Start VoltGuard"; Flags: nowait postinstall skipifsilent
