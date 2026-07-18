"""
Configuración EDIABAS para BMW E87 120i.

EDIABAS trabaja con "jobs" definidos en ficheros .PRG/.GRP instalados
en C:\\EDIABAS\\ECU\\. No hablamos bytes CAN crudos — le pedimos a la DLL
un job con nombre y nos devuelve resultados ya decodificados.
"""

import os

# ─── Rutas EDIABAS ───────────────────────────────────────────────────────────────
EDIABAS_BIN_PATH = r"C:\EDIABAS\Bin"
EDIABAS_DLL_PATH = os.path.join(EDIABAS_BIN_PATH, "api32.dll")   # DLL original BMW — la que usa INPA
EDIABAS_ECU_PATH = r"C:\EDIABAS\ECU"

# ─── Timeouts ───────────────────────────────────────────────────────────────────
JOB_POLL_INTERVAL = 0.05   # segundos entre polls de apiState()
JOB_TIMEOUT        = 10.0  # segundos máximo esperando un job

# ─── Módulos BMW E87 → nombre de fichero ECU (sin extensión) ───────────────────
# CONFIRMADO con bestinfo.exe + coche real: tu motor es un N46 con
# centralita MEV9. D_MOTOR es solo un wrapper con 1 job (INFO) — para
# leer datos reales hay que usar el .PRG específico directamente.
ECU_MODULES = {
    "DME":   "d71n47a0",  # Motor N46/MEV9 — .PRG específico (208 jobs reales)
    "EGS":   "D_EGS",     # Caja de cambios automática (aún no verificado)
    "DSC":   "D_DSC",     # Control de estabilidad / ABS (aún no verificado)
    "KOMBI": "D_KOMBI",   # Cuadro de instrumentos (aún no verificado)
    "CAS":   "D_CAS",     # Car Access System (arranque, aún no verificado)
    "EPS":   "D_EPS",     # Dirección asistida eléctrica (aún no verificado)
}

# ─── Jobs EDIABAS reales (confirmados con bestinfo.exe sobre d71n47a0.prg) ──────
# Motor real: N46 con centralita MEV9 (confirmado con job INFO de D_MOTOR)
JOB_READ_DTC       = "FS_LESEN"        # Leer códigos de fallo (DTCs)
JOB_CLEAR_DTC       = "FS_LOESCHEN"    # Borrar códigos de fallo
JOB_IDENT           = "IDENT"          # Identificación de la ECU (NO "IDENTIFIKATION")
JOB_INFO            = "INFO"           # Info general del .PRG (motor, comentario)

# Jobs de estado en vivo — cada uno da UN valor. Se ejecutan por separado
# (a diferencia de otros ECUs BMW, este N46/MEV9 no tiene un STATUS_MOTOR
# único que agrupe todo; hay que llamarlos uno a uno).
JOB_RPM              = "STATUS_MOTORDREHZAHL"          # RPM del motor
JOB_COOLANT_TEMP     = "STATUS_KUEHLMITTELTEMPERATUR"  # Temp. refrigerante
JOB_ENGINE_TEMP      = "STATUS_MOTORTEMPERATUR"        # Temp. motor (general)
JOB_INTAKE_AIR_TEMP  = "STATUS_ANSAUGLUFTTEMPERATUR"   # Temp. aire admisión
JOB_BATTERY_VOLTAGE  = "STATUS_UBATT"                  # Voltaje batería
JOB_BOOST_ACTUAL     = "STATUS_LADEDRUCK_IST"          # Presión turbo real
JOB_BOOST_TARGET     = "STATUS_LADEDRUCK_SOLL"         # Presión turbo objetivo
JOB_ODOMETER         = "STATUS_KILOMETERSTAND"         # Kilometraje
JOB_OIL_LEVEL        = "STATUS_OELNIVEAU"              # Nivel de aceite
JOB_AIR_MASS         = "STATUS_LUFTMASSE_IST"          # Masa de aire real
JOB_ATMOSPHERIC_PRESSURE = "STATUS_ATMOSPHAERENDRUCK"  # Presión atmosférica

# ─── Resultados esperados por job (nombres de campo tal como los devuelve EDIABAS) ──
# Estos nombres varían según el job — usa explore_ediabas.py con el coche
# conectado para confirmar los nombres de campo reales de cada STATUS_xxx.
DTC_RESULT_FIELDS = ["F_ORT_0_FCODE", "F_ORT_0_ATEXT", "F_ORT_0_UW_KM"]

# ─── Catálogo de telemetría en vivo ─────────────────────────────────────────────
# Cada entrada define: qué job EDIABAS llamar, en qué ECU, qué campo del
# resultado leer, y cómo publicarlo en InfluxDB (measurement + field).
#
# IMPORTANTE: "result_field" son nombres CANDIDATOS, aún no confirmados
# contra el coche real (salvo que se indique lo contrario). Usa
# explore_ediabas.py para verificarlos y ajústalos aquí — es la ÚNICA
# fuente de verdad que necesita tocarse; el resto del pipeline no cambia.
#
# Campos confirmados hasta ahora: ECU, COMMENT (job INFO en D_MOTOR).
TELEMETRY_METRICS = [
    {
        "id":            "rpm",
        "ecu":           "DME",
        "job":           JOB_RPM,
        "result_field":  "STAT_UMDR_MOTOR_W",   # candidato, pendiente confirmar
        "measurement":   "engine",
        "field":         "rpm",
        "cast":          float,
    },
    {
        "id":            "coolant_temp",
        "ecu":           "DME",
        "job":           JOB_COOLANT_TEMP,
        "result_field":  "STAT_TEMP_MOTOR_W",   # candidato, pendiente confirmar
        "measurement":   "temperatures",
        "field":         "coolant_temp_c",
        "cast":          float,
    },
    {
        "id":            "intake_air_temp",
        "ecu":           "DME",
        "job":           JOB_INTAKE_AIR_TEMP,
        "result_field":  "STAT_TEMP_ANSAUGLUFT_W",  # candidato, pendiente confirmar
        "measurement":   "temperatures",
        "field":         "intake_air_temp_c",
        "cast":          float,
    },
    {
        "id":            "battery_voltage",
        "ecu":           "DME",
        "job":           JOB_BATTERY_VOLTAGE,
        "result_field":  "STAT_UBATT_W",         # candidato, pendiente confirmar
        "measurement":   "electrical",
        "field":         "battery_voltage_v",
        "cast":          float,
    },
    {
        "id":            "odometer",
        "ecu":           "DME",
        "job":           JOB_ODOMETER,
        "result_field":  "STAT_KILOMETERSTAND_W",  # candidato, pendiente confirmar
        "measurement":   "vehicle",
        "field":         "odometer_km",
        "cast":          float,
    },
]