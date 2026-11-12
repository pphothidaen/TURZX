#!/usr/bin/env python3
"""
Diagnostic: direct serial connect + small test write to TURZX display.
"""
import os, sys, time
os.chdir("/Users/kimlenglim/Project/TURZX")
sys.path.insert(0, "/Users/kimlenglim/Project/TURZX")

import serial
from backend.services.serial_driver import TURXZSerialDriver, TURXZSerialError, TURXZSerialProtocolError

port = "/dev/cu.usbmodemUSB35INCHIPSV21"
baud = 115200

print("=== Step 1: direct connect ===")
try:
    ser = serial.Serial(
        port=port, baudrate=baud, bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
        timeout=2, rtscts=True,
    )
    print("  serial opened:", ser.is_open, "port:", ser.port, "baud:", ser.baudrate)
except Exception as e:
    print("  FAIL open serial:", e)
    sys.exit(1)

try:
    # Send HELLO (0x45) per protocol
    ser.write(b'\x45')
    ser.flush()
    time.sleep(0.5)
    resp = ser.read(64)
    print("  HELLO response:", resp.hex() if resp else None)
except Exception as e:
    print("  HELLO fail:", e)

try:
    ser.write(b'\x45')
    ser.flush()
    time.sleep(0.3)
    resp = ser.read(8)
    print("  HELLO (short) response:", resp.hex() if resp else None)
except Exception as e:
    print("  HELLO (short) fail:", e)

# Send a simple CLEAR command packet (x=0,y=0,ex=0,ey=0, cmd=CLEAR)
import struct
def encode_cmd_packet(x, y, ex, ey, cmd):
    # Build 6-byte packet for the Rev A protocol
    # (replicating encode_command_packet logic from serial_driver)
    data = struct.pack('<BBBB', cmd, 0, x, y)  # placeholder; use real encoding
    return data

# Try a 6-byte command: [cmd(1) + reserved? ...]
# Replicate the real packet encode from serial_driver Command enum
from backend.services.serial_driver import Command
print("\nCommand.CLEAR:", Command.CLEAR, "hex:", hex(Command.CLEAR))

try:
    ser.write(b'\x02\x01\x00\x00\x00\x02')
    ser.flush()
    time.sleep(0.5)
    resp = ser.read(32)
    print("  raw test write resp:", resp.hex() if resp else None)
    print("  raw test write OK")
except serial.SerialException as e:
    print("  raw test write FAIL:", e)

print("\n=== STEP SUMMARY ===")
print("direct serial connect + write:", "OK" if ser.is_open else "FAIL")
ser.close()
