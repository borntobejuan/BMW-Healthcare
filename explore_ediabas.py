"""
Herramienta de introspección EDIABAS.

EDIABAS no permite "listar todos los campos" directamente — hay que
ejecutar un job y luego usar apiJobInfo() para ver qué devolvió
realmente. Este script te deja explorar de forma interactiva antes
de escribir código que dependa de nombres de campo concretos.

Uso:
    python explore_ediabas.py D_MOTOR STATUS_MOTOR
    python explore_ediabas.py D_MOTOR FS_LESEN
    python explore_ediabas.py D_MOTOR IDENTIFIKATION
"""

import sys
from core.ediabas_bridge_client import EdiabasBridgeClient


def explore(ecu: str, job: str, params: str = ""):
    print(f"\n{'─'*60}")
    print(f"  EDIABAS Explorer  |  ECU={ecu}  JOB={job}")
    print(f"{'─'*60}\n")

    client = EdiabasBridgeClient()

    try:
        result = client.run_job(ecu, job, params)

        print(f"[+] Job completado. {len(result.sets)} set(s) de resultados.\n")

        if not result.sets:
            print("[!] Sin resultados. Puede que el job no aplique a esta ECU,")
            print("    o que el motor no esté encendido (jobs de estado en vivo")
            print("    a veces requieren contacto + motor arrancado).")
        else:
            for i, row in enumerate(result.sets, start=1):
                print(f"  ── Set {i} ({len(row)} campos) ──")
                for field, value in row.items():
                    print(f"    {field:<25} = {value}")
                print()

    except Exception as e:
        print(f"[!] Error: {e}")

    print(f"{'─'*60}\n")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso: python explore_ediabas.py <ECU> <JOB> [params]")
        print("Ejemplo: python explore_ediabas.py D_MOTOR IDENTIFIKATION")
        sys.exit(1)

    ecu    = sys.argv[1]
    job    = sys.argv[2]
    params = sys.argv[3] if len(sys.argv) > 3 else ""
    explore(ecu, job, params)