// EdiabasBridge — puente C# entre Python y EDIABAS, vía apiNET32.dll.
//
// Historial de esta pieza (por qué llegamos aquí):
// 1. api64.dll (EdiabasLib) requería CLR inicializado — resuelto con
//    este mismo patrón de "app .NET real como puente".
// 2. api32.dll (la DLL nativa C original de BMW) parecía funcionar con
//    P/Invoke crudo, pero SIEMPRE crasheaba en apiInit() con access
//    violation — incluso con P/Invoke real de .NET, que resuelve la
//    ABI automáticamente. Esto descartó cualquier problema de
//    convención de llamada o argumentos.
// 3. La documentación oficial de BMW (EDIABAS API Developer's Guide)
//    reveló que existe un wrapper .NET OFICIAL — apiNET32.dll — pensado
//    exactamente para ser consumido desde C#/VB.NET sin P/Invoke manual.
//    Es un assembly .NET real (no una DLL nativa C con exports), así
//    que se referencia como cualquier librería .NET normal.
//
// Uso:
//   EdiabasBridge.exe job <ECU> <JOB> [params]
//   EdiabasBridge.exe job D_MOTOR IDENTIFIKATION
//
// Salida: JSON por stdout con los sets de resultados, o {"error": "..."}

using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Text.Json;

namespace EdiabasBridge;

/// <summary>
/// Wrapper sobre el assembly apiNET32.dll cargado por reflexión.
///
/// Se carga por reflexión (Assembly.LoadFrom) en vez de añadir una
/// <Reference> directa en el .csproj porque no conocemos de antemano
/// el namespace/nombre de clase exactos que expone esa DLL en tu
/// instalación concreta de EDIABAS — distintas versiones han usado
/// nombres ligeramente distintos (API, EDIABAS.API, BMW.Rheingold.*).
/// El código inspecciona el assembly y busca los métodos por nombre.
/// </summary>
internal static class ApiNet
{
    private const string AssemblyPath = @"C:\EDIABAS\Bin\apiNET32.dll";

    private static Assembly? _assembly;
    private static Type? _apiType;
    private static object? _instance;   // null si la clase es totalmente estática

    public static void Load()
    {
        _assembly = Assembly.LoadFrom(AssemblyPath);

        // Busca la clase que expone apiInit — normalmente se llama "API"
        // o similar. Recorremos todos los tipos públicos del assembly.
        foreach (var type in _assembly.GetExportedTypes())
        {
            var initMethod = type.GetMethod("apiInit", BindingFlags.Public | BindingFlags.Static)
                           ?? type.GetMethod("apiInit", BindingFlags.Public | BindingFlags.Instance);
            if (initMethod != null)
            {
                _apiType = type;
                if (!initMethod.IsStatic)
                {
                    _instance = Activator.CreateInstance(type);
                }
                return;
            }
        }

        throw new InvalidOperationException(
            "No se encontró ninguna clase con método apiInit en apiNET32.dll. " +
            "Tipos disponibles: " + string.Join(", ", _assembly.GetExportedTypes().Select(t => t.FullName)));
    }

    private static object? Invoke(string methodName, params object?[] args)
    {
        if (_apiType == null)
            throw new InvalidOperationException("Llama a Load() primero.");

        MethodInfo? method;
        try
        {
            method = _apiType.GetMethod(methodName, BindingFlags.Public | BindingFlags.Static | BindingFlags.Instance);
        }
        catch (AmbiguousMatchException)
        {
            // Varias sobrecargas con el mismo nombre — nos quedamos con
            // la que tenga el mismo número de argumentos que se pasaron,
            // que suele ser suficiente para desambiguar en esta DLL.
            var candidates = _apiType.GetMethods(BindingFlags.Public | BindingFlags.Static | BindingFlags.Instance)
                .Where(m => m.Name == methodName && m.GetParameters().Length == args.Length)
                .ToList();
            if (candidates.Count != 1)
                throw new InvalidOperationException(
                    $"'{methodName}' tiene {candidates.Count} sobrecargas con {args.Length} argumentos — " +
                    "necesita desambiguación manual como en ApiResultText().");
            method = candidates[0];
        }

        if (method == null)
            throw new MissingMethodException($"Método '{methodName}' no encontrado en {_apiType.FullName}");

        return method.Invoke(method.IsStatic ? null : _instance, args);
    }

    public static bool ApiInit() => (bool)(Invoke("apiInit") ?? false);
    public static void ApiEnd() => Invoke("apiEnd");
    public static void ApiJob(string ecu, string job, string parameters, string resultFilter)
        => Invoke("apiJob", ecu, job, parameters, resultFilter);
    public static int ApiState() => Convert.ToInt32(Invoke("apiState"));
    public static int ApiErrorCode() => Convert.ToInt32(Invoke("apiErrorCode"));

    public static string? ApiResultText(string setName, int setIndex)
    {
        // Firma exacta descubierta a partir del error "Ambiguous match":
        //   Boolean apiResultText(String ByRef, String, UInt16, String)
        // Es decir: devuelve bool (éxito), el resultado sale por el primer
        // parámetro (ByRef/out), luego el nombre del set, el índice (UInt16),
        // y un cuarto parámetro de formato/filtro (pasamos cadena vacía).
        if (_apiType == null)
            throw new InvalidOperationException("Llama a Load() primero.");

        var method = _apiType.GetMethod(
            "apiResultText",
            BindingFlags.Public | BindingFlags.Static | BindingFlags.Instance,
            binder: null,
            types: new[] { typeof(string).MakeByRefType(), typeof(string), typeof(ushort), typeof(string) },
            modifiers: null);

        if (method == null)
            return null;

        // args[0] es el parámetro ByRef — se pasa como objeto contenedor
        // que el reflection API rellenará tras la llamada.
        object?[] args = { null, setName, (ushort)setIndex, "" };
        object? success = method.Invoke(method.IsStatic ? null : _instance, args);

        bool ok = success is bool b && b;
        return ok ? args[0] as string : null;
    }
}

internal static class Program
{
    private const int ApiBusy = 0;
    private const int ApiReady = 1;
    private const int ApiBreakState = 2;
    private const int ApiError = 3;
    private const int JobTimeoutMs = 10000;
    private const int PollIntervalMs = 50;

    // Campos candidatos a probar por cada set — apiNET32.dll no garantiza
    // tener un equivalente a apiResultName para listar dinámicamente, así
    // que probamos nombres conocidos (identificación de ECU + DTCs).
    // Añade más aquí según lo que descubras con IDENTIFIKATION/FS_LESEN.
    private static readonly string[] CandidateFields =
    {
        // Campos de identificación / info general (confirmados: ECU, COMMENT)
        "SATZANZAHL", "JOBNAME", "VARIANTE", "ECU", "COMMENT",
        "FGNR", "SGNAME",
        // Campos de DTCs (FS_LESEN)
        "F_ORT_0_FCODE", "F_ORT_0_ATEXT", "F_ORT_0_UW_KM",
        // Candidatos típicos para jobs STATUS_xxx de motor N46/MEV9 —
        // confirmar nombres reales con explore_ediabas.py + coche conectado
        "MOTORDREHZAHL", "DREHZAHL", "STAT_UMDR_MOTOR_W",
        "KUEHLMITTELTEMPERATUR", "TEMP_KUEHLMITTEL", "STAT_TEMP_MOTOR_W",
        "MOTORTEMPERATUR", "ANSAUGLUFTTEMPERATUR", "TEMP_ANSAUGLUFT",
        "UBATT", "BATTERIESPANNUNG", "STAT_UBATT_W",
        "LADEDRUCK_IST", "LADEDRUCK_SOLL",
        "KILOMETERSTAND", "STAT_KILOMETERSTAND_W",
        "OELNIVEAU", "LUFTMASSE_IST", "ATMOSPHAERENDRUCK",
    };

    private static int Main(string[] args)
    {
        try
        {
            if (args.Length < 1)
            {
                WriteError("Uso: EdiabasBridge.exe job <ECU> <JOB> [params]");
                return 1;
            }

            switch (args[0])
            {
                case "job":
                    return RunJobCommand(args);
                default:
                    WriteError($"Comando desconocido: {args[0]}");
                    return 1;
            }
        }
        catch (Exception ex)
        {
            WriteError($"Excepción no controlada: {ex.Message}");
            return 1;
        }
    }

    private static int RunJobCommand(string[] args)
    {
        if (args.Length < 3)
        {
            WriteError("Uso: EdiabasBridge.exe job <ECU> <JOB> [params]");
            return 1;
        }

        string ecu = args[1];
        string job = args[2];
        string jobParams = args.Length > 3 ? args[3] : "";

        ApiNet.Load();

        bool initOk = ApiNet.ApiInit();
        if (!initOk)
        {
            WriteError($"apiInit() falló: {GetError()}");
            return 1;
        }

        try
        {
            ApiNet.ApiJob(ecu, job, jobParams, "");
            WaitForJobDone(ecu, job);

            var sets = ReadAllSets();
            var output = new Dictionary<string, object>
            {
                ["ecu"] = ecu,
                ["job"] = job,
                ["sets"] = sets,
            };
            Console.WriteLine(JsonSerializer.Serialize(output));
            return 0;
        }
        catch (Exception ex)
        {
            WriteError(ex.Message);
            return 1;
        }
        finally
        {
            ApiNet.ApiEnd();
        }
    }

    private static void WaitForJobDone(string ecu, string job)
    {
        var deadline = DateTime.UtcNow.AddMilliseconds(JobTimeoutMs);
        while (true)
        {
            int state = ApiNet.ApiState();
            if (state == ApiReady) return;
            if (state == ApiError) throw new Exception($"Job {ecu}.{job} falló: {GetError()}");
            if (state == ApiBreakState) throw new Exception($"Job {ecu}.{job} interrumpido.");
            if (DateTime.UtcNow > deadline)
                throw new TimeoutException($"Timeout esperando job {ecu}.{job}");
            System.Threading.Thread.Sleep(PollIntervalMs);
        }
    }

    private static List<Dictionary<string, string?>> ReadAllSets()
    {
        // SATZANZAHL en el set 0 (system results) indica cuántos sets de
        // datos hay — es un resultado estándar documentado, presente en
        // cualquier job de EDIABAS, a diferencia de apiResultSets/
        // apiResultName que no están garantizados en todas las variantes.
        var sets = new List<Dictionary<string, string?>>();

        string? countStr = ApiNet.ApiResultText("SATZANZAHL", 0);
        int numSets = 1;
        if (countStr != null && int.TryParse(countStr, out var parsed))
            numSets = parsed;

        for (int setIndex = 1; setIndex <= numSets; setIndex++)
        {
            var row = ReadKnownFields(setIndex);
            if (row.Count > 0)
                sets.Add(row);
        }

        return sets;
    }

    private static Dictionary<string, string?> ReadKnownFields(int setIndex)
    {
        var row = new Dictionary<string, string?>();
        foreach (var field in CandidateFields)
        {
            var value = ApiNet.ApiResultText(field, setIndex);
            if (value != null)
                row[field] = value;
        }
        return row;
    }

    private static string GetError()
    {
        int code = ApiNet.ApiErrorCode();
        return $"código de error EDIABAS: {code}";
    }

    private static void WriteError(string message)
    {
        var output = new Dictionary<string, string> { ["error"] = message };
        Console.WriteLine(JsonSerializer.Serialize(output));
    }
}