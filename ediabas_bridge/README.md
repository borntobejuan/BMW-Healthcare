# EdiabasBridge

Puente en C# entre Python y `api64.dll` (EDIABAS).

## Por qué existe

`api64.dll` (de EdiabasLib) exporta funciones nativas planas
(`__apiInit`, `__apiJob`, etc.) pero internamente depende del runtime
.NET (CLR) para funcionar. Un proceso Python puro que la carga vía
`ctypes` nunca inicializa ese CLR, y las llamadas provocan corrupción
de memoria (`access violation`).

Este ejecutable **es** una app .NET real — el CLR arranca automáticamente
al iniciar el proceso — así que puede llamar a la DLL sin problemas.
Python simplemente lo ejecuta como subproceso y lee su salida JSON.

```
[Python] → subprocess.run(EdiabasBridge.exe) → [api64.dll con CLR OK] → JSON → [Python]
```

## Compilación

Requiere el SDK de .NET (no solo el Runtime) — verifica con:

```powershell
dotnet --list-sdks
```

Desde la carpeta `ediabas_bridge/`:

```powershell
cd ediabas_bridge
dotnet publish -c Release -r win-x86 --self-contained true
```

**Importante:** `win-x86` (32 bits), no `win-x64` — porque `api32.dll`
(la DLL original de BMW, la misma que usa INPA) es de 32 bits.

Esto genera el ejecutable en:
```
ediabas_bridge/bin/Release/net8.0/win-x86/publish/EdiabasBridge.exe
```

Esa es la ruta que espera `core/ediabas_bridge_client.py` por defecto.

## Prueba manual (sin Python)

```powershell
cd ediabas_bridge\bin\Release\net8.0\win-x86\publish
.\EdiabasBridge.exe job D_MOTOR IDENTIFIKATION
```

Deberías ver una línea JSON con los resultados, o `{"error": "..."}`
si algo falla — pero ya no debería crashear con access violation,
porque P/Invoke de .NET resuelve la convención de llamada real
automáticamente, sin necesidad de adivinar la decoración stdcall a mano.

## Uso desde Python

```python
from core.ediabas_bridge_client import EdiabasBridgeClient

client = EdiabasBridgeClient()
result = client.run_job("D_MOTOR", "IDENTIFIKATION")
print(result.sets)
```