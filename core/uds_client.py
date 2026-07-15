"""
Cliente UDS (ISO 14229) sobre ISO-TP (ISO 15765-2) para BMW E87.

Permite:
  - Abrir sesiones de diagnóstico
  - Leer DTCs (códigos de fallo)
  - Leer identificadores de datos (DID)
  - Leer información ECU

BMW E-series usa "Extended 11-bit addressing" en ISO-TP,
soportado nativamente por la librería can-isotp.

Stack de capas:
  [python-can]  →  CAN físico (cable K+DCAN)
  [can-isotp]   →  fragmentación/reensamblado ISO-TP
  [udsoncan]    →  protocolo UDS (servicios de diagnóstico)
"""

import isotp
import udsoncan
from udsoncan.connections import PythonIsoTpConnection
from udsoncan.client import Client
from udsoncan import services
import can
from core.config import MODULE_IDS, REQUEST_TIMEOUT, DCAN_BITRATE


# Parámetros ISO-TP recomendados para BMW E87
ISOTP_PARAMS = {
    "stmin":           25,    # 25 ms entre frames consecutivos
    "blocksize":        8,    # 8 frames antes de nuevo flow control
    "wftmax":           0,    # No esperar wait frames
    "tx_data_length":   8,    # CAN 2.0: payload de 8 bytes
    "tx_padding":    0x00,    # Padding con 0x00
    "rx_flowcontrol_timeout": 1000,  # ms
    "rx_consecutive_frame_timeout": 1000,
    "squash_nones": False,
    "max_frame_size": 4095,
}


class BMWModuleClient:
    """
    Cliente UDS para un módulo específico del BMW E87.

    Uso:
        with CANConnection(port="COM3") as bus:
            client = BMWModuleClient(bus, module="DME")
            with client.session():
                dtcs = client.read_dtcs()
                vin  = client.read_vin()
    """

    def __init__(self, bus: can.BusABC, module: str = "DME"):
        if module not in MODULE_IDS:
            raise ValueError(f"Módulo desconocido: {module}. Disponibles: {list(MODULE_IDS)}")

        ids = MODULE_IDS[module]
        self.module = module
        self.bus    = bus

        # Dirección ISO-TP: "Extended 11-bit" usado por BMW E-series
        self.tp_addr = isotp.Address(
            isotp.AddressingMode.Normal_11bits,
            txid=ids["tx"],
            rxid=ids["rx"],
        )
        self._client: Client | None = None

    def _build_stack(self) -> isotp.CanStack:
        return isotp.CanStack(
            bus=self.bus,
            address=self.tp_addr,
            params=ISOTP_PARAMS,
        )

    # ── Context manager de sesión UDS ────────────────────────────────────────────
    def session(self):
        """Context manager que abre y cierra la sesión UDS."""
        return _UDSSession(self)

    # ── Servicios UDS ────────────────────────────────────────────────────────────
    def read_dtcs(self, status_mask: int = 0x0C) -> list[dict]:
        """
        Lee DTCs del módulo (servicio UDS 0x19 — ReadDTCInformation).

        status_mask=0x0C: fallos confirmados (bit 2=confirmed, bit 3=pending)
        Devuelve lista de dicts con 'dtc' y 'status'.
        """
        if not self._client:
            raise RuntimeError("Llama dentro de un bloque 'with client.session()'")

        resp = self._client.get_dtc_by_status_mask(status_mask)
        result = []
        for entry in resp.dtcs:
            result.append({
                "dtc":    f"{entry.id:06X}",
                "status": entry.status.get_byte_as_named_bitfield(),
            })
        return result

    def read_vin(self) -> str:
        """Lee el VIN del módulo (DID 0xF190)."""
        if not self._client:
            raise RuntimeError("Llama dentro de un bloque 'with client.session()'")
        resp = self._client.read_data_by_identifier(udsoncan.DataIdentifier.VIN)
        return resp.service_data.values[udsoncan.DataIdentifier.VIN].decode("ascii", errors="replace")

    def read_ecu_info(self) -> dict:
        """Lee información de la ECU: nombre, versión de software, hardware."""
        if not self._client:
            raise RuntimeError("Llama dentro de un bloque 'with client.session()'")
        info = {}
        dids = {
            0xF18A: "ecu_name",
            0xF189: "software_version",
            0xF191: "hardware_version",
            0xF187: "part_number",
        }
        for did, key in dids.items():
            try:
                resp = self._client.read_data_by_identifier(did)
                raw  = resp.service_data.values[did]
                info[key] = raw.decode("ascii", errors="replace").strip()
            except Exception:
                info[key] = "N/A"
        return info

    def read_did(self, did: int) -> bytes:
        """Lee un DID arbitrario (raw bytes). Útil para explorar."""
        if not self._client:
            raise RuntimeError("Llama dentro de un bloque 'with client.session()'")
        resp = self._client.read_data_by_identifier(did)
        return resp.service_data.values[did]


class _UDSSession:
    """Context manager interno que gestiona el ciclo de vida de la sesión UDS."""

    def __init__(self, owner: BMWModuleClient):
        self.owner = owner
        self._stack: isotp.CanStack | None = None

    def __enter__(self) -> BMWModuleClient:
        self._stack = self.owner._build_stack()
        conn = PythonIsoTpConnection(self._stack)

        cfg = udsoncan.configs.default_client_config.copy()
        cfg["request_timeout"] = REQUEST_TIMEOUT

        self.owner._client = Client(conn, config=cfg)
        self.owner._client.__enter__()

        # Abre sesión de diagnóstico extendida (0x03)
        self.owner._client.change_session(
            services.DiagnosticSessionControl.Session.extendedDiagnosticSession
        )
        print(f"[+] Sesión UDS abierta con {self.owner.module}")
        return self.owner

    def __exit__(self, *_):
        if self.owner._client:
            try:
                # Vuelve a sesión default antes de cerrar
                self.owner._client.change_session(
                    services.DiagnosticSessionControl.Session.defaultSession
                )
            except Exception:
                pass
            self.owner._client.__exit__(None, None, None)
            self.owner._client = None
        print(f"[*] Sesión UDS cerrada ({self.owner.module})")
