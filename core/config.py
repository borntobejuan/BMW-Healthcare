"""
Configuración central para BMW E87 120i
Cable: K+DCAN USB (chip FTDI)

El E87 usa dos buses CAN principales:
  - PT-CAN (Powertrain CAN): 500 kbps → Motor, caja, ABS, DSC
  - K-CAN (Karosserie CAN): 100 kbps → Carrocería, KOMBI, luces, puertas
El cable K+DCAN se conecta al D-CAN del OBD (500 kbps) que hace de gateway.
"""

# ─── Puerto serie ───────────────────────────────────────────────────────────────
# Windows: "COM3", "COM9", etc.  |  Linux/Mac: "/dev/ttyUSB0"
SERIAL_PORT = "COM3"

# ─── Velocidades CAN ────────────────────────────────────────────────────────────
DCAN_BITRATE   = 500_000   # D-CAN / PT-CAN  → 500 kbps
KCAN_BITRATE   = 100_000   # K-CAN (carrocería) → 100 kbps

# ─── Timeouts ───────────────────────────────────────────────────────────────────
REQUEST_TIMEOUT   = 2.0   # segundos para esperar respuesta UDS
SESSION_TIMEOUT   = 5.0   # timeout de sesión de diagnóstico

# ─── Dirección UDS estándar OBD ─────────────────────────────────────────────────
# BMW E-series usa addressing "Extended 11-bit" (no el Normal_11bits estándar)
# TX: tester → ECU  |  RX: ECU → tester
UDS_TX_ID = 0x6F1   # ID del tester (nosotros)
UDS_RX_ID = 0x6F1   # Funcional broadcast; cada módulo responde con su propio ID

# ─── IDs de módulos BMW E87 en el bus D-CAN ─────────────────────────────────────
# Formato: TX_ID (tester→módulo), RX_ID (módulo→tester)
MODULE_IDS = {
    "DME":   {"tx": 0x6F1, "rx": 0x12},   # Motor (Digital Motor Electronics)
    "EGS":   {"tx": 0x6F1, "rx": 0xA2},   # Caja de cambios automática
    "DSC":   {"tx": 0x6F1, "rx": 0x56},   # Control de estabilidad
    "ABS":   {"tx": 0x6F1, "rx": 0x56},   # ABS (mismo nodo que DSC en E87)
    "KOMBI": {"tx": 0x6F1, "rx": 0xD0},   # Cuadro de instrumentos
    "CAS":   {"tx": 0x6F1, "rx": 0x60},   # Car Access System (arranque)
    "FRM":   {"tx": 0x6F1, "rx": 0xE0},   # Footwell Module (luces/puertas)
    "EPS":   {"tx": 0x6F1, "rx": 0x0C1},  # Dirección asistida eléctrica
}

# ─── Mensajes CAN conocidos del E87 (sniffer / raw) ────────────────────────────
# IDs que transmiten módulos de forma continua en el bus (sin necesidad de UDS)
KNOWN_CAN_IDS = {
    0x0AA: "Estado motor / RPM",
    0x0A9: "Temperatura motor",
    0x0CE: "Velocidad ruedas (ABS)",
    0x130: "Velocidad del vehículo",
    0x153: "Posición acelerador",
    0x1D0: "Estado transmisión (EGS)",
    0x200: "Dirección (ángulo volante)",
    0x2B0: "Presión de freno",
    0x34F: "Estado climatización",
    0x3B4: "Estado batería / alternador",
    0x615: "Diagnóstico gateway",
}
