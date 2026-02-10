import cls
import json
import time
import math
import threading
import struct
import socket
import argparse
import logging
from typing import Dict, List, Optional

import pyaudio
import numpy as np
import paho.mqtt.client as mqtt
from pythonosc.udp_client import SimpleUDPClient

# Configuration
# TODO: Move to a config file or env vars if needed
MQTT_BROKER = "localhost" # Or IP of the Brain/Mosquitto
MQTT_PORT = 1883
X32_IP = "192.168.1.10" # Replace with actual X32 IP
X32_PORT = 10023
SAMPLE_RATE = 48000
CHUNK_SIZE = 1024 # Buffer size
CHANNELS = 32
TELEMETRY_INTERVAL = 0.2 # 200ms

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("EdgeNode")

class X32EdgeNode:
    def __init__(self, mqtt_broker, mqtt_port, x32_ip, x32_port):
        self.mqtt_broker = mqtt_broker
        self.mqtt_port = mqtt_port
        self.x32_ip = x32_ip
        self.x32_port = x32_port
        
        # Audio
        self.p = pyaudio.PyAudio()
        self.stream = None
        self.running = False
        
        # MQTT
        self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="X32_Edge_Probe")
        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message
        
        # OSC
        self.osc_client = SimpleUDPClient(x32_ip, x32_port)
        
        # State
        self.last_telemetry_time = 0
        self.levels = [0.0] * CHANNELS

    def on_mqtt_connect(self, client, userdata, flags, rc, properties=None):
        logger.info(f"Connected to MQTT Broker with code {rc}")
        client.subscribe("x32/commands")

    def on_mqtt_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            address = payload.get("address")
            args = payload.get("args")
            
            if address:
                logger.debug(f"Forwarding OSC: {address} {args}")
                self.osc_client.send_message(address, args)
        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")

    def audio_callback(self, in_data, frame_count, time_info, status):
        # Convert byte data to numpy array
        # Int32 for 24-bit audio usually padded or similar, X-USB is often 32 channel int32 or float32 depending on driver
        # Asuming Int32 for now, might need adjustment based on specific driver/OS
        audio_data = np.frombuffer(in_data, dtype=np.int32)
        
        # Audio data is interleaved: [ch1, ch2, ..., ch32, ch1, ch2...]
        # Reshape to (frames, channels)
        try:
            audio_data = audio_data.reshape((frame_count, CHANNELS))
        except ValueError:
            logger.error(f"Audio buffer size mismatch. Got {len(audio_data)}, expected {frame_count * CHANNELS}")
            return (None, pyaudio.paContinue)

        # Calculate RMS for each channel
        # 2^31 is max value for int32
        # Normalize to -1.0 to 1.0
        normalized_data = audio_data / 2147483648.0 
        
        # Calculate RMS: sqrt(mean(x^2))
        # axis=0 calculates mean across frames for each channel
        rms_values = np.sqrt(np.mean(normalized_data**2, axis=0))
        
        # Convert to dBFS
        # Add epsilon to avoid log(0)
        db_values = 20 * np.log10(rms_values + 1e-9)
        
        # Store for telemetry loop
        self.levels = db_values.tolist()
        
        return (None, pyaudio.paContinue)

    def telemetry_loop(self):
        while self.running:
            current_time = time.time()
            if current_time - self.last_telemetry_time >= TELEMETRY_INTERVAL:
                # Create map "1": level, "2": level...
                telemetry_data = {str(i+1): lvl for i, lvl in enumerate(self.levels)}
                
                try:
                    self.mqtt_client.publish("x32/telemetry", json.dumps(telemetry_data))
                    self.last_telemetry_time = current_time
                except Exception as e:
                    logger.error(f"Failed to publish telemetry: {e}")
            
            time.sleep(0.05) # Sleep briefly to yield CPU

    def xremote_loop(self):
        """Periodically send /xremote to receive OSC updates from console (fader moves)"""
        while self.running:
            try:
                self.osc_client.send_message("/xremote", [])
                time.sleep(9) # X32 subscription expires after 10s
            except Exception as e:
                logger.error(f"Failed to send /xremote: {e}")

    def start(self):
        self.running = True
        
        # Connect MQTT
        try:
            self.mqtt_client.connect(self.mqtt_broker, self.mqtt_port, 60)
            self.mqtt_client.loop_start()
        except Exception as e:
            logger.error(f"Could not connect to MQTT: {e}")
            return

        # Start Audio Stream
        try:
            # TODO: Add logic to find specific device index by name "X-USB"
            # For now, using default
            self.stream = self.p.open(format=pyaudio.paInt32,
                                      channels=CHANNELS,
                                      rate=SAMPLE_RATE,
                                      input=True,
                                      frames_per_buffer=CHUNK_SIZE,
                                      stream_callback=self.audio_callback)
            self.stream.start_stream()
            logger.info("Audio stream started")
        except Exception as e:
            logger.error(f"Could not start audio stream: {e}")
            self.stop()
            return
            
        # Start Threads
        self.telemetry_thread = threading.Thread(target=self.telemetry_loop)
        self.telemetry_thread.start()
        
        self.xremote_thread = threading.Thread(target=self.xremote_loop)
        self.xremote_thread.start()
        
        logger.info("Edge Node Running. Press Ctrl+C to stop.")
        
        try:
            while self.stream.is_active():
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        self.running = False
        logger.info("Stopping...")
        
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
        
        self.p.terminate()
        self.mqtt_client.loop_stop()
        self.mqtt_client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="X32 Edge Node")
    parser.add_argument("--broker", default="localhost", help="MQTT Broker IP")
    parser.add_argument("--x32", default="192.168.1.10", help="X32 Console IP")
    args = parser.parse_args()
    
    node = X32EdgeNode(args.broker, 1883, args.x32, 10023)
    node.start()
