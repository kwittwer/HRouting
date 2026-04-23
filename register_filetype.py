"""
Registriert/deregistriert die .hrp Dateiendung für HRouting unter Windows.

Nutzung (als Administrator):
    python register_filetype.py install   <Pfad-zur-EXE>
    python register_filetype.py uninstall
"""

import sys
import winreg
from pathlib import Path

EXTENSION = ".hrp"
PROG_ID = "HRouting.Project"
FILE_DESC = "HRouting Projekt"


def register(exe_path: str):
    exe = str(Path(exe_path).resolve())
    print(f"Registriere {EXTENSION} → {exe}")

    # 1. Extension → ProgID
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                          rf"Software\Classes\{EXTENSION}") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, PROG_ID)

    # 2. ProgID → Beschreibung + Öffne-Befehl
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                          rf"Software\Classes\{PROG_ID}") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, FILE_DESC)

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                          rf"Software\Classes\{PROG_ID}\shell\open\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{exe}" "%1"')

    # 3. Icon (EXE-Icon verwenden)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                          rf"Software\Classes\{PROG_ID}\DefaultIcon") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{exe}",0')

    # Explorer-Cache aktualisieren
    try:
        import ctypes
        SHCNE_ASSOCCHANGED = 0x08000000
        SHCNF_IDLIST = 0x0000
        ctypes.windll.shell32.SHChangeNotify(
            SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
    except Exception:
        pass

    print(f"✓ {EXTENSION} ist jetzt mit HRouting verknüpft.")
    print("  Doppelklick auf .hrp-Dateien öffnet HRouting.")


def unregister():
    print(f"Entferne {EXTENSION}-Registrierung …")
    for path in [
        rf"Software\Classes\{PROG_ID}\shell\open\command",
        rf"Software\Classes\{PROG_ID}\shell\open",
        rf"Software\Classes\{PROG_ID}\shell",
        rf"Software\Classes\{PROG_ID}\DefaultIcon",
        rf"Software\Classes\{PROG_ID}",
        rf"Software\Classes\{EXTENSION}",
    ]:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except FileNotFoundError:
            pass

    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
    except Exception:
        pass

    print(f"✓ {EXTENSION}-Verknüpfung entfernt.")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("install", "uninstall"):
        print("Nutzung:")
        print("  python register_filetype.py install <Pfad-zur-EXE>")
        print("  python register_filetype.py uninstall")
        sys.exit(1)

    if sys.argv[1] == "install":
        if len(sys.argv) < 3:
            print("FEHLER: Pfad zur EXE angeben!")
            sys.exit(1)
        register(sys.argv[2])
    else:
        unregister()
