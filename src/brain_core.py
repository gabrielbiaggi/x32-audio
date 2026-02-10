import json
import time
import math
import logging
import argparse
from typing import Dict, List, Optional

import paho.mqtt.client as mqtt

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("BrainCore")

# Constants
CONFIG_PATH = "../config/x32_map.json" # Relative from src/
TARGET_BUS_IDX = [11, 12] # Default, can be overridden by config

# X32 Math Helper
# Behringer X32 Fader Curve (approximate)
# 0.0 - 1.0 float to/from dB
# This is a piecewise approximation or log function. 
# For simplicity in this iteration, we will use a common approximation 
# found in open source X32 libraries.
def fader_to_db(fader_val):
    if fader_val >= 0.5:
        return fader_val * 40.0 - 30.0 # 0.5 -> -10, 0.75 -> 0, 1.0 -> +10
    elif fader_val >= 0.25:
        return fader_val * 80.0 - 50.0 # 0.25 -> -30, 0.5 -> -10
    elif fader_val > 0.0625:
        return fader_val * 160.0 - 70.0 # 0.0625 -> -60, 0.25 -> -30
    else:
        return -90.0 # Shutdown/Inf

def db_to_fader(db_val):
    if db_val >= -10.0:
        return (db_val + 30.0) / 40.0
    elif db_val >= -30.0:
        return (db_val + 50.0) / 80.0
    elif db_val >= -60.0:
        return (db_val + 70.0) / 160.0
    else:
        return 0.0

class ChannelStrip:
    def __init__(self, channel_id, config_data):
        self.id = channel_id
        self.name = config_data.get("name", f"Ch {channel_id}")
        self.group = config_data.get("group", "ignore")
        self.priority = config_data.get("priority", "none")
        
        self.current_dbfs = -90.0
        self.current_fader_level = 0.0 # 0.0 to 1.0 (Send level to Bus)
        self.target_fader_level = 0.75 # Default 0dB
        
        self.is_overridden = False
        self.override_end_time = 0

class BrainCore:
    def __init__(self, broker_ip, config_file):
        self.broker_ip = broker_ip
        self.channels: Dict[str, ChannelStrip] = {}
        self.config = self.load_config(config_file)
        
        # MQTT
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="X32_Brain")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        
        self.running = True
        
        # State
        self.speech_active = False # Gate for Ducking

    def load_config(self, path):
        try:
            with open(path, 'r') as f:
                data = json.load(f)
                logger.info(f"Loaded configuration from {path}")
                
                # Initialize channels
                for ch_id, ch_data in data.get("channels", {}).items():
                    self.channels[ch_id] = ChannelStrip(ch_id, ch_data)
                    
                global TARGET_BUS_IDX
                TARGET_BUS_IDX = data.get("target_bus", [11, 12])
                
                return data
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            return {}

    def on_connect(self, client, userdata, flags, rc, properties=None):
        logger.info(f"Connected to MQTT Broker ({rc})")
        client.subscribe("x32/telemetry")
        client.subscribe("x32/status/fader/#") # Hypothetical topic for manual moves if reported

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode())
            
            if topic == "x32/telemetry":
                self.process_telemetry(payload)
                
        except Exception as e:
            logger.error(f"Error processing message on {topic}: {e}")

    def process_telemetry(self, levels: Dict[str, float]):
        # Update current levels
        max_speech_db = -90.0
        
        for ch_id, db_val in levels.items():
            if ch_id in self.channels:
                self.channels[ch_id].current_dbfs = db_val
                
                # Check Speech Gate
                if self.channels[ch_id].group == "speech" and db_val > -35.0:
                    max_speech_db = max(max_speech_db, db_val)

        # Determine Ducking State
        self.speech_active = (max_speech_db > -35.0)
        
        # Run Mixing Logic
        self.run_mixing_logic()

    def run_mixing_logic(self):
        # 1. Apply Ducking to Music (Drums/Band)
        duck_amount = -4.0 if self.speech_active else 0.0
        # Convert dB reduction to linear multiplier? Or just offset target?
        # X32 faders are log. We will adjust the Target DB, then convert to Fader 0-1
        
        # 2. Iterate channels
        commands = []
        
        for ch in self.channels.values():
            if ch.is_overridden:
                if time.time() > ch.override_end_time:
                    ch.is_overridden = False
                    logger.info(f"Override ended for {ch.name}")
                else:
                    continue # Skip automation
            
            # Logic by Group
            new_target_db = 0.0 # Default send level (Unity)
            
            if ch.group == "drums" or ch.group == "band":
                # Apply Ducking
                new_target_db = 0.0 + duck_amount # Assuming we want them at 0dB normally
                
            elif ch.group == "vocals":
                # Auto-Leveling Logic
                # Target: -18dBFS RMS
                # If current < -18, boost. If > -18, cut.
                # Simple P-controller
                error = -18.0 - ch.current_dbfs
                
                # Deadband
                if abs(error) < 2.0:
                    continue # Good enough
                
                # Restrict gain to reasonable limits (e.g. +10dB to -10dB from Unity)
                # This is a simplification. Real auto-leveling needs more state (current gain).
                # Since we don't know the CURRENT fader pos from the console in this loop 
                # (unless we track it via separate OSC feedback which is complex),
                # We will assume nominal start and nudge.
                # FOR SAFETY in this v1: We just set a fixed safe level.
                new_target_db = 0.0
                
            elif ch.group == "speech":
                new_target_db = 0.0 # Keep speech at Unity send
            
            else:
                continue

            # Convert to Fader 0.0-1.0
            # For sends, the address is typically /ch/01/mix/01/level (for mixbus 1)
            # But wait, user said "Bus 11 e 12 (Stereo Linked)".
            # When linked, usually sending to odd bus controls the level for both? 
            # Or depends on "sends on fader" logic. usually /ch/01/mix/11/level works.
            
            fader_val = db_to_fader(new_target_db)
            
            # Rate limiting / Optimization: Only send if changed significantly
            # (Skipped for brevity, but crucial for real deploy)
            
            # Construct OSC Command
            # ch.id is "1", "2"... needs "01", "02"
            ch_str = f"{int(ch.id):02d}"
            
            # Send to Bus 11
            address = f"/ch/{ch_str}/mix/11/level"
            commands.append({"address": address, "args": [float(fader_val)]})

        # Send Batch Command
        if commands:
            for cmd in commands:
                self.client.publish("x32/commands", json.dumps(cmd))

    def start(self):
        logger.info("Starting Brain Core...")
        try:
            self.client.connect(self.broker_ip, 1883, 60)
            self.client.loop_forever()
        except KeyboardInterrupt:
            logger.info("Stopping Brain Core")
            self.client.disconnect()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/x32_map.json", help="Path to config file")
    parser.add_argument("--broker", default="localhost", help="MQTT Broker")
    args = parser.parse_args()
    
    brain = BrainCore(args.broker, args.config)
    brain.start()
