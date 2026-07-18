"""
Test mínimo y aislado de apiInit() en api64.dll.

Sin clases, sin wrapper, sin nada intermedio — solo para confirmar
si el crash viene de la DLL en sí o de algo en nuestro código.

Uso:
    python test_raw_init.py
"""

import os
import ctypes

DLL_PATH = r"C:\EDIABAS\Bin\api64.dll"
BIN_PATH = r"C:\EDIABAS\Bin"

print(f"Cargando: {DLL_PATH}")
os.environ["PATH"] = BIN_PATH + os.pathsep + os.environ["PATH"]

dll = ctypes.CDLL(DLL_PATH)   # CDLL en vez de WinDLL — prueba de convención cdecl
print("[+] DLL cargada correctamente (CDLL).")

# Probamos SIN declarar restype/argtypes primero — a veces ctypes
# infiere mal el tipo de retorno por defecto (asume int en vez de
# lo que realmente espera la función) y eso puede causar corrupción.
fn = getattr(dll, "__apiInit")
print(f"[+] Símbolo __apiInit encontrado: {fn}")

print("\n[*] Llamando a __apiInit() ... (aquí puede crashear)")
result = fn()
print(f"[+] apiInit() devolvió: {result}")