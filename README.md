# BMW E87 120i — CAN Bus Diagnostic Tool

Setup mínimo en Python para leer módulos del BMW E87 a través del cable K+DCAN USB.

---

## Requisitos de hardware

- Cable **K+DCAN USB** con chip **FTDI** (genuino, no clon CH340)
- Switch del cable en posición **D-CAN** (no K-LINE)
- BMW E87 con **contacto en posición II** (sin arrancar)

---

## Instalación

```bash
# 1. Instalar dependencias
pip install -r requirements.txt

# 2. Instalar drivers FTDI (solo primera vez)
# → https://ftdichip.com/drivers/vcp-drivers/
# → Device Manager → Puerto COM → Propiedades:
#     · COM port: COM3 (o COM9, máximo COM9)
#     · Latency Timer: 1 ms  ← MUY IMPORTANTE

# 3. Ajustar el puerto en core/config.py
SERIAL_PORT = "COM3"   # Windows
# SERIAL_PORT = "/dev/ttyUSB0"  # Linux
```

---

## Uso

### Paso 1 — Verificar que el cable recibe tráfico
```bash
python main.py sniff
python main.py sniff --duration 30 --known   # solo IDs conocidos del E87
```
Si ves tramas CAN en pantalla, el cable funciona ✓

### Paso 2 — Leer DTCs del motor (DME)
```bash
python main.py dtc DME
python main.py dtc DSC
python main.py dtc ABS
```

### Paso 3 — Leer info de la ECU
```bash
python main.py info DME
python main.py info KOMBI
```

### Paso 4 — Leer VIN
```bash
python main.py vin
```

---

## Estructura del proyecto

```
bmw_e87_can/
├── main.py               # Punto de entrada CLI
├── requirements.txt
└── core/
    ├── config.py         # Parámetros CAN, IDs de módulos
    ├── connection.py     # Conexión python-can con K+DCAN
    ├── sniffer.py        # Escucha tráfico raw del bus
    └── uds_client.py     # Cliente UDS (leer DTCs, DIDs, VIN)
```

---

## Stack de protocolos

```
[Cable K+DCAN USB]
       │
  [pyserial / python-can]   ← CAN físico a 500 kbps
       │
  [can-isotp]               ← Fragmentación ISO-TP (ISO 15765-2)
       │
  [udsoncan]                ← Servicios UDS (ISO 14229)
       │
  [Tu código]               ← read_dtcs(), read_vin(), etc.
```

---

## Buses del E87

| Bus    | Velocidad | Módulos                        |
|--------|-----------|-------------------------------|
| D-CAN  | 500 kbps  | OBD port → gateway diagnóstico |
| PT-CAN | 500 kbps  | DME, EGS, DSC, ABS, CAS       |
| K-CAN  | 100 kbps  | KOMBI, FRM, luces, puertas     |

---

## Solución de problemas

| Síntoma                        | Causa probable               | Solución                          |
|-------------------------------|------------------------------|-----------------------------------|
| No se detecta el cable         | Driver CH340 en vez de FTDI  | Instalar drivers FTDI VCP         |
| Sin tramas en `sniff`          | Switch en K-LINE             | Cambiar switch a D-CAN            |
| Sin tramas en `sniff`          | Contacto apagado             | Poner contacto posición II        |
| Timeout en sesión UDS          | Latency Timer alto           | Poner Latency Timer = 1 ms        |
| Error "puerto no encontrado"   | COM > 9                      | Reasignar a COM3 en Device Manager|
