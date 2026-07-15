"""
Publisher — escribe frames decodificados en InfluxDB.

Usa escritura en batch para no saturar InfluxDB con una
llamada HTTP por cada trama CAN (el bus manda cientos por segundo).

Diseño:
  - Acumula puntos en memoria hasta BATCH_SIZE o FLUSH_INTERVAL segundos
  - Thread-safe: puede recibir datos desde el thread del sniffer
  - Si InfluxDB no está disponible, loguea el error y continúa
    (no bloquea el pipeline de lectura CAN)
"""

from __future__ import annotations
import os
import time
import threading
import logging
from datetime import datetime, timezone

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import WriteOptions

log = logging.getLogger(__name__)


class InfluxPublisher:
    """
    Publica frames decodificados en InfluxDB.

    Uso:
        publisher = InfluxPublisher()
        publisher.start()
        publisher.publish(decoded_frame)
        publisher.stop()

    O como context manager:
        with InfluxPublisher() as pub:
            pub.publish(frame)
    """

    def __init__(
        self,
        host:           str  = None,
        token:          str  = None,
        org:            str  = None,
        bucket:         str  = None,
        batch_size:     int  = None,
        flush_interval: int  = None,
    ):
        # Valores desde .env con fallback a defaults
        self.host           = host           or os.getenv("INFLUX_HOST",           "http://localhost:8086")
        self.token          = token          or os.getenv("INFLUX_TOKEN",          "bmw-local-token-changeme")
        self.org            = org            or os.getenv("INFLUX_ORG",            "bmw")
        self.bucket         = bucket         or os.getenv("INFLUX_BUCKET",         "e87")
        self.batch_size     = batch_size     or int(os.getenv("INFLUX_BATCH_SIZE", "50"))
        self.flush_interval = flush_interval or int(os.getenv("INFLUX_FLUSH_INTERVAL", "1"))

        self._client:    InfluxDBClient | None = None
        self._write_api                        = None
        self._lock       = threading.Lock()
        self._buffer:    list[Point]           = []
        self._last_flush = time.time()
        self._running    = False
        self._flush_thread: threading.Thread | None = None

    # ── Ciclo de vida ────────────────────────────────────────────────────────────
    def start(self):
        """Conecta a InfluxDB y arranca el thread de flush periódico."""
        self._client = InfluxDBClient(
            url=self.host,
            token=self.token,
            org=self.org,
        )
        self._write_api = self._client.write_api(
            write_options=WriteOptions(
                batch_size=1,       # Controlamos el batch manualmente
                flush_interval=99999,
            )
        )
        self._running = True
        self._flush_thread = threading.Thread(
            target=self._flush_loop,
            daemon=True,
            name="influx-flush",
        )
        self._flush_thread.start()
        log.info(f"[InfluxDB] Conectado a {self.host} / bucket={self.bucket}")

    def stop(self):
        """Flush final y cierre limpio."""
        self._running = False
        if self._flush_thread:
            self._flush_thread.join(timeout=5)
        self._flush_now()
        if self._write_api:
            self._write_api.close()
        if self._client:
            self._client.close()
        log.info("[InfluxDB] Conexión cerrada.")

    # ── Publicación ──────────────────────────────────────────────────────────────
    def publish(self, frame: dict):
        """
        Acepta un frame decodificado (salida de decoder.decode())
        y lo añade al buffer de escritura.

        frame = {
            "measurement": "engine",
            "tags":   {"module": "DME"},
            "fields": {"rpm": 1250.0, "throttle_pct": 15.2},
        }
        """
        point = self._frame_to_point(frame)
        if point is None:
            return

        with self._lock:
            self._buffer.append(point)
            should_flush = len(self._buffer) >= self.batch_size

        if should_flush:
            self._flush_now()

    def _frame_to_point(self, frame: dict) -> Point | None:
        try:
            p = Point(frame["measurement"])
            for k, v in frame.get("tags", {}).items():
                p = p.tag(k, v)
            for k, v in frame.get("fields", {}).items():
                p = p.field(k, v)
            p = p.time(datetime.now(timezone.utc), "ms")
            return p
        except Exception as e:
            log.warning(f"[InfluxDB] Error construyendo Point: {e}")
            return None

    def _flush_now(self):
        """Escribe el buffer actual en InfluxDB."""
        with self._lock:
            if not self._buffer:
                return
            batch = self._buffer[:]
            self._buffer.clear()
            self._last_flush = time.time()

        try:
            self._write_api.write(bucket=self.bucket, org=self.org, record=batch)
            log.debug(f"[InfluxDB] {len(batch)} puntos escritos.")
        except Exception as e:
            log.warning(f"[InfluxDB] Error escribiendo batch: {e}")

    def _flush_loop(self):
        """Thread que hace flush periódico aunque el buffer no esté lleno."""
        while self._running:
            time.sleep(0.1)
            if time.time() - self._last_flush >= self.flush_interval:
                self._flush_now()

    # ── Context manager ──────────────────────────────────────────────────────────
    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()