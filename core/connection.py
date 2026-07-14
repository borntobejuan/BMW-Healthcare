"""
Capa de conexión CAN para cable K+DCAN USB (chip FTDI)

El cable K+DCAN en modo D-CAN se comporta como interfaz serial que encapsula
tramas CAN. python-can lo maneja con el interface 'serial'.

Uso:
    from core.connection import CANConnection
    with CANConnection() as bus:
        # bus es un objeto python-can listo para enviar/recibir
"""

import can
import serial.tools.list_ports
from config import SERIAL_PORT, DCAN_BITRATE


def detect_kdcan_port() -> str | None:
    """
    Escanea puertos COM y devuelve el del cable K+DCAN (chip FTDI).
    Útil si no sabes en qué COM está el cable.
    """
    ports = serial.tools.list_ports.comports()
    for p in ports:
        desc = (p.description or "").upper()
        mfr  = (p.manufacturer or "").upper()
        if "FTDI" in desc or "FTDI" in mfr or "USB SERIAL" in desc:
            print(f"[+] K+DCAN detectado en: {p.device}  ({p.description})")
            return p.device
    print("[!] No se detectó cable K+DCAN. Verifica drivers FTDI.")
    return None


class CANConnection:
    """
    Context manager que abre y cierra la conexión CAN con el cable K+DCAN.

    Ejemplo:
        with CANConnection(port="COM3") as bus:
            msg = bus.recv(timeout=2.0)
    """

    def __init__(self, port: str = SERIAL_PORT, bitrate: int = DCAN_BITRATE):
        self.port    = port
        self.bitrate = bitrate
        self.bus     = None

    def connect(self) -> can.BusABC:
        """Abre la conexión y devuelve el bus CAN."""
        print(f"[*] Conectando a {self.port} @ {self.bitrate // 1000} kbps ...")
        self.bus = can.interface.Bus(
            interface="serial",     # interface nativo python-can para K+DCAN
            channel=self.port,
            bitrate=self.bitrate,
        )
        print(f"[+] Bus CAN abierto: {self.bus.channel_info}")
        return self.bus

    def disconnect(self):
        if self.bus:
            self.bus.shutdown()
            print("[*] Bus CAN cerrado.")
            self.bus = None

    # ── Context manager ──────────────────────────────────────────────────────────
    def __enter__(self) -> can.BusABC:
        return self.connect()

    def __exit__(self, *_):
        self.disconnect()
