"""
Wrapper ctypes para api32.dll (EDIABAS).

Esta es la misma DLL que usa INPA por debajo. En vez de hablar CAN
crudo por serial, le pedimos a EDIABAS que ejecute "jobs" con nombre
(definidos en los ficheros .PRG/.GRP de C:\\EDIABAS\\ECU\\) y nos
devuelve resultados ya decodificados con nombre y tipo.

Firma real de la DLL (confirmada contra el Api.h oficial):
    APIBOOL apiInit(void)
    void    apiEnd(void)
    void    apiJob(const char *ecu, const char *job, const char *params, const char *resultFilter)
    int     apiState(void)                                    // APIBUSY/APIREADY/APIBREAK/APIERROR
    APIBOOL apiResultText(char *dst, const char *setName, APIWORD setIndex)
    APIBOOL apiResultChar / apiResultByte / apiResultDWord / apiResultBinary
    int     apiErrorCode(void)
    const char *apiErrorText(void)
"""

from __future__ import annotations
import os
import time
import ctypes
from ctypes import c_char_p, c_int, c_void_p, c_char, create_string_buffer
from dataclasses import dataclass, field

from ediabas_config import (
    EDIABAS_BIN_PATH, EDIABAS_DLL_PATH,
    JOB_POLL_INTERVAL, JOB_TIMEOUT,
)


# ─── Estados de job (según Api.h) ────────────────────────────────────────────────
API_BUSY  = 0
API_READY = 1
API_BREAK = 2
API_ERROR = 3

_STATE_NAMES = {API_BUSY: "BUSY", API_READY: "READY", API_BREAK: "BREAK", API_ERROR: "ERROR"}

# Tamaño de buffer para textos de resultado (suficiente para la mayoría de campos)
RESULT_TEXT_BUFFER_SIZE = 1024


class EdiabasError(Exception):
    """Error devuelto por la propia API de EDIABAS (apiErrorCode/apiErrorText)."""
    pass


@dataclass
class JobResult:
    """Resultado de un job EDIABAS: lista de sets (filas), cada uno con sus campos."""
    ecu:  str
    job:  str
    sets: list[dict] = field(default_factory=list)

    def first(self) -> dict:
        """Devuelve el primer set de resultados, o dict vacío si no hay ninguno."""
        return self.sets[0] if self.sets else {}


class EdiabasClient:
    """
    Cliente de bajo nivel para api32.dll.

    Uso:
        with EdiabasClient() as ed:
            result = ed.run_job("D_MOTOR", "STATUS_MOTOR")
            print(result.first())
    """

    def __init__(self, dll_path: str = EDIABAS_DLL_PATH, bin_path: str = EDIABAS_BIN_PATH):
        self.dll_path = dll_path
        self.bin_path = bin_path
        self._dll: ctypes.WinDLL | None = None
        self._fn: dict = {}          # nombre lógico → función ctypes ya tipada
        self._initialized = False

    # ── Ciclo de vida ────────────────────────────────────────────────────────────
    def connect(self):
        """Carga la DLL y llama a apiInit()."""
        if not os.path.exists(self.dll_path):
            raise FileNotFoundError(
                f"No se encuentra api64.dll en {self.dll_path}. "
                f"Verifica que EDIABAS está instalado."
            )

        # EDIABAS necesita que su bin/ esté en el PATH para encontrar DLLs auxiliares
        if self.bin_path not in os.environ["PATH"]:
            os.environ["PATH"] = self.bin_path + os.pathsep + os.environ["PATH"]

        self._dll = ctypes.WinDLL(self.dll_path)
        self._declare_signatures()

        ok = self._fn["init"](None)
        if not ok:
            raise EdiabasError(f"apiInit() falló: {self._get_error()}")

        self._initialized = True
        print(f"[+] EDIABAS inicializado ({self.dll_path})")

    def disconnect(self):
        """Llama a apiEnd() y libera la DLL."""
        if self._initialized and self._dll:
            self._fn["end"](None)
            self._initialized = False
            print("[*] EDIABAS finalizado.")
        self._dll = None

    def _declare_signatures(self):
        """
        Enlaza y tipa cada función exportada por la DLL.

        Esta es api32.dll (la original de BMW, la misma que usa INPA).
        list_dll_exports.py mostró CADA función exportada dos veces:
          - "___apiInit@4"  → decoración stdcall completa (@N = bytes de pila)
          - "__apiInit"     → sin decoración

        La variante "__apiInit" causaba access violation con dirección
        cambiante (0xFFFFFFFD, etc.) — patrón típico de desalineación de
        pila. Usamos la variante decorada, que fija sin ambigüedad la
        convención stdcall real vía el sufijo @N.

        IMPORTANTE — verificado matemáticamente dividiendo @N entre 4:
        CADA función tiene un argumento MÁS de los que documenta el
        Api.h oficial (ej. apiInit@4 = 1 arg, pese a que la doc dice
        "void apiInit(void)"). Es consistente con un handle/puntero de
        instancia oculto que antecede a los argumentos documentados.
        Se le pasa siempre None/0 (c_void_p) al llamar.
        """
        dll = self._dll

        def bind(export_name: str, restype, argtypes):
            fn = getattr(dll, export_name)
            fn.restype  = restype
            fn.argtypes = argtypes
            return fn

        self._fn["init"]        = bind("___apiInit@4",        c_int,  [c_void_p])
        self._fn["end"]         = bind("___apiEnd@4",          None,   [c_void_p])
        self._fn["job"]         = bind("___apiJob@20",         None,   [c_void_p, c_char_p, c_char_p, c_char_p, c_char_p])
        self._fn["state"]       = bind("___apiState@4",        c_int,  [c_void_p])
        self._fn["result_text"] = bind("___apiResultText@20",  c_int,  [c_void_p, c_char_p, c_char_p, c_int])
        self._fn["result_sets"] = bind("___apiResultSets@8",   c_int,  [c_void_p, c_void_p])
        self._fn["result_name"] = bind("___apiResultName@16",  c_int,  [c_void_p, c_char_p, c_int])
        self._fn["job_info"]    = bind("___apiJobInfo@8",      c_int,  [c_void_p, c_char_p])
        self._fn["error_code"]  = bind("___apiErrorCode@4",    c_int,  [c_void_p])
        self._fn["error_text"]  = bind("___apiErrorText@12",   c_char_p, [c_void_p, c_void_p])

    def _get_error(self) -> str:
        if not self._dll:
            return "DLL no cargada"
        code = self._fn["error_code"](None)
        text = self._fn["error_text"](None)
        text_str = text.decode("latin-1", errors="replace") if text else ""
        return f"[{code}] {text_str}"

    # ── Ejecución de jobs ────────────────────────────────────────────────────────
    def run_job(self, ecu: str, job: str, params: str = "", result_filter: str = "") -> JobResult:
        """
        Ejecuta un job EDIABAS y espera su finalización.

        Args:
            ecu:           nombre del fichero .PRG sin extensión (ej. "D_MOTOR")
            job:           nombre del job (ej. "STATUS_MOTOR", "FS_LESEN")
            params:        parámetros del job separados por ';' (mayoría no necesitan)
            result_filter: filtro de campos a devolver (vacío = todos)

        Returns:
            JobResult con los sets de resultados.
        """
        if not self._initialized:
            raise RuntimeError("Llama a connect() antes de ejecutar jobs.")

        self._fn["job"](
            None,
            ecu.encode("latin-1"),
            job.encode("latin-1"),
            params.encode("latin-1"),
            result_filter.encode("latin-1"),
        )

        self._wait_job_done(ecu, job)
        return self._read_results(ecu, job)

    def _wait_job_done(self, ecu: str, job: str):
        """Polling de apiState() hasta que el job termine."""
        deadline = time.time() + JOB_TIMEOUT
        while True:
            state = self._fn["state"](None)
            if state == API_READY:
                return
            if state == API_ERROR:
                raise EdiabasError(f"Job {ecu}.{job} falló: {self._get_error()}")
            if state == API_BREAK:
                raise EdiabasError(f"Job {ecu}.{job} interrumpido.")
            if time.time() > deadline:
                raise TimeoutError(f"Timeout esperando job {ecu}.{job} ({JOB_TIMEOUT}s)")
            time.sleep(JOB_POLL_INTERVAL)

    def _read_results(self, ecu: str, job: str) -> JobResult:
        """
        Lee todos los resultados del último job vía introspección real:
        __apiResultSets() da el número de sets, __apiResultName() permite
        descubrir los nombres de campo reales de cada uno.
        """
        result = JobResult(ecu=ecu, job=job)

        num_sets = self._fn["result_sets"](None)
        for set_index in range(1, num_sets + 1):
            row = self._read_result_set(set_index)
            if row:
                result.sets.append(row)

        return result

    def _read_result_set(self, set_index: int) -> dict:
        """
        Lee un set de resultados descubriendo sus campos reales
        vía __apiResultName(idx) — itera desde 1 hasta que devuelve vacío.
        """
        row = {}
        name_buffer = create_string_buffer(RESULT_TEXT_BUFFER_SIZE)

        field_idx = 1
        while True:
            ok = self._fn["result_name"](None, name_buffer, field_idx)
            if not ok:
                break
            field_name = name_buffer.value.decode("latin-1", errors="replace").strip()
            if not field_name:
                break

            value = self.raw_result_text(field_name, set_index)
            row[field_name] = value
            field_idx += 1

            if field_idx > 200:  # salvaguarda anti bucle infinito
                break

        return row

    def raw_result_text(self, field_name: str, set_index: int = 0) -> str | None:
        """
        Lee un campo de resultado específico por nombre.
        set_index=0 es el set "global" (job info), 1+ son sets de datos.
        """
        if not self._initialized:
            raise RuntimeError("Llama a connect() antes de leer resultados.")
        buffer = create_string_buffer(RESULT_TEXT_BUFFER_SIZE)
        ok = self._fn["result_text"](None, buffer, field_name.encode("latin-1"), set_index)
        if not ok:
            return None
        return buffer.value.decode("latin-1", errors="replace").strip()

    def job_info(self) -> str:
        """Devuelve info textual del último job ejecutado (útil para depurar campos disponibles)."""
        if not self._initialized:
            raise RuntimeError("Llama a connect() antes de pedir info.")
        buffer = create_string_buffer(4096)
        self._fn["job_info"](None, buffer)
        return buffer.value.decode("latin-1", errors="replace")

    # ── Context manager ──────────────────────────────────────────────────────────
    def __enter__(self) -> "EdiabasClient":
        self.connect()
        return self

    def __exit__(self, *_):
        self.disconnect()