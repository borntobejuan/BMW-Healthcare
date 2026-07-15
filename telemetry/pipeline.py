"""
Pipeline de telemetría en tiempo real.

Arquitectura de threads:
  ┌─────────────────┐     Queue      ┌──────────────────┐
  │  Thread: Reader │ ──── CAN ────► │  Thread: Writer  │
  │  (sniffer CAN)  │   messages     │  (decode+publish)│
  └─────────────────┘                └──────────────────┘

El Reader lee del bus CAN y mete mensajes en la queue.
El Writer saca de la queue, decodifica y publica en InfluxDB.

Separar lectura y escritura evita que una latencia en InfluxDB
haga perder tramas CAN.

Uso:
    python main.py live
    python main.py live --port COM3 --duration 0   (0 = infinito)
"""

from __future__ import annotations
import time
import queue
import threading
import logging
import signal
import sys

import can
from core.connection import CANConnection, detect_kdcan_port
from core.config import SERIAL_PORT
from telemetry.decoder import decode, supported_ids
from telemetry.publisher import InfluxPublisher

log = logging.getLogger(__name__)

# Tamaño máximo de la queue interna (tramas CAN en vuelo)
# Si el writer no da abasto, las tramas más antiguas se descartan
QUEUE_MAX = 1000


class LivePipeline:
    """
    Orquesta el pipeline completo CAN → InfluxDB.

    Uso:
        pipeline = LivePipeline(port="COM3")
        pipeline.run(duration=0)   # 0 = hasta Ctrl+C
    """

    def __init__(self, port: str = None):
        self.port      = port or detect_kdcan_port() or SERIAL_PORT
        self._queue:   queue.Queue[can.Message | None] = queue.Queue(maxsize=QUEUE_MAX)
        self._stop     = threading.Event()
        self._stats    = {"received": 0, "decoded": 0, "published": 0, "dropped": 0}

    def run(self, duration: float = 0.0):
        """
        Arranca el pipeline. duration=0 corre hasta Ctrl+C.
        """
        # Captura Ctrl+C para cierre limpio
        signal.signal(signal.SIGINT, self._handle_sigint)

        print(f"\n{'─'*55}")
        print(f"  BMW E87 Live Telemetry  |  puerto: {self.port}")
        print(f"  IDs decodificados: {[hex(i) for i in supported_ids()]}")
        print(f"  Ctrl+C para detener")
        print(f"{'─'*55}\n")

        deadline = (time.time() + duration) if duration > 0 else None

        with InfluxPublisher() as publisher:
            # Thread writer (decoder + publisher)
            writer_thread = threading.Thread(
                target=self._writer_loop,
                args=(publisher,),
                daemon=True,
                name="can-writer",
            )
            writer_thread.start()

            # Thread de stats periódicas
            stats_thread = threading.Thread(
                target=self._stats_loop,
                daemon=True,
                name="stats",
            )
            stats_thread.start()

            # Loop principal: lectura CAN (hilo principal)
            try:
                with CANConnection(port=self.port) as bus:
                    print("[+] Bus CAN conectado. Enviando datos a InfluxDB...\n")
                    while not self._stop.is_set():
                        if deadline and time.time() > deadline:
                            break
                        msg = bus.recv(timeout=0.5)
                        if msg is None:
                            continue
                        self._stats["received"] += 1
                        try:
                            self._queue.put_nowait(msg)
                        except queue.Full:
                            self._stats["dropped"] += 1
            except Exception as e:
                log.error(f"[Pipeline] Error en lectura CAN: {e}")
            finally:
                self._stop.set()
                self._queue.put(None)   # Señal de fin al writer
                writer_thread.join(timeout=5)
                self._print_final_stats()

    def _writer_loop(self, publisher: InfluxPublisher):
        """Thread que consume la queue, decodifica y publica."""
        while True:
            try:
                msg = self._queue.get(timeout=1.0)
            except queue.Empty:
                if self._stop.is_set():
                    break
                continue

            if msg is None:   # Señal de fin
                break

            frame = decode(msg)
            if frame is None:
                continue

            self._stats["decoded"] += 1
            publisher.publish(frame)
            self._stats["published"] += 1

    def _stats_loop(self):
        """Imprime estadísticas cada 5 segundos."""
        while not self._stop.is_set():
            time.sleep(5)
            s = self._stats
            qsize = self._queue.qsize()
            print(
                f"  [stats] recibidas={s['received']:>6}  "
                f"decodificadas={s['decoded']:>5}  "
                f"publicadas={s['published']:>5}  "
                f"descartadas={s['dropped']:>3}  "
                f"queue={qsize:>4}"
            )

    def _handle_sigint(self, *_):
        print("\n[*] Deteniendo pipeline...")
        self._stop.set()

    def _print_final_stats(self):
        s = self._stats
        print(f"\n{'─'*55}")
        print(f"  Resumen sesión")
        print(f"  Tramas recibidas:    {s['received']}")
        print(f"  Tramas decodificadas:{s['decoded']}")
        print(f"  Puntos publicados:   {s['published']}")
        print(f"  Tramas descartadas:  {s['dropped']}")
        print(f"{'─'*55}\n")
