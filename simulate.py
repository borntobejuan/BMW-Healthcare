"""
Simulador de telemetría BMW E87 120i — modo EDIABAS.

Genera datos realistas de un coche en marcha y los publica en InfluxDB,
usando exactamente el mismo formato de "frame" que produce el poller
EDIABAS real (telemetry/poller.py). Así el dashboard de Grafana no
necesita distinguir si los datos vienen del coche real o simulados.

Simula un ciclo de conducción urbana:
  ralentí → aceleración → velocidad de crucero → frenada → ralentí

Uso:
    python simulate.py                    # ciclo infinito
    python simulate.py --duration 60      # 60 segundos
    python simulate.py --scenario city    # conducción urbana (default)
    python simulate.py --scenario highway # autopista
    python simulate.py --scenario idle    # solo ralentí (motor calentando)
"""

from __future__ import annotations
import time
import math
import random
import argparse
import threading
from dotenv import load_dotenv

load_dotenv()

from telemetry.publisher import InfluxPublisher


class DrivingSimulator:
    """
    Simula el estado físico del coche en cada tick y produce frames
    en el mismo formato measurement/tags/fields que usa el poller real
    (ver core/ediabas_config.py → TELEMETRY_METRICS).
    """

    def __init__(self, scenario: str = "city"):
        self.scenario = scenario
        self.t        = 0.0

        self.rpm          = 850.0
        self.speed_kmh    = 0.0
        self.throttle_pct = 0.0
        # En el escenario "overheat" partimos con el motor ya caliente
        # para que la alerta se dispare en ~1 min de prueba, no en 6.
        self.coolant_c    = 88.0 if scenario == "overheat" else 20.0
        self.intake_air_c = 18.0
        self.voltage_v    = 14.2
        self.odometer_km  = 187_432.0   # kilometraje inicial de ejemplo

    def tick(self, dt: float = 1.0) -> list[dict]:
        """Avanza `dt` segundos y devuelve los frames listos para InfluxDB."""
        self.t += dt
        self._update_state(dt)
        return self._build_frames()

    def _update_state(self, dt: float):
        if self.scenario == "idle":
            target_speed, target_throttle = 0.0, 0.0

        elif self.scenario == "highway":
            base = 120.0 + 20.0 * math.sin(self.t / 60)
            target_speed    = max(0, base + random.gauss(0, 2))
            target_throttle = 35.0 + 10.0 * math.sin(self.t / 30)

        else:  # city
            cycle = self.t % 60
            if cycle < 5:
                target_speed, target_throttle = 0.0, 0.0
            elif cycle < 20:
                target_speed    = min(50, (cycle - 5) * 3.5)
                target_throttle = 40.0 + random.gauss(0, 3)
            elif cycle < 35:
                target_speed    = 50.0 + random.gauss(0, 1)
                target_throttle = 18.0 + random.gauss(0, 2)
            elif cycle < 45:
                target_speed    = max(0, 50 - (cycle - 35) * 5)
                target_throttle = 0.0
            else:
                target_speed, target_throttle = 0.0, 0.0

        alpha_speed    = 1 - math.exp(-dt / 3.0)
        alpha_throttle = 1 - math.exp(-dt / 0.3)

        self.speed_kmh    += alpha_speed    * (target_speed    - self.speed_kmh)
        self.throttle_pct += alpha_throttle * (target_throttle - self.throttle_pct)
        self.throttle_pct  = max(0.0, min(100.0, self.throttle_pct))

        if self.speed_kmh < 1:
            target_rpm = 850 + self.throttle_pct * 20 + random.gauss(0, 20)
        else:
            target_rpm = 800 + self.speed_kmh * 35 + self.throttle_pct * 30
            target_rpm += random.gauss(0, 50)
        target_rpm = max(700.0, min(6800.0, target_rpm))
        alpha_rpm  = 1 - math.exp(-dt / 0.5)
        self.rpm  += alpha_rpm * (target_rpm - self.rpm)

        if self.scenario == "overheat":
            # Escenario de prueba de alertas: conduce normal pero el
            # refrigerante sube sin control (simula termostato pegado
            # cerrado / bomba fallando). A los ~2 min supera los 105°C
            # y la regla "coolant_overheat" del analyzer debe dispararse.
            target_coolant = 120.0
            self.coolant_c += 0.25 * dt + random.gauss(0, 0.05)
            self.coolant_c = min(target_coolant, self.coolant_c)
        else:
            target_coolant = 90.0 if self.rpm > 900 else 85.0
            warmup_rate    = 0.05 * dt if self.coolant_c < target_coolant else -0.01 * dt
            self.coolant_c = min(target_coolant + 5, self.coolant_c + warmup_rate * (target_coolant - self.coolant_c + 1))
            self.coolant_c += random.gauss(0, 0.05)

        target_intake = 25.0 + (self.speed_kmh * 0.02)
        self.intake_air_c += 0.05 * dt * (target_intake - self.intake_air_c)
        self.intake_air_c += random.gauss(0, 0.1)

        target_v = 14.2 + random.gauss(0, 0.05) if self.rpm > 900 else 12.4 + random.gauss(0, 0.02)
        self.voltage_v += 0.1 * (target_v - self.voltage_v)

        # El odómetro avanza según velocidad y tiempo transcurrido (km/h * h)
        self.odometer_km += self.speed_kmh * (dt / 3600.0)

    def _build_frames(self) -> list[dict]:
        return [
            {
                "measurement": "engine",
                "tags":   {"module": "DME"},
                "fields": {"rpm": round(self.rpm, 1)},
            },
            {
                "measurement": "temperatures",
                "tags":   {"module": "DME"},
                "fields": {"coolant_temp_c": round(self.coolant_c, 1)},
            },
            {
                "measurement": "temperatures",
                "tags":   {"module": "DME"},
                "fields": {"intake_air_temp_c": round(self.intake_air_c, 1)},
            },
            {
                "measurement": "electrical",
                "tags":   {"module": "DME"},
                "fields": {"battery_voltage_v": round(self.voltage_v, 2)},
            },
            {
                "measurement": "vehicle",
                "tags":   {"module": "DME"},
                "fields": {"odometer_km": round(self.odometer_km, 1)},
            },
        ]


def run_simulation(
    scenario: str = "city",
    duration: float = 0.0,
    interval: float = 1.0,
    stop_event: threading.Event | None = None,
    use_signal: bool = True,
):
    """
    Publica datos simulados en InfluxDB cada `interval` segundos,
    imitando el ritmo real del poller EDIABAS (mucho más lento que
    el CAN raw, porque cada métrica es un request/response individual).
    duration=0 corre hasta Ctrl+C (o hasta que se active stop_event).

    stop_event: si se pasa, se usa como señal de parada externa (p.ej.
    desde una GUI, que llama a stop_event.set() al pulsar "Detener").
    Si no se pasa, se crea uno propio controlado solo por Ctrl+C.

    use_signal=False evita registrar el manejador de Ctrl+C — necesario
    cuando esta función corre en un thread que no es el principal.
    """
    sim  = DrivingSimulator(scenario=scenario)
    stop = stop_event if stop_event is not None else threading.Event()

    if use_signal:
        import signal
        def handle_sigint(*_):
            print("\n[*] Deteniendo simulación...")
            stop.set()
        signal.signal(signal.SIGINT, handle_sigint)

    print(f"\n{'─'*55}")
    print(f"  BMW E87 Simulator (modo EDIABAS)  |  escenario: {scenario}")
    print(f"  Intervalo: {interval}s  |  {'∞ hasta Ctrl+C' if duration == 0 else f'{duration}s'}")
    print(f"{'─'*55}\n")

    deadline  = (time.time() + duration) if duration > 0 else None
    published = 0
    alerts    = 0
    t_start   = time.time()

    from core.ediabas_config import ANALYZER_RULES
    from telemetry.analyzer import MetricAnalyzer
    analyzer = MetricAnalyzer(rules=ANALYZER_RULES)

    with InfluxPublisher() as publisher:
        print("[+] Conectado a InfluxDB. Publicando...\n")
        while not stop.is_set():
            if deadline and time.time() > deadline:
                break

            loop_start = time.time()
            for frame in sim.tick(dt=interval):
                # Mismo pipeline que el poller real: crudo + derivados + alertas
                for out_frame in analyzer.process(frame):
                    publisher.publish(out_frame)
                    published += 1
                    if out_frame["measurement"] == "alerts":
                        alerts += 1
                        state = "ACTIVADA" if out_frame["fields"]["active"] else "RECUPERADA"
                        print(f"  ⚠ [{state}] {out_frame['tags']['rule']}: {out_frame['fields']['message']}")

            elapsed = time.time() - t_start
            if int(elapsed) % 10 == 0 and elapsed > 1:
                print(
                    f"  t={elapsed:>6.0f}s  "
                    f"rpm={sim.rpm:>6.0f}  "
                    f"speed={sim.speed_kmh:>5.1f} km/h  "
                    f"coolant={sim.coolant_c:>4.1f}°C  "
                    f"points={published}"
                )

            sleep_time = interval - (time.time() - loop_start)
            if sleep_time > 0:
                stop.wait(timeout=sleep_time)

    analyzer.save_state()
    elapsed = time.time() - t_start
    print(f"\n{'─'*55}")
    print(f"  Simulación finalizada")
    print(f"  Duración:          {elapsed:.1f}s")
    print(f"  Puntos publicados: {published}")
    print(f"  Alertas emitidas:  {alerts}")
    print(f"{'─'*55}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BMW E87 — Simulador de telemetría (modo EDIABAS)")
    parser.add_argument("--scenario", default="city", choices=["city", "highway", "idle", "overheat"])
    parser.add_argument("--duration", default=0.0, type=float, help="Segundos a simular (0=infinito)")
    parser.add_argument("--interval", default=1.0, type=float, help="Segundos entre lecturas (default: 1.0, como el poller real)")
    args = parser.parse_args()
    run_simulation(scenario=args.scenario, duration=args.duration, interval=args.interval)