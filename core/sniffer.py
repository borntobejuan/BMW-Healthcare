"""
Sniffer CAN — escucha tramas raw del bus E87.

Útil como PRIMER PASO: antes de enviar comandos UDS, 
verifica que el cable recibe tráfico del bus del coche.

Uso:
    python -m core.sniffer
    python -m core.sniffer --port COM9 --duration 10
"""

import time
import argparse
import can
from core.connection import CANConnection, detect_kdcan_port
from core.config import KNOWN_CAN_IDS, SERIAL_PORT


def sniff(port: str, duration: float = 15.0, only_known: bool = False):
    """
    Escucha el bus CAN durante `duration` segundos e imprime las tramas.

    Args:
        port:        Puerto COM del cable (ej. "COM3")
        duration:    Segundos de escucha
        only_known:  Si True, filtra y muestra solo IDs conocidos del E87
    """
    seen_ids: dict[int, int] = {}  # {can_id: count}

    print(f"\n{'─'*55}")
    print(f"  BMW E87 CAN Sniffer  |  {duration}s  |  puerto: {port}")
    print(f"{'─'*55}\n")

    with CANConnection(port=port) as bus:
        deadline = time.time() + duration
        while time.time() < deadline:
            msg = bus.recv(timeout=0.5)
            if msg is None:
                continue

            can_id   = msg.arbitration_id
            label    = KNOWN_CAN_IDS.get(can_id, "?")
            payload  = msg.data.hex(" ").upper()
            seen_ids[can_id] = seen_ids.get(can_id, 0) + 1

            if only_known and label == "?":
                continue

            # Marca IDs conocidos del E87
            marker = "★" if label != "?" else " "
            print(f"  {marker} [{can_id:03X}]  {payload:<23}  {label}")

    # ── Resumen ──────────────────────────────────────────────────────────────────
    print(f"\n{'─'*55}")
    print(f"  IDs únicos captados: {len(seen_ids)}")
    if seen_ids:
        top = sorted(seen_ids.items(), key=lambda x: -x[1])[:5]
        print("  Top 5 por frecuencia:")
        for cid, cnt in top:
            name = KNOWN_CAN_IDS.get(cid, "desconocido")
            print(f"    [{cid:03X}]  {cnt:>5} tramas  —  {name}")
    print(f"{'─'*55}\n")


# ── CLI ──────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BMW E87 CAN sniffer")
    parser.add_argument("--port",     default=None,  help="Puerto COM (ej. COM3)")
    parser.add_argument("--duration", default=15.0,  type=float, help="Segundos")
    parser.add_argument("--known",    action="store_true", help="Solo IDs conocidos")
    args = parser.parse_args()

    port = args.port or detect_kdcan_port() or SERIAL_PORT
    sniff(port=port, duration=args.duration, only_known=args.known)
