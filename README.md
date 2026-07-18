# BMW E87 120i — EDIABAS Diagnostic & Telemetry Tool

Lectura de módulos BMW vía EDIABAS (la misma tecnología que usa INPA),
con telemetría en vivo hacia InfluxDB + Grafana.

---

## Arquitectura

```
[Cable K+DCAN USB]
        │
   api32.dll (EDIABAS, original BMW)
        │
   apiNET32.dll (wrapper .NET oficial)
        │
   EdiabasBridge.exe  ← app .NET, evita crashes de ctypes puro
        │
   subprocess + JSON
        │
   core/ediabas_bridge_client.py  (Python)
        │
   ┌────┴────┐
   │         │
 main.py   telemetry/poller.py
 (dtc/info)     │
             InfluxPublisher → InfluxDB → Grafana
```

`api32.dll` es la DLL original de BMW y necesita convenciones de
llamada específicas que `ctypes` puro no resuelve de forma fiable.
`EdiabasBridge.exe` es una pequeña app .NET que sí puede llamarla
(vía el wrapper oficial `apiNET32.dll`) y expone el resultado como
JSON simple por stdout — Python solo lanza el proceso y lee la salida.

---

## Requisitos

- Cable K+DCAN USB (genérico, chip FTDI) — confirmado funcional
- EDIABAS/INPA instalado en `C:\EDIABAS\`
- SDK de .NET (`dotnet --list-sdks` debe mostrar algo)
- Python 3.12 de 32 bits *o* 64 bits (el bridge C# resuelve la
  arquitectura de `api32.dll`; Python no necesita ser 32 bits)
- Docker Desktop (para InfluxDB + Grafana)

---

## Instalación

```powershell
# 1. Dependencias Python
pip install -r requirements.txt

# 2. Compilar el puente EDIABAS (una sola vez)
cd ediabas_bridge
dotnet publish -c Release -r win-x86 --self-contained true
cd ..

# 3. Levantar InfluxDB + Grafana
cd infra
docker compose up -d
cd ..
```

---

## Uso

### Explorar módulos y jobs (sin conocer nombres de antemano)

```powershell
# Listar jobs reales de un .PRG/.GRP (no requiere coche)
C:\EDIABAS\Bin\bestinfo.exe C:\EDIABAS\ECU\d71n47a0.prg

# Ejecutar un job y ver resultados (requiere coche + contacto puesto)
python explore_ediabas.py d71n47a0 IDENT
python explore_ediabas.py d71n47a0 STATUS_MOTORDREHZAHL
```

### Leer DTCs e info de un módulo

```powershell
python main.py dtc DME
python main.py info DME
```

### Telemetría en vivo → InfluxDB → Grafana

```powershell
python main.py live
python main.py live --interval 2.0   # cada 2s en vez de 1s
```

Grafana en `http://localhost:3000` (admin/admin) — dashboard
"BMW E87 120i — Live EDIABAS Telemetry" cargado automáticamente.

### Simular sin coche (para probar el dashboard)

```powershell
python simulate.py
python simulate.py --scenario highway
```

---

## Estructura del proyecto

```
bmw_e87_can/
├── main.py                    # CLI: dtc, info, live
├── explore_ediabas.py         # Herramienta de introspección
├── simulate.py                # Simulador (mismo formato que el poller real)
├── requirements.txt
│
├── core/
│   ├── ediabas_config.py      # Rutas, módulos, jobs, catálogo de métricas
│   └── ediabas_bridge_client.py  # Cliente Python del puente C#
│
├── ediabas_bridge/            # Puente C# (.NET) — habla con api32.dll
│   ├── Program.cs
│   ├── EdiabasBridge.csproj
│   └── README.md
│
├── telemetry/
│   ├── publisher.py           # Escritura batch en InfluxDB
│   └── poller.py              # Polling periódico de métricas EDIABAS
│
└── infra/
    ├── docker-compose.yml     # InfluxDB + Grafana
    └── grafana/                # Dashboards y datasources provisionados
```

---

## Módulos confirmados

| Módulo | Fichero real | Estado |
|--------|--------------|--------|
| DME (motor) | `d71n47a0.prg` | ✅ N46/MEV9 — 208 jobs catalogados |
| KOMBI | `D_KOMBI.grp` | Wrapper — pendiente `IDENTIFIKATION` con coche |
| DSC | `D_DSC.grp` | Wrapper — pendiente `IDENTIFIKATION` con coche |
| CAS | `D_CAS.grp` | Wrapper — pendiente `IDENTIFIKATION` con coche |

Ver `core/ediabas_config.py` → `ECU_MODULES` y `TELEMETRY_METRICS`
para el estado exacto de cada job y nombre de campo — algunos
`result_field` en `TELEMETRY_METRICS` son candidatos aún no
confirmados contra el coche real (se indica en el propio fichero).

---

## Solución de problemas

| Síntoma | Causa | Solución |
|---|---|---|
| `access violation` con ctypes puro | `api32.dll` necesita convención de llamada exacta que ctypes no resuelve bien | Usar siempre el bridge C#, no ctypes directo |
| Error EDIABAS 13 (IFH-0003) | Cable no conectado al coche o sin contacto | Conectar OBD + contacto posición II |
| Error EDIABAS 98 (SYS-0008) | Job no existe en ese `.PRG`/`.GRP` | Verificar con `bestinfo.exe` o `_JOBS` |
| Set de resultados vacío | `result_field` no coincide con el nombre real | Usar `explore_ediabas.py` para descubrir el campo real |
| `EdiabasBridge.exe` no encontrado | No compilado aún | `dotnet publish` (ver Instalación) |
