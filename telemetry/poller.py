"""
Poller EDIABAS — lee métricas del catálogo TELEMETRY_METRICS a intervalos
regulares y las publica en InfluxDB.

A diferencia del bus CAN (donde los módulos transmiten solos y solo hay
que escuchar), EDIABAS funciona por request/response: hay que preguntar
activamente por cada dato, uno a uno, ejecutando un job distinto por
métrica. Esto es más lento que CAN raw, así que el intervalo de polling
es configurable y realista (varios Hz como máximo, no cientos).

Arquitectura:
  ┌───────────────┐   por cada métrica   ┌──────────────────┐
  │  Poll loop    │ ──── run_job() ────► │  EdiabasBridge   │
  │  (intervalo)  │ ◄─── resultado ────  │  (.exe subproc)  │
  └───────┬───────┘                      └──────────────────┘
          │
          ▼
  ┌───────────────┐
  │ InfluxPublisher│ ──► InfluxDB ──► Grafana
  └───────────────┘

Uso:
    python main.py live
    python main.py live --interval 2.0 --duration 0
"""

from __future__ import annotations
import time
import signal
import logging
import threading

from core.ediabas_bridge_client import EdiabasBridgeClient, EdiabasBridgeError, EdiabasBridgeCancelled
from core.ediabas_config import ECU_MODULES, TELEMETRY_METRICS, ANALYZER_RULES
from telemetry.publisher import InfluxPublisher
from telemetry.analyzer import MetricAnalyzer

log = logging.getLogger(__name__)


class EdiabasPoller:
    """
    Orquesta el polling periódico de métricas EDIABAS → analyzer → InfluxDB.

    Uso:
        poller = EdiabasPoller(interval=1.0)
        poller.run(duration=0)   # 0 = hasta Ctrl+C
    """

    def __init__(self, interval: float = 1.0):
        self.interval = interval
        self.client = EdiabasBridgeClient()
        self.analyzer = MetricAnalyzer(rules=ANALYZER_RULES)
        self._stop = threading.Event()
        self._stats = {"polled": 0, "published": 0, "errors": 0, "alerts": 0}

    def run(self, duration: float = 0.0, use_signal: bool = True):
        """
        Arranca el polling. duration=0 corre hasta Ctrl+C (o hasta stop()).

        use_signal=False evita registrar el manejador de Ctrl+C — necesario
        cuando run() se llama desde un thread que no es el principal (p.ej.
        una GUI), ya que signal.signal() solo funciona en el hilo principal.
        """
        if use_signal:
            signal.signal(signal.SIGINT, self._handle_sigint)

        print(f"\n{'─'*55}")
        print(f"  BMW E87 EDIABAS Live Telemetry")
        print(f"  Intervalo: {self.interval}s  |  Métricas: {len(TELEMETRY_METRICS)}")
        print(f"  Ctrl+C para detener")
        print(f"{'─'*55}\n")

        deadline = (time.time() + duration) if duration > 0 else None
        t_start = time.time()

        with InfluxPublisher() as publisher:
            while not self._stop.is_set():
                if deadline and time.time() > deadline:
                    break

                cycle_start = time.time()
                self._poll_once(publisher)

                elapsed = time.time() - t_start
                if int(elapsed) % 10 == 0:
                    self._print_stats(elapsed)

                # Mantener el ritmo del intervalo configurado — wait()
                # en vez de sleep() para salir al instante si stop()
                # se activa durante la espera entre ciclos.
                sleep_time = self.interval - (time.time() - cycle_start)
                if sleep_time > 0:
                    self._stop.wait(timeout=sleep_time)

        self.analyzer.save_state()
        self._print_final_stats()

    def stop(self):
        """Detiene el poller desde fuera (p.ej. botón de una GUI)."""
        self._stop.set()

    def _poll_once(self, publisher: InfluxPublisher):
        """Ejecuta un ciclo: lee cada métrica del catálogo y publica lo que responda."""
        for metric in TELEMETRY_METRICS:
            if self._stop.is_set():
                return

            self._stats["polled"] += 1
            try:
                ecu = ECU_MODULES.get(metric["ecu"], metric["ecu"])
                # cancel_event=self._stop: si se pulsa Detener MIENTRAS
                # esta llamada concreta está en curso (hasta 15s de
                # timeout), el subproceso EdiabasBridge.exe se mata al
                # momento en vez de esperar a que termine o expire.
                raw_value = self.client.read_field(
                    ecu, metric["job"], metric["result_field"],
                    cancel_event=self._stop,
                )

                if raw_value is None:
                    # Campo no encontrado — probablemente result_field aún
                    # no confirmado contra el coche real. No es un error
                    # fatal, solo se omite esta métrica en este ciclo.
                    continue

                value = metric["cast"](raw_value)
                frame = {
                    "measurement": metric["measurement"],
                    "tags":        {"module": metric["ecu"]},
                    "fields":      {metric["field"]: value},
                }
                # El analyzer devuelve el frame original + derivados + alertas
                for out_frame in self.analyzer.process(frame):
                    publisher.publish(out_frame)
                    self._stats["published"] += 1
                    if out_frame["measurement"] == "alerts":
                        self._stats["alerts"] += 1

            except EdiabasBridgeCancelled:
                return
            except EdiabasBridgeError as e:
                self._stats["errors"] += 1
                log.warning(f"[{metric['id']}] Error EDIABAS: {e}")
            except (ValueError, TypeError) as e:
                self._stats["errors"] += 1
                log.warning(f"[{metric['id']}] Error convirtiendo valor '{raw_value}': {e}")

    def _print_stats(self, elapsed: float):
        s = self._stats
        print(
            f"  [stats] t={elapsed:>6.0f}s  "
            f"consultas={s['polled']:>5}  "
            f"publicadas={s['published']:>5}  "
            f"errores={s['errors']:>3}"
        )

    def _print_final_stats(self):
        s = self._stats
        print(f"\n{'─'*55}")
        print(f"  Resumen sesión")
        print(f"  Consultas realizadas: {s['polled']}")
        print(f"  Puntos publicados:    {s['published']}")
        print(f"  Alertas emitidas:     {s['alerts']}")
        print(f"  Errores:              {s['errors']}")
        print(f"{'─'*55}\n")

    def _handle_sigint(self, *_):
        print("\n[*] Deteniendo poller...")
        self._stop.set()