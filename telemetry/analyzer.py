"""
Capa de análisis de telemetría — se interpone entre la lectura
(poller real o simulador) y la publicación en InfluxDB.

    lectura → MetricAnalyzer.process(frame) → [frames] → publisher

Por cada frame de entrada devuelve una lista de frames de salida:
  1. El frame original, siempre (dato crudo intacto).
  2. Frames derivados (media móvil, tendencia) en el measurement
     "<measurement>_derived".
  3. Frames de alerta en el measurement "alerts" cuando una regla
     con histéresis se dispara o se recupera.

Diseño:
  - Sin dependencias externas (solo stdlib): deque para ventanas.
  - Estado en memoria por (measurement, field); opcionalmente
    persistible a JSON (hooks de baseline, ver más abajo).
  - Las reglas viven en core/ediabas_config.py → ANALYZER_RULES,
    junto al resto de la configuración del proyecto.

Hooks de baseline (fase 2, cuando haya datos reales del coche):
  - save_state()/load_state() ya persisten el histórico estadístico
    mínimo necesario (medias por métrica) en un JSON local.
  - La idea: tras N sesiones reales, esas medias se convierten en el
    "normal" de TU coche, y podrán definirse reglas relativas
    ("alerta si se desvía >X% de su baseline") en vez de absolutas.
"""

from __future__ import annotations
import json
import time
import logging
from collections import deque
from pathlib import Path

log = logging.getLogger(__name__)

STATE_FILE = Path(__file__).parent.parent / "analyzer_state.json"


# ────────────────────────────────────────────────────────────────────────────────
#  Reglas de umbral con histéresis
# ────────────────────────────────────────────────────────────────────────────────
class ThresholdRule:
    """
    Regla de umbral con histéresis temporal: la condición debe mantenerse
    durante `sustain_s` segundos seguidos para disparar la alerta (evita
    falsos positivos por picos puntuales), y debe dejar de cumplirse
    durante `recover_s` segundos para considerarla recuperada.

    Config (dict):
      id:          identificador único ("coolant_overheat")
      measurement: measurement del frame a vigilar ("temperatures")
      field:       field concreto ("coolant_temp_c")
      condition:   "above" | "below"
      value:       umbral numérico
      sustain_s:   segundos sostenidos para disparar (default 10)
      recover_s:   segundos por debajo para recuperar (default 15)
      severity:    "warning" | "critical" (default "warning")
      message:     texto humano para el panel
    """

    def __init__(self, cfg: dict):
        self.id          = cfg["id"]
        self.measurement = cfg["measurement"]
        self.field       = cfg["field"]
        self.condition   = cfg.get("condition", "above")
        self.value       = float(cfg["value"])
        self.sustain_s   = float(cfg.get("sustain_s", 10))
        self.recover_s   = float(cfg.get("recover_s", 15))
        self.severity    = cfg.get("severity", "warning")
        self.message     = cfg.get("message", self.id)

        self._breach_since:  float | None = None
        self._ok_since:      float | None = None
        self.active = False

    def _breaches(self, v: float) -> bool:
        return v > self.value if self.condition == "above" else v < self.value

    def update(self, v: float, now: float) -> str | None:
        """
        Devuelve "fired" si la alerta acaba de activarse, "recovered" si
        acaba de recuperarse, o None si no hay cambio de estado.
        """
        if self._breaches(v):
            self._ok_since = None
            if self._breach_since is None:
                self._breach_since = now
            if not self.active and (now - self._breach_since) >= self.sustain_s:
                self.active = True
                return "fired"
        else:
            self._breach_since = None
            if self._ok_since is None:
                self._ok_since = now
            if self.active and (now - self._ok_since) >= self.recover_s:
                self.active = False
                return "recovered"
        return None


# ────────────────────────────────────────────────────────────────────────────────
#  Serie temporal en memoria para derivadas
# ────────────────────────────────────────────────────────────────────────────────
class _Series:
    """Ventana deslizante de (timestamp, valor) para una métrica."""

    def __init__(self, window_s: float = 120.0):
        self.window_s = window_s
        self.points: deque[tuple[float, float]] = deque()
        # Estadísticos acumulados de toda la sesión (para baseline)
        self.session_count = 0
        self.session_mean  = 0.0

    def add(self, t: float, v: float):
        self.points.append((t, v))
        cutoff = t - self.window_s
        while self.points and self.points[0][0] < cutoff:
            self.points.popleft()
        # Media incremental de sesión (Welford simplificado)
        self.session_count += 1
        self.session_mean += (v - self.session_mean) / self.session_count

    def moving_avg(self, span_s: float) -> float | None:
        if not self.points:
            return None
        cutoff = self.points[-1][0] - span_s
        vals = [v for (t, v) in self.points if t >= cutoff]
        return sum(vals) / len(vals) if vals else None

    def trend_per_min(self, span_s: float = 60.0) -> float | None:
        """
        Pendiente (unidades/minuto) por regresión lineal simple sobre
        los últimos `span_s` segundos. None si no hay puntos suficientes.
        """
        if len(self.points) < 5:
            return None
        cutoff = self.points[-1][0] - span_s
        pts = [(t, v) for (t, v) in self.points if t >= cutoff]
        if len(pts) < 5:
            return None
        n = len(pts)
        t0 = pts[0][0]
        xs = [t - t0 for (t, _) in pts]
        ys = [v for (_, v) in pts]
        sx, sy = sum(xs), sum(ys)
        sxx = sum(x * x for x in xs)
        sxy = sum(x * y for x, y in zip(xs, ys))
        denom = n * sxx - sx * sx
        if denom == 0:
            return None
        slope_per_s = (n * sxy - sx * sy) / denom
        return slope_per_s * 60.0


# ────────────────────────────────────────────────────────────────────────────────
#  Analyzer principal
# ────────────────────────────────────────────────────────────────────────────────
class MetricAnalyzer:
    """
    Mantiene estado por métrica, evalúa reglas y genera frames derivados.

    Uso (en el poller o el simulador):
        analyzer = MetricAnalyzer(rules=ANALYZER_RULES)
        for out_frame in analyzer.process(frame):
            publisher.publish(out_frame)
    """

    def __init__(self, rules: list[dict] | None = None, derived_every_s: float = 10.0):
        self.series: dict[tuple[str, str], _Series] = {}
        self.rules: list[ThresholdRule] = [ThresholdRule(cfg) for cfg in (rules or [])]
        self.derived_every_s = derived_every_s
        self._last_derived: dict[tuple[str, str], float] = {}

    # ── Núcleo ──────────────────────────────────────────────────────────────────
    def process(self, frame: dict) -> list[dict]:
        """
        Procesa un frame y devuelve la lista de frames a publicar
        (siempre incluye el original).
        """
        out = [frame]
        now = time.time()
        measurement = frame["measurement"]
        tags = frame.get("tags", {})

        for field_name, value in frame.get("fields", {}).items():
            if not isinstance(value, (int, float)):
                continue

            key = (measurement, field_name)
            series = self.series.setdefault(key, _Series())
            series.add(now, float(value))

            # 1) Frames derivados, con rate-limit para no duplicar volumen
            if now - self._last_derived.get(key, 0) >= self.derived_every_s:
                self._last_derived[key] = now
                derived = self._build_derived(measurement, field_name, tags, series)
                if derived:
                    out.append(derived)

            # 2) Reglas de umbral
            for rule in self.rules:
                if rule.measurement == measurement and rule.field == field_name:
                    transition = rule.update(float(value), now)
                    if transition:
                        out.append(self._build_alert(rule, float(value), transition, tags))

        return out

    def _build_derived(self, measurement: str, field_name: str,
                       tags: dict, series: _Series) -> dict | None:
        fields = {}
        avg30 = series.moving_avg(30.0)
        if avg30 is not None:
            fields[f"{field_name}_avg30s"] = round(avg30, 2)
        trend = series.trend_per_min(60.0)
        if trend is not None:
            fields[f"{field_name}_trend_per_min"] = round(trend, 3)
        if not fields:
            return None
        return {
            "measurement": f"{measurement}_derived",
            "tags": tags,
            "fields": fields,
        }

    def _build_alert(self, rule: ThresholdRule, value: float,
                     transition: str, tags: dict) -> dict:
        state = 1 if transition == "fired" else 0
        log.warning(f"[ALERTA {transition.upper()}] {rule.id}: {rule.message} (valor={value})")
        return {
            "measurement": "alerts",
            "tags": {
                **tags,
                "rule":     rule.id,
                "severity": rule.severity,
            },
            "fields": {
                "active":  state,
                "value":   value,
                "message": rule.message,
            },
        }

    # ── Persistencia / hooks de baseline (fase 2) ───────────────────────────────
    def save_state(self, path: Path = STATE_FILE):
        """
        Guarda las medias de sesión por métrica. Con varias sesiones
        acumuladas, esto se convierte en el baseline de "lo normal"
        para este coche concreto.
        """
        data = {}
        for (measurement, field_name), series in self.series.items():
            if series.session_count > 0:
                data[f"{measurement}.{field_name}"] = {
                    "session_mean":  round(series.session_mean, 3),
                    "session_count": series.session_count,
                    "saved_at":      time.time(),
                }
        try:
            existing = json.loads(path.read_text()) if path.exists() else {"sessions": []}
        except (json.JSONDecodeError, OSError):
            existing = {"sessions": []}
        existing.setdefault("sessions", []).append(data)
        # Conservar solo las últimas 30 sesiones
        existing["sessions"] = existing["sessions"][-30:]
        path.write_text(json.dumps(existing, indent=2))
        log.info(f"Estado del analyzer guardado en {path}")

    def load_baseline(self, path: Path = STATE_FILE) -> dict[str, float]:
        """
        Devuelve {"measurement.field": media_histórica} promediando
        todas las sesiones guardadas. Base para futuras reglas
        relativas ("desviación >X% del baseline").
        """
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
        acc: dict[str, list[float]] = {}
        for session in data.get("sessions", []):
            for key, stats in session.items():
                acc.setdefault(key, []).append(stats["session_mean"])
        return {k: sum(v) / len(v) for k, v in acc.items()}