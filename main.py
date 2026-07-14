import serial
import serial.tools.list_ports

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