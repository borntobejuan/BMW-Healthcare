import serial
import serial.tools.list_ports
import can


# 1. Detectar el puerto del cable INPA K+DCAN
def find_obd_port():
    ports = serial.tools.list_ports.comports()
    for port in ports:
        print(f"{port.device} - {port.description}")
        if "FTDI" in port.description or "USB Serial" in port.description:
            return port.device
    return None

port = find_obd_port()
print(f"Puerto encontrado: {port}")

# 2. Abrir conexión básica KWP2000 / K-Line
ser = serial.Serial(
    port=port,        # En Linux: '/dev/ttyUSB0'
    baudrate=10400,     # Baudrate estándar K-Line BMW
    bytesize=8,
    parity=serial.PARITY_NONE,
    stopbits=1,
    timeout=1
)

