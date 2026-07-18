"""
BMW E87 120i — EDIABAS Diagnostic Tool
========================================
Punto de entrada principal. Ejecuta uno de los modos:

  python main.py dtc  [módulo]   → Lee DTCs de un módulo
  python main.py info [módulo]   → Lee info/identificación de un módulo
  python main.py live            → Polling continuo → InfluxDB → Grafana

Módulos disponibles: ver core/ediabas_config.py → ECU_MODULES
(actualmente: DME confirmado como d71n47a0 / N46-MEV9; el resto son
wrappers .grp pendientes de identificar con el coche conectado)

Antes de usar:
  1. Compila el puente: cd ediabas_bridge && dotnet publish -c Release -r win-x86 --self-contained true
  2. Conecta el cable K+DCAN al OBD del coche y al PC
  3. Pon el contacto (posición II, sin arrancar)
  4. pip install -r requirements.txt
  5. cd infra && docker compose up -d   (para live)
"""

import argparse
from dotenv import load_dotenv
load_dotenv()

from core.ediabas_config import ECU_MODULES, JOB_IDENT, JOB_READ_DTC, DTC_RESULT_FIELDS
from core.ediabas_bridge_client import EdiabasBridgeClient, EdiabasBridgeError


def _resolve_ecu(module: str) -> str:
    """Traduce un alias de módulo (DME, EGS...) al nombre real de fichero .PRG/.GRP."""
    return ECU_MODULES.get(module.upper(), module)


def cmd_dtc(args):
    ecu = _resolve_ecu(args.module)
    print(f"\n[*] Leyendo DTCs de: {args.module} ({ecu})")

    client = EdiabasBridgeClient()
    try:
        result = client.run_job(ecu, JOB_READ_DTC)
    except EdiabasBridgeError as e:
        print(f"[!] Error: {e}")
        return

    if not result.sets:
        print(f"[✓] Sin fallos activos en {args.module}, o campos aún no confirmados.")
        print(f"    Campos esperados: {DTC_RESULT_FIELDS}")
        return

    print(f"\n  {'#':<4}{'Campos'}")
    print(f"  {'─'*50}")
    for i, row in enumerate(result.sets, start=1):
        print(f"  {i:<4}{row}")
    print()


def cmd_info(args):
    ecu = _resolve_ecu(args.module)
    print(f"\n[*] Leyendo info de: {args.module} ({ecu})")

    client = EdiabasBridgeClient()
    try:
        result = client.run_job(ecu, args.job)
    except EdiabasBridgeError as e:
        print(f"[!] Error: {e}")
        return

    if not result.sets:
        print("[!] Sin resultados con los campos conocidos.")
        return

    for i, row in enumerate(result.sets, start=1):
        print(f"\n  ── Set {i} ──")
        for field, value in row.items():
            print(f"    {field:<25} = {value}")
    print()


def cmd_live(args):
    from telemetry.poller import EdiabasPoller
    poller = EdiabasPoller(interval=args.interval)
    poller.run(duration=args.duration)


def main():
    parser = argparse.ArgumentParser(
        description="BMW E87 120i — EDIABAS Diagnostic Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Módulos disponibles: {', '.join(ECU_MODULES.keys())}",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_dtc = sub.add_parser("dtc", help="Lee DTCs de un módulo")
    p_dtc.add_argument("module", nargs="?", default="DME", help="Módulo (default DME)")
    p_dtc.set_defaults(func=cmd_dtc)

    p_info = sub.add_parser("info", help="Lee info/identificación de un módulo")
    p_info.add_argument("module", nargs="?", default="DME", help="Módulo (default DME)")
    p_info.add_argument("--job", default=JOB_IDENT, help=f"Job a ejecutar (default {JOB_IDENT})")
    p_info.set_defaults(func=cmd_info)

    p_live = sub.add_parser("live", help="Polling continuo → InfluxDB → Grafana")
    p_live.add_argument("--interval", default=1.0, type=float, help="Segundos entre ciclos (default 1.0)")
    p_live.add_argument("--duration", default=0.0, type=float, help="Segundos (0=infinito, default 0)")
    p_live.set_defaults(func=cmd_live)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
