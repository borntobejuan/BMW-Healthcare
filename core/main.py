"""
BMW E87 120i — CAN Bus Diagnostic Tool
=======================================
Punto de entrada principal. Ejecuta uno de los modos:

  python main.py sniff          → Escucha tráfico CAN raw (primer paso)
  python main.py dtc  [módulo]  → Lee DTCs de un módulo
  python main.py info [módulo]  → Lee info ECU de un módulo
  python main.py vin            → Lee VIN del DME

Módulos disponibles: DME, EGS, DSC, ABS, KOMBI, CAS, FRM, EPS

Antes de usar:
  1. Conecta el cable K+DCAN al OBD del coche y al PC
  2. Pon el contacto (posición II, sin arrancar)
  3. Ajusta SERIAL_PORT en core/config.py (ej. "COM3")
  4. pip install -r requirements.txt
"""

import sys
import argparse
from config import SERIAL_PORT, MODULE_IDS
from connection import CANConnection, detect_kdcan_port
from sniffer import sniff
from uds_client import BMWModuleClient


def cmd_sniff(args):
    port = args.port or detect_kdcan_port() or SERIAL_PORT
    sniff(port=port, duration=args.duration, only_known=args.known)


def cmd_dtc(args):
    module = args.module.upper()
    port   = args.port or detect_kdcan_port() or SERIAL_PORT

    print(f"\n[*] Leyendo DTCs del módulo: {module}")
    with CANConnection(port=port) as bus:
        client = BMWModuleClient(bus, module=module)
        with client.session():
            dtcs = client.read_dtcs()

    if not dtcs:
        print(f"[✓] Sin fallos activos en {module}")
    else:
        print(f"\n  {'DTC':<10} {'Estado'}")
        print(f"  {'─'*40}")
        for d in dtcs:
            print(f"  {d['dtc']:<10} {d['status']}")
    print()


def cmd_info(args):
    module = args.module.upper()
    port   = args.port or detect_kdcan_port() or SERIAL_PORT

    print(f"\n[*] Leyendo info del módulo: {module}")
    with CANConnection(port=port) as bus:
        client = BMWModuleClient(bus, module=module)
        with client.session():
            info = client.read_ecu_info()

    print(f"\n  Módulo: {module}")
    print(f"  {'─'*35}")
    for k, v in info.items():
        print(f"  {k:<20}: {v}")
    print()


def cmd_vin(args):
    port = args.port or detect_kdcan_port() or SERIAL_PORT

    print("\n[*] Leyendo VIN del DME ...")
    with CANConnection(port=port) as bus:
        client = BMWModuleClient(bus, module="DME")
        with client.session():
            vin = client.read_vin()
    print(f"\n  VIN: {vin}\n")


# ── CLI ──────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="BMW E87 120i — CAN Diagnostic Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Módulos disponibles: {', '.join(MODULE_IDS.keys())}",
    )
    parser.add_argument("--port", default=None, help="Puerto COM (ej. COM3, /dev/ttyUSB0)")

    sub = parser.add_subparsers(dest="cmd", required=True)

    # sniff
    p_sniff = sub.add_parser("sniff", help="Escucha tráfico CAN raw")
    p_sniff.add_argument("--duration", default=15.0, type=float, help="Segundos (default 15)")
    p_sniff.add_argument("--known", action="store_true", help="Solo IDs conocidos del E87")
    p_sniff.set_defaults(func=cmd_sniff)

    # dtc
    p_dtc = sub.add_parser("dtc", help="Lee DTCs de un módulo")
    p_dtc.add_argument("module", nargs="?", default="DME", help="Módulo (default DME)")
    p_dtc.set_defaults(func=cmd_dtc)

    # info
    p_info = sub.add_parser("info", help="Lee info de la ECU")
    p_info.add_argument("module", nargs="?", default="DME", help="Módulo (default DME)")
    p_info.set_defaults(func=cmd_info)

    # vin
    p_vin = sub.add_parser("vin", help="Lee el VIN del DME")
    p_vin.set_defaults(func=cmd_vin)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
