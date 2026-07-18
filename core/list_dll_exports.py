"""
Lista los símbolos (funciones) que exporta realmente api64.dll.

Esto es necesario porque algunas reimplementaciones de EDIABAS
(como EdiabasLib) pueden exportar funciones con nombres distintos
al api32.dll original, o requerir mayúsculas/guiones bajos distintos.

Uso:
    python list_dll_exports.py
"""

import sys
import ctypes
import subprocess
import re

from ediabas_config import EDIABAS_DLL_PATH


def list_exports_via_dumpbin(dll_path: str):
    """
    Intenta usar dumpbin (Visual Studio) si está disponible.
    Da la lista más fiable de símbolos exportados.
    """
    try:
        result = subprocess.run(
            ["dumpbin", "/exports", dll_path],
            capture_output=True, text=True, timeout=10,
        )
        print(result.stdout)
        return True
    except FileNotFoundError:
        return False


def list_exports_via_pefile(dll_path: str):
    """Fallback usando la librería pefile (pip install pefile)."""
    try:
        import pefile
    except ImportError:
        print("[!] pefile no instalado. Ejecuta: pip install pefile")
        return False

    pe = pefile.PE(dll_path)
    print(f"\n{'─'*60}")
    print(f"  Símbolos exportados por {dll_path}")
    print(f"{'─'*60}\n")

    if not hasattr(pe, "DIRECTORY_ENTRY_EXPORT"):
        print("[!] Esta DLL no tiene tabla de exports (¿es .NET/COM?)")
        return True

    names = []
    for exp in pe.DIRECTORY_ENTRY_EXPORT.symbols:
        if exp.name:
            names.append(exp.name.decode("latin-1"))

    # Filtramos y mostramos los que parecen relacionados con la API
    api_like = [n for n in names if "api" in n.lower() or "Api" in n]
    others   = [n for n in names if n not in api_like]

    print(f"  Funciones con 'api' en el nombre ({len(api_like)}):")
    for n in sorted(api_like):
        print(f"    {n}")

    print(f"\n  Resto de exports ({len(others)}):")
    for n in sorted(others)[:30]:
        print(f"    {n}")
    if len(others) > 30:
        print(f"    ... y {len(others) - 30} más")

    return True


def try_common_prefixes(dll_path: str):
    """
    Prueba a cargar la DLL y buscar variantes comunes de nombre
    para apiInit (mayúsculas, guion bajo, decoración de C++).
    """
    dll = ctypes.WinDLL(dll_path)
    candidates = [
        "apiInit", "ApiInit", "API_INIT", "apiinit",
        "_apiInit", "_apiInit@0", "apiInit@0",
        "APIInit", "EdiabasInit",
    ]
    print(f"\n{'─'*60}")
    print("  Probando nombres candidatos para apiInit:")
    print(f"{'─'*60}\n")
    for name in candidates:
        try:
            fn = getattr(dll, name)
            print(f"  ✓ ENCONTRADO: {name}")
        except AttributeError:
            print(f"  ✗ no existe: {name}")


if __name__ == "__main__":
    print(f"\nDLL objetivo: {EDIABAS_DLL_PATH}\n")

    found = list_exports_via_dumpbin(EDIABAS_DLL_PATH)
    if not found:
        print("[*] dumpbin no disponible, probando con pefile...")
        found = list_exports_via_pefile(EDIABAS_DLL_PATH)

    if not found:
        print("[*] pefile tampoco disponible, probando nombres candidatos directamente...")

    try_common_prefixes(EDIABAS_DLL_PATH)
