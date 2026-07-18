"""
GUI de escritorio para BMW E87 120i — EDIABAS Diagnostic Tool.

Dos pestañas:
  - Simulador: lanza/para simulate.py sin coche, para probar el dashboard.
  - EDIABAS (real): dtc/info bajo demanda + live (polling continuo).

Toda operación bloqueante corre en threads separados para no congelar
la ventana. La salida de consola de cada acción se redirige a un
cuadro de texto en la propia pestaña.

Uso:
    python gui.py
"""

from __future__ import annotations
import sys
import io
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext
from dotenv import load_dotenv

load_dotenv()

from core.ediabas_config import ECU_MODULES, JOB_IDENT, JOB_READ_DTC, DTC_RESULT_FIELDS
from core.ediabas_bridge_client import EdiabasBridgeClient, EdiabasBridgeError


# ── Utilidad: redirige stdout de un bloque de código a un widget Text ──────────────
class _TextRedirect(io.TextIOBase):
    """Redirige escrituras de stdout a un widget Text de tkinter, thread-safe."""

    def __init__(self, widget: scrolledtext.ScrolledText):
        self.widget = widget

    def write(self, s: str) -> int:
        # after(0, ...) asegura que la modificación del widget ocurre
        # en el hilo de la GUI, aunque write() se llame desde otro thread.
        self.widget.after(0, self._append, s)
        return len(s)

    def _append(self, s: str):
        self.widget.configure(state="normal")
        self.widget.insert(tk.END, s)
        self.widget.see(tk.END)
        self.widget.configure(state="disabled")

    def flush(self):
        pass


class BaseTab(ttk.Frame):
    """Funcionalidad común: consola de salida + helper para lanzar en thread."""

    def __init__(self, parent):
        super().__init__(parent, padding=12)
        self._worker: threading.Thread | None = None

    def _build_console(self, parent, height: int = 16) -> scrolledtext.ScrolledText:
        console = scrolledtext.ScrolledText(
            parent, height=height, state="disabled",
            bg="#111", fg="#0f0", font=("Consolas", 9),
        )
        return console

    def _run_in_thread(self, target, console: scrolledtext.ScrolledText, on_done=None):
        """
        Ejecuta `target` (sin argumentos) en un thread, redirigiendo
        stdout de ese bloque al widget `console`.
        """
        if self._worker and self._worker.is_alive():
            return  # ya hay algo corriendo

        def runner():
            redirect = _TextRedirect(console)
            old_stdout = sys.stdout
            sys.stdout = redirect
            try:
                target()
            except Exception as e:
                print(f"\n[!] Error inesperado: {e}\n")
            finally:
                sys.stdout = old_stdout
                if on_done:
                    console.after(0, on_done)

        self._worker = threading.Thread(target=runner, daemon=True)
        self._worker.start()

    def _clear_console(self, console: scrolledtext.ScrolledText):
        console.configure(state="normal")
        console.delete("1.0", tk.END)
        console.configure(state="disabled")


# ── Pestaña: Simulador ──────────────────────────────────────────────────────────────
class SimulatorTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self._stop_event = threading.Event()
        self._build_ui()

    def _build_ui(self):
        row = ttk.Frame(self)
        row.pack(fill="x", pady=(0, 10))

        ttk.Label(row, text="Escenario:").pack(side="left")
        self.scenario_var = tk.StringVar(value="city")
        ttk.Combobox(
            row, textvariable=self.scenario_var,
            values=["city", "highway", "idle"], width=12, state="readonly",
        ).pack(side="left", padx=(6, 20))

        ttk.Label(row, text="Intervalo (s):").pack(side="left")
        self.interval_var = tk.StringVar(value="1.0")
        ttk.Entry(row, textvariable=self.interval_var, width=6).pack(side="left", padx=(6, 20))

        self.start_btn = ttk.Button(row, text="▶ Iniciar simulación", command=self._start)
        self.start_btn.pack(side="left", padx=(0, 8))

        self.stop_btn = ttk.Button(row, text="■ Detener", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left")

        ttk.Label(
            self,
            text="Publica datos simulados en InfluxDB con el mismo formato que el poller real.\n"
                 "Ábrelo junto a Grafana (http://localhost:3000) para ver el dashboard en vivo.",
            foreground="#555",
        ).pack(anchor="w", pady=(0, 8))

        self.console = self._build_console(self)
        self.console.pack(fill="both", expand=True)

    def _start(self):
        try:
            interval = float(self.interval_var.get())
        except ValueError:
            interval = 1.0

        self._stop_event.clear()
        self._clear_console(self.console)
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")

        from simulate import run_simulation

        def target():
            run_simulation(
                scenario=self.scenario_var.get(),
                duration=0.0,
                interval=interval,
                stop_event=self._stop_event,
                use_signal=False,   # estamos en un thread, no en el principal
            )

        def on_done():
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")

        self._run_in_thread(target, self.console, on_done=on_done)

    def _stop(self):
        self._stop_event.set()
        self.stop_btn.config(state="disabled")


# ── Pestaña: EDIABAS real ───────────────────────────────────────────────────────────
class EdiabasTab(BaseTab):
    def __init__(self, parent):
        super().__init__(parent)
        self._live_poller = None
        self._build_ui()

    def _build_ui(self):
        modules = list(ECU_MODULES.keys())

        # ── Fila de acciones puntuales (dtc / info) ──────────────────────────────
        row1 = ttk.LabelFrame(self, text="Consulta puntual", padding=10)
        row1.pack(fill="x", pady=(0, 10))

        ttk.Label(row1, text="Módulo:").grid(row=0, column=0, sticky="w")
        self.module_var = tk.StringVar(value="DME")
        ttk.Combobox(
            row1, textvariable=self.module_var, values=modules,
            width=10, state="readonly",
        ).grid(row=0, column=1, padx=(6, 20))

        ttk.Button(row1, text="Info / Identificación", command=self._run_info).grid(row=0, column=2, padx=4)
        ttk.Button(row1, text="Leer DTCs", command=self._run_dtc).grid(row=0, column=3, padx=4)

        # ── Fila de live ──────────────────────────────────────────────────────────
        row2 = ttk.LabelFrame(self, text="Telemetría en vivo → InfluxDB → Grafana", padding=10)
        row2.pack(fill="x", pady=(0, 10))

        ttk.Label(row2, text="Intervalo (s):").grid(row=0, column=0, sticky="w")
        self.live_interval_var = tk.StringVar(value="1.0")
        ttk.Entry(row2, textvariable=self.live_interval_var, width=6).grid(row=0, column=1, padx=(6, 20))

        self.live_start_btn = ttk.Button(row2, text="▶ Iniciar live", command=self._start_live)
        self.live_start_btn.grid(row=0, column=2, padx=4)

        self.live_stop_btn = ttk.Button(row2, text="■ Detener", command=self._stop_live, state="disabled")
        self.live_stop_btn.grid(row=0, column=3, padx=4)

        ttk.Label(
            self,
            text="Requiere el cable K+DCAN conectado al coche con contacto puesto.\n"
                 "Compila antes el puente: cd ediabas_bridge && dotnet publish -c Release -r win-x86 --self-contained true",
            foreground="#555",
        ).pack(anchor="w", pady=(0, 8))

        self.console = self._build_console(self)
        self.console.pack(fill="both", expand=True)

    # ── Consultas puntuales ──────────────────────────────────────────────────────────
    def _resolve_ecu(self, module: str) -> str:
        return ECU_MODULES.get(module.upper(), module)

    def _run_info(self):
        self._clear_console(self.console)
        module = self.module_var.get()

        def target():
            ecu = self._resolve_ecu(module)
            print(f"[*] Leyendo info de: {module} ({ecu})\n")
            client = EdiabasBridgeClient()
            try:
                result = client.run_job(ecu, JOB_IDENT)
            except EdiabasBridgeError as e:
                print(f"[!] Error: {e}")
                return

            if not result.sets:
                print("[!] Sin resultados con los campos conocidos.")
                return

            for i, row in enumerate(result.sets, start=1):
                print(f"── Set {i} ──")
                for field, value in row.items():
                    print(f"  {field:<25} = {value}")

        self._run_in_thread(target, self.console)

    def _run_dtc(self):
        self._clear_console(self.console)
        module = self.module_var.get()

        def target():
            ecu = self._resolve_ecu(module)
            print(f"[*] Leyendo DTCs de: {module} ({ecu})\n")
            client = EdiabasBridgeClient()
            try:
                result = client.run_job(ecu, JOB_READ_DTC)
            except EdiabasBridgeError as e:
                print(f"[!] Error: {e}")
                return

            if not result.sets:
                print(f"[✓] Sin fallos activos, o campos aún no confirmados.")
                print(f"    Campos esperados: {DTC_RESULT_FIELDS}")
                return

            for i, row in enumerate(result.sets, start=1):
                print(f"{i}: {row}")

        self._run_in_thread(target, self.console)

    # ── Live ─────────────────────────────────────────────────────────────────────────
    def _start_live(self):
        try:
            interval = float(self.live_interval_var.get())
        except ValueError:
            interval = 1.0

        self._clear_console(self.console)
        self.live_start_btn.config(state="disabled")
        self.live_stop_btn.config(state="normal")

        from telemetry.poller import EdiabasPoller
        self._live_poller = EdiabasPoller(interval=interval)

        def target():
            self._live_poller.run(duration=0.0, use_signal=False)

        def on_done():
            self.live_start_btn.config(state="normal")
            self.live_stop_btn.config(state="disabled")

        self._run_in_thread(target, self.console, on_done=on_done)

    def _stop_live(self):
        if self._live_poller:
            self._live_poller.stop()
        self.live_stop_btn.config(state="disabled")


# ── Ventana principal ────────────────────────────────────────────────────────────────
def main():
    root = tk.Tk()
    root.title("BMW E87 120i — Diagnostic Tool")
    root.geometry("780x560")

    style = ttk.Style()
    if "clam" in style.theme_names():
        style.theme_use("clam")

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=8, pady=8)

    sim_tab = SimulatorTab(notebook)
    ediabas_tab = EdiabasTab(notebook)

    notebook.add(sim_tab, text="  Simulador  ")
    notebook.add(ediabas_tab, text="  EDIABAS (real)  ")

    root.mainloop()


if __name__ == "__main__":
    main()