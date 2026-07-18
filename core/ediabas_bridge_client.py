"""
Cliente EDIABAS vía subproceso (EdiabasBridge.exe).

api32.dll (la DLL original de BMW, la misma que usa INPA) no se puede
llamar de forma fiable desde ctypes puro — las pruebas con distintas
convenciones de llamada crashearon reiteradamente. En su lugar,
EdiabasBridge.exe es una app .NET real que usa el wrapper oficial
apiNET32.dll vía reflexión, y expone el resultado como JSON por stdout.

Uso:
    from core.ediabas_bridge_client import EdiabasBridgeClient

    client = EdiabasBridgeClient()
    result = client.run_job("d71n47a0", "STATUS_MOTORDREHZAHL")
    print(result.first())

    # O directamente el valor de un campo concreto:
    rpm = client.read_field("d71n47a0", "STATUS_MOTORDREHZAHL", "STAT_UMDR_MOTOR_W")

Cancelación (importante para GUIs):
    Cada llamada lanza un subproceso que puede tardar hasta
    JOB_TIMEOUT_SECONDS si el coche no responde. Para poder abortar
    desde fuera (p.ej. un botón "Detener"), pásale un
    threading.Event a `cancel_event` — el cliente mata el proceso
    en cuanto se activa, en vez de esperar al timeout completo.
"""

from __future__ import annotations
import json
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


# Ruta al ejecutable compilado — ver ediabas_bridge/README.md para compilarlo
# win-x86 (32 bits) porque api32.dll es de 32 bits.
BRIDGE_EXE_PATH = Path(__file__).parent.parent / "ediabas_bridge" / "bin" / "Release" / "net8.0" / "win-x86" / "publish" / "EdiabasBridge.exe"

JOB_TIMEOUT_SECONDS = 15   # timeout máximo por llamada individual
CANCEL_POLL_SECONDS = 0.1  # frecuencia con la que se comprueba cancel_event


class EdiabasBridgeError(Exception):
    """Error devuelto por EDIABAS a través del puente."""
    pass


class EdiabasBridgeCancelled(Exception):
    """La llamada fue abortada porque se activó cancel_event."""
    pass


@dataclass
class JobResult:
    ecu:  str
    job:  str
    sets: list[dict] = field(default_factory=list)

    def first(self) -> dict:
        return self.sets[0] if self.sets else {}


class EdiabasBridgeClient:
    """
    Cliente que delega en EdiabasBridge.exe (proceso .NET) para hablar
    con api32.dll, evitando los crashes de ctypes.
    """

    def __init__(self, exe_path: Path = BRIDGE_EXE_PATH):
        self.exe_path = exe_path
        if not self.exe_path.exists():
            raise FileNotFoundError(
                f"No se encuentra EdiabasBridge.exe en {self.exe_path}. "
                f"Compílalo primero (ver ediabas_bridge/README.md)."
            )

    def run_job(
        self,
        ecu: str,
        job: str,
        params: str = "",
        cancel_event: threading.Event | None = None,
    ) -> JobResult:
        """
        Ejecuta un job EDIABAS lanzando el subproceso EdiabasBridge.exe.

        Args:
            ecu:          nombre del fichero .PRG/.GRP sin extensión (ej. "d71n47a0")
            job:          nombre del job (ej. "IDENT", "STATUS_MOTORDREHZAHL")
            params:       parámetros del job (la mayoría no necesitan)
            cancel_event: si se activa mientras el subproceso está en
                          marcha, se mata inmediatamente en vez de
                          esperar al timeout (útil desde una GUI).

        Returns:
            JobResult con los sets de resultados.
        """
        cmd = [str(self.exe_path), "job", ecu, job, params]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        deadline = time.time() + JOB_TIMEOUT_SECONDS
        while proc.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                proc.kill()
                proc.wait(timeout=2)
                raise EdiabasBridgeCancelled(f"Job {ecu}.{job} cancelado por el usuario")
            if time.time() > deadline:
                proc.kill()
                proc.wait(timeout=2)
                raise EdiabasBridgeError(f"Timeout ejecutando job {ecu}.{job} (subproceso colgado)")
            time.sleep(CANCEL_POLL_SECONDS)

        stdout, stderr = proc.communicate()

        if not stdout.strip():
            raise EdiabasBridgeError(
                f"EdiabasBridge.exe no devolvió salida. stderr: {stderr.strip()}"
            )

        try:
            data = json.loads(stdout.strip().splitlines()[-1])
        except json.JSONDecodeError:
            raise EdiabasBridgeError(f"Salida no es JSON válido: {stdout!r}")

        if "error" in data:
            raise EdiabasBridgeError(data["error"])

        return JobResult(ecu=data["ecu"], job=data["job"], sets=data.get("sets", []))

    def read_field(
        self,
        ecu: str,
        job: str,
        result_field: str,
        cancel_event: threading.Event | None = None,
    ) -> str | None:
        """
        Conveniencia: ejecuta el job y devuelve directamente el valor
        de un campo concreto del primer set de resultados (o None si
        el job no lo devolvió — p.ej. nombre de campo incorrecto).
        """
        result = self.run_job(ecu, job, cancel_event=cancel_event)
        return result.first().get(result_field)