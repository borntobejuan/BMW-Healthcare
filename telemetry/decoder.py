"""
Decoder de tramas CAN del BMW E87 120i.

Convierte bytes raw del bus CAN en valores físicos con unidades reales.
Cada función recibe msg.data (bytearray de 8 bytes) y devuelve un dict
con los campos decodificados listos para escribir en InfluxDB.

Referencias:
  - IDs y fórmulas del PT-CAN E87/E90 documentados por la comunidad
  - Verificados contra INPA y proyectos de reverse engineering del E87
"""

from __future__ import annotations
import struct
from can import Message


# ── Tipo de retorno ──────────────────────────────────────────────────────────────
# measurement: nombre de la "tabla" en InfluxDB
# tags:        etiquetas (strings, para filtrar/agrupar)
# fields:      valores numéricos a graficar
DecodedFrame = dict  # {"measurement": str, "tags": dict, "fields": dict}


# ── Decoders por CAN ID ──────────────────────────────────────────────────────────

def _decode_0x0AA(data: bytes) -> DecodedFrame | None:
    """
    0x0AA — Motor: RPM + estado de arranque
    Byte 0-1: RPM  (valor * 0.25 → rpm reales)
    Byte 2:   Estado motor (0=apagado, 1=arrancando, 2=encendido)
    """
    if len(data) < 3:
        return None
    raw_rpm   = struct.unpack_from(">H", data, 0)[0]
    rpm       = raw_rpm * 0.25
    estado    = data[2] & 0x03
    estados   = {0: "off", 1: "cranking", 2: "running"}
    return {
        "measurement": "engine",
        "tags":   {"module": "DME"},
        "fields": {
            "rpm":          round(rpm, 1),
            "engine_state": estados.get(estado, "unknown"),
        },
    }


def _decode_0x0A9(data: bytes) -> DecodedFrame | None:
    """
    0x0A9 — Temperaturas motor
    Byte 1: Temp refrigerante  (valor - 48 → °C)
    Byte 2: Temp aceite        (valor - 48 → °C)
    """
    if len(data) < 3:
        return None
    coolant_c = data[1] - 48
    oil_c     = data[2] - 48
    # Filtrar valores fuera de rango físico
    if not (-40 <= coolant_c <= 200):
        return None
    return {
        "measurement": "temperatures",
        "tags":   {"module": "DME"},
        "fields": {
            "coolant_temp_c": coolant_c,
            "oil_temp_c":     oil_c,
        },
    }


def _decode_0x0CE(data: bytes) -> DecodedFrame | None:
    """
    0x0CE — Velocidades de rueda individuales (ABS/DSC)
    Bytes 0-1: Rueda delantera izquierda  (FL)
    Bytes 2-3: Rueda delantera derecha    (FR)
    Bytes 4-5: Rueda trasera izquierda    (RL)
    Bytes 6-7: Rueda trasera derecha      (RR)
    Factor: * 0.05625 → km/h
    """
    if len(data) < 8:
        return None
    fl = struct.unpack_from(">H", data, 0)[0] * 0.05625
    fr = struct.unpack_from(">H", data, 2)[0] * 0.05625
    rl = struct.unpack_from(">H", data, 4)[0] * 0.05625
    rr = struct.unpack_from(">H", data, 6)[0] * 0.05625
    return {
        "measurement": "wheel_speeds",
        "tags":   {"module": "DSC"},
        "fields": {
            "fl_kmh": round(fl, 2),
            "fr_kmh": round(fr, 2),
            "rl_kmh": round(rl, 2),
            "rr_kmh": round(rr, 2),
        },
    }


def _decode_0x130(data: bytes) -> DecodedFrame | None:
    """
    0x130 — Velocidad del vehículo (KOMBI)
    Bytes 0-1: Velocidad * 0.1 → km/h
    """
    if len(data) < 2:
        return None
    raw   = struct.unpack_from(">H", data, 0)[0]
    speed = raw * 0.1
    if speed > 350:   # filtro anti-ruido
        return None
    return {
        "measurement": "vehicle",
        "tags":   {"module": "KOMBI"},
        "fields": {"speed_kmh": round(speed, 1)},
    }


def _decode_0x153(data: bytes) -> DecodedFrame | None:
    """
    0x153 — Posición acelerador + carga motor
    Byte 0: Posición pedal acelerador (0-255 → 0-100%)
    Byte 1: Carga motor               (0-255 → 0-100%)
    """
    if len(data) < 2:
        return None
    throttle = round(data[0] / 255 * 100, 1)
    load     = round(data[1] / 255 * 100, 1)
    return {
        "measurement": "engine",
        "tags":   {"module": "DME"},
        "fields": {
            "throttle_pct": throttle,
            "engine_load_pct": load,
        },
    }


def _decode_0x1D0(data: bytes) -> DecodedFrame | None:
    """
    0x1D0 — Transmisión (EGS)
    Byte 0: Marcha actual (0=punto muerto, 1-6=marchas, 7=marcha atrás)
    Byte 1: Marcha seleccionada
    """
    if len(data) < 2:
        return None
    gear_map = {0: "N", 7: "R"}
    actual   = gear_map.get(data[0], str(data[0]))
    selected = gear_map.get(data[1], str(data[1]))
    return {
        "measurement": "transmission",
        "tags":   {"module": "EGS"},
        "fields": {
            "gear_actual":   data[0],
            "gear_selected": data[1],
        },
    }


def _decode_0x3B4(data: bytes) -> DecodedFrame | None:
    """
    0x3B4 — Batería / alternador
    Bytes 0-1: Tensión batería * 0.05 → V
    Byte 2:    Estado de carga (0-100%)
    """
    if len(data) < 3:
        return None
    voltage = struct.unpack_from(">H", data, 0)[0] * 0.05
    soc     = data[2]
    if not (8.0 <= voltage <= 18.0):  # filtro físico
        return None
    return {
        "measurement": "electrical",
        "tags":   {"module": "DME"},
        "fields": {
            "battery_voltage_v": round(voltage, 2),
            "battery_soc_pct":   soc,
        },
    }


def _decode_0x200(data: bytes) -> DecodedFrame | None:
    """
    0x200 — Ángulo de dirección (EPS)
    Bytes 0-1: Ángulo signed * 0.1 → grados (negativo=izquierda)
    Byte 2:    Velocidad angular del volante
    """
    if len(data) < 3:
        return None
    raw_angle    = struct.unpack_from(">h", data, 0)[0]  # signed
    angle_deg    = raw_angle * 0.1
    angular_vel  = data[2]
    return {
        "measurement": "steering",
        "tags":   {"module": "EPS"},
        "fields": {
            "steering_angle_deg": round(angle_deg, 1),
            "steering_speed":     angular_vel,
        },
    }


# ── Tabla de dispatch ────────────────────────────────────────────────────────────
_DECODERS: dict[int, callable] = {
    0x0AA: _decode_0x0AA,
    0x0A9: _decode_0x0A9,
    0x0CE: _decode_0x0CE,
    0x130: _decode_0x130,
    0x153: _decode_0x153,
    0x1D0: _decode_0x1D0,
    0x3B4: _decode_0x3B4,
    0x200: _decode_0x200,
}


def decode(msg: Message) -> DecodedFrame | None:
    """
    Punto de entrada principal. Recibe un mensaje CAN y devuelve
    el frame decodificado, o None si el ID no está mapeado o hay error.
    """
    decoder = _DECODERS.get(msg.arbitration_id)
    if decoder is None:
        return None
    try:
        return decoder(bytes(msg.data))
    except Exception:
        return None


def supported_ids() -> list[int]:
    """Lista de CAN IDs que este decoder conoce."""
    return list(_DECODERS.keys())
