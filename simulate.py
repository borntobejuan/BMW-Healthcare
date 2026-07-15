from __future__ import annotations

import threading
import time
import math
import random
import argparse
import signal
import sys
from dotenv import load_dotenv

"""
Simulador de telemetría BMW E87 120i.

Genera datos realistas de un coche en marcha y los publica
directamente en InfluxDB, sin necesidad de cable ni coche.

Simula un ciclo de conducción urbana:
  ralentí → aceleración → velocidad de crucero → frenada → ralentí

Uso:
    python simulate.py                    # ciclo infinito
    python simulate.py --duration 60      # 60 segundos
    python simulate.py --scenario city    # conducción urbana (default)
    python simulate.py --scenario highway # autopista
    python simulate.py --scenario idle    # solo ralentí (motor calentando)
"""



load_dotenv()

from telemetry.publisher import InfluxPublisher


# ── Escenarios de conducción ─────────────────────────────────────────────────────

class DrivingSimulator:
    """
    Simula el estado físico del coche en cada tick.
    Todos los valores evolucionan de forma continua y realista.
    """

    def __init__(self, scenario: str = "city"):
        self.scenario = scenario
        self.t        = 0.0      # tiempo acumulado en segundos

        # Estado inicial: motor arrancado, ralentí
        self.rpm          = 850.0
        self.speed_kmh    = 0.0
        self.throttle_pct = 0.0
        self.coolant_c    = 20.0   # frío al arrancar
        self.oil_c        = 18.0
        self.gear         = 0      # punto muerto
        self.voltage_v    = 14.2
        self.steering_deg = 0.0

    def tick(self, dt: float = 0.1) -> list[dict]:
        """
        Avanza la simulación `dt` segundos y devuelve
        la lista de frames decodificados listos para publicar.
        """
        self.t += dt
        self._update_state(dt)
        return self._build_frames()

    def _update_state(self, dt: float):
        """Actualiza el estado físico del coche."""

        # ── Ciclo de conducción según escenario ──────────────────────────────────
        if self.scenario == "idle":
            target_speed    = 0.0
            target_throttle = 0.0

        elif self.scenario == "highway":
            # Velocidad alta con pequeñas variaciones
            base  = 120.0 + 20.0 * math.sin(self.t / 60)
            target_speed    = max(0, base + random.gauss(0, 2))
            target_throttle = 35.0 + 10.0 * math.sin(self.t / 30)

        else:  # city — ciclo urbano: acelera, cruza, frena, para
            cycle = self.t % 60   # ciclo de 60 segundos
            if cycle < 5:         # ralentí inicial
                target_speed, target_throttle = 0.0, 0.0
            elif cycle < 20:      # aceleración a 50 km/h
                target_speed    = min(50, (cycle - 5) * 3.5)
                target_throttle = 40.0 + random.gauss(0, 3)
            elif cycle < 35:      # crucero ~50 km/h
                target_speed    = 50.0 + random.gauss(0, 1)
                target_throttle = 18.0 + random.gauss(0, 2)
            elif cycle < 45:      # frenada
                target_speed    = max(0, 50 - (cycle - 35) * 5)
                target_throttle = 0.0
            else:                 # parado en semáforo
                target_speed, target_throttle = 0.0, 0.0

        # ── Suavizado (inercia física) ────────────────────────────────────────────
        alpha_speed    = 1 - math.exp(-dt / 3.0)   # constante de tiempo 3s
        alpha_throttle = 1 - math.exp(-dt / 0.3)   # respuesta rápida 0.3s

        self.speed_kmh    += alpha_speed    * (target_speed    - self.speed_kmh)
        self.throttle_pct += alpha_throttle * (target_throttle - self.throttle_pct)
        self.throttle_pct  = max(0.0, min(100.0, self.throttle_pct))

        # ── RPM en función de velocidad + acelerador ──────────────────────────────
        if self.speed_kmh < 1:
            target_rpm = 850 + self.throttle_pct * 20 + random.gauss(0, 20)
        else:
            target_rpm = 800 + self.speed_kmh * 35 + self.throttle_pct * 30
            target_rpm += random.gauss(0, 50)
        target_rpm     = max(700, min(6800, target_rpm))
        alpha_rpm      = 1 - math.exp(-dt / 0.5)
        self.rpm      += alpha_rpm * (target_rpm - self.rpm)

        # ── Marcha estimada ───────────────────────────────────────────────────────
        if self.speed_kmh < 2:
            self.gear = 0
        elif self.speed_kmh < 20:
            self.gear = 1
        elif self.speed_kmh < 35:
            self.gear = 2
        elif self.speed_kmh < 55:
            self.gear = 3
        elif self.speed_kmh < 80:
            self.gear = 4
        elif self.speed_kmh < 110:
            self.gear = 5
        else:
            self.gear = 6

        # ── Temperatura refrigerante (sube hasta ~90°C, estabiliza) ──────────────
        target_coolant = 90.0 if self.rpm > 900 else 85.0
        warmup_rate    = 0.05 * dt if self.coolant_c < target_coolant else -0.01 * dt
        self.coolant_c = min(target_coolant + 5, self.coolant_c + warmup_rate * (target_coolant - self.coolant_c + 1))
        self.coolant_c += random.gauss(0, 0.05)

        # ── Temperatura aceite (más lenta que refrigerante) ───────────────────────
        target_oil = self.coolant_c + 15
        self.oil_c += 0.02 * dt * (target_oil - self.oil_c)
        self.oil_c += random.gauss(0, 0.05)

        # ── Voltaje batería (baja al arrancar, sube con alternador) ───────────────
        if self.rpm > 900:
            target_v = 14.2 + random.gauss(0, 0.05)
        else:
            target_v = 12.4 + random.gauss(0, 0.02)
        self.voltage_v += 0.1 * (target_v - self.voltage_v)

        # ── Ángulo de dirección (oscila suavemente en ciudad) ─────────────────────
        if self.scenario == "city":
            self.steering_deg = 15 * math.sin(self.t / 8) + random.gauss(0, 0.5)
        else:
            self.steering_deg = 3 * math.sin(self.t / 20) + random.gauss(0, 0.2)

    def _build_frames(self) -> list[dict]:
        """Construye los frames en el mismo formato que decoder.py."""
        spd = self.speed_kmh

        # Velocidades de rueda con pequeñas diferencias entre ejes
        fl = spd + random.gauss(0, 0.1)
        fr = spd + random.gauss(0, 0.1)
        rl = spd * 0.995 + random.gauss(0, 0.1)   # tracción trasera, ligera diferencia
        rr = spd * 0.995 + random.gauss(0, 0.1)

        return [
            {
                "measurement": "engine",
                "tags":   {"module": "DME"},
                "fields": {
                    "rpm":              round(self.rpm, 1),
                    "throttle_pct":     round(self.throttle_pct, 1),
                    "engine_load_pct":  round(self.throttle_pct * 0.85, 1),
                    "engine_state":     "running" if self.rpm > 700 else "off",
                },
            },
            {
                "measurement": "vehicle",
                "tags":   {"module": "KOMBI"},
                "fields": {"speed_kmh": round(spd, 1)},
            },
            {
                "measurement": "temperatures",
                "tags":   {"module": "DME"},
                "fields": {
                    "coolant_temp_c": round(self.coolant_c, 1),
                    "oil_temp_c":     round(self.oil_c, 1),
                },
            },
            {
                "measurement": "electrical",
                "tags":   {"module": "DME"},
                "fields": {
                    "battery_voltage_v": round(self.voltage_v, 2),
                    "battery_soc_pct":   95,
                },
            },
            {
                "measurement": "transmission",
                "tags":   {"module": "EGS"},
                "fields": {
                    "gear_actual":   self.gear,
                    "gear_selected": self.gear,
                },
            },
            {
                "measurement": "wheel_speeds",
                "tags":   {"module": "DSC"},
                "fields": {
                    "fl_kmh": round(max(0.0, fl), 2),
                    "fr_kmh": round(max(0.0, fr), 2),
                    "rl_kmh": round(max(0.0, rl), 2),
                    "rr_kmh": round(max(0.0, rr), 2),
                },
            },
            {
                "measurement": "steering",
                "tags":   {"module": "EPS"},
                "fields": {
                    "steering_angle_deg": round(self.steering_deg, 1),
                    "steering_speed":     0,
                },
            },
        ]


# ── Runner ───────────────────────────────────────────────────────────────────────

def run_simulation(scenario: str = "city", duration: float = 0.0, tick_hz: float = 10.0):
    """
    Publica datos simulados en InfluxDB al ritmo de tick_hz por segundo.
    duration=0 corre hasta Ctrl+C.
    """
    dt       = 1.0 / tick_hz
    sim      = DrivingSimulator(scenario=scenario)
    stop     = threading.Event()

    def handle_sigint(*_):

        print("\n[*] Deteniendo simulación...")
        stop.set()

    signal.signal(signal.SIGINT, handle_sigint)

    print(f"\n{'─'*55}")
    print(f"  BMW E87 Simulator  |  escenario: {scenario}  |  {tick_hz:.0f} Hz")
    print(f"  {'∞ hasta Ctrl+C' if duration == 0 else f'{duration}s'}")
    print(f"{'─'*55}\n")

    deadline  = (time.time() + duration) if duration > 0 else None
    published = 0
    t_start   = time.time()

    with InfluxPublisher() as publisher:
        print("[+] Conectado a InfluxDB. Publicando...\n")
        while not stop.is_set():
            if deadline and time.time() > deadline:
                break

            loop_start = time.time()
            frames     = sim.tick(dt)

            for frame in frames:
                publisher.publish(frame)
                published += 1

            # Stats cada 5 segundos
            elapsed = time.time() - t_start
            if int(elapsed) % 5 == 0 and elapsed > 1:
                print(
                    f"  t={elapsed:>6.1f}s  "
                    f"rpm={sim.rpm:>6.0f}  "
                    f"speed={sim.speed_kmh:>5.1f} km/h  "
                    f"coolant={sim.coolant_c:>4.1f}°C  "
                    f"gear={sim.gear}  "
                    f"points={published}"
                )

            # Mantener el ritmo de tick_hz — stop.wait() sale inmediatamente si se activa
            sleep_time = dt - (time.time() - loop_start)
            if sleep_time > 0:
                stop.wait(timeout=sleep_time)

    elapsed = time.time() - t_start
    print(f"\n{'─'*55}")
    print(f"  Simulación finalizada")
    print(f"  Duración:         {elapsed:.1f}s")
    print(f"  Puntos publicados:{published}")
    print(f"{'─'*55}\n")


# ── CLI ──────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BMW E87 — Simulador de telemetría")
    parser.add_argument(
        "--scenario", default="city",
        choices=["city", "highway", "idle"],
        help="Escenario de conducción (default: city)",
    )
    parser.add_argument(
        "--duration", default=0.0, type=float,
        help="Segundos a simular (0=infinito, default: 0)",
    )
    parser.add_argument(
        "--hz", default=10.0, type=float,
        help="Frecuencia de publicación en Hz (default: 10)",
    )
    args = parser.parse_args()
    run_simulation(scenario=args.scenario, duration=args.duration, tick_hz=args.hz)
