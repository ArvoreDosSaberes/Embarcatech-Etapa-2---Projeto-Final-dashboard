#!/usr/bin/env python3

"""
Test MQTT Connection
Description: Simple script to test MQTT broker connectivity
Author: Carlos Delfino <consultoria@carlosdelfino.eti.br>
"""

import os
import sys
import json
import time
from dotenv import load_dotenv
import paho.mqtt.client as mqtt

WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(WORKSPACE_ROOT, ".env"))

def on_connect(client, userdata, flags, rc, properties=None):
    """Callback when connected to MQTT broker"""
    if rc == 0:
        print(f"✅ [MQTT/Connection] Successfully connected to broker")
        base_topic = os.getenv("MQTT_BASE_TOPIC", "racks").rstrip("/")
        client.subscribe(f"{base_topic}/#")
        print(f"📡 [MQTT/Subscription] Subscribed to topic: {base_topic}/#")
    else:
        print(f"❌ [MQTT/Connection] Failed to connect, return code: {rc}")

def on_message(client, userdata, msg):
    """Callback when message received"""
    try:
        raw_payload = msg.payload.decode()
    except Exception as e:
        print(f"❌ [MQTT/Error] Error decoding payload: {e}")
        return

    # Primeiro tenta interpretar como JSON; se falhar, exibe como texto simples
    try:
        data = json.loads(raw_payload)
        print(f"\n📨 [MQTT/Message] Received message on topic: {msg.topic}")
        print(f"   JSON: {json.dumps(data, indent=2)}")
    except json.JSONDecodeError:
        print(f"\n📨 [MQTT/Message] Received message on topic: {msg.topic}")
        print(f"   Text payload: {raw_payload}")

def on_disconnect(client, userdata, rc):
    """Callback when disconnected"""
    if rc != 0:
        print(f"⚠️  [MQTT/Disconnect] Unexpected disconnection, code: {rc}")
    else:
        print(f"🔌 [MQTT/Disconnect] Disconnected from broker")

def main():
    """Main test function"""
    print("=" * 60)
    print("🧪 MQTT Connection Test - Rack Inteligente Dashboard")
    print("=" * 60)
    print()
    
    # Validate environment variables
    server = os.getenv("MQTT_SERVER")
    if not server:
        print("❌ [Config/Error] MQTT_SERVER not configured in .env file")
        print("   Please copy .env.example to .env and configure it")
        sys.exit(1)
    
    username = os.getenv("MQTT_USERNAME")
    password = os.getenv("MQTT_PASSWORD")
    port = int(os.getenv("MQTT_PORT", 1883))
    keepalive = int(os.getenv("MQTT_KEEPALIVE", 60))
    
    print(f"ℹ️  [Config] MQTT Configuration:")
    print(f"   Server: {server}")
    print(f"   Port: {port}")
    print(f"   Username: {username}")
    print(f"   Password: {'*' * len(password) if password else 'None'}")
    print()
    
    # Create MQTT client
    print("🔧 [MQTT/Client] Creating MQTT client...")
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    
    if username:
        client.username_pw_set(username, password)
    
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect
    
    # Connect to broker
    print(f"🔌 [MQTT/Connect] Connecting to {server}:{port}...")
    try:
        client.connect(server, port, keepalive)
        client.loop_start()
        
        print("✅ [MQTT/Status] Connection initiated")
        print("⏳ [MQTT/Status] Waiting for messages (Press Ctrl+C to exit)...")
        print()
        
        # Keep running
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n⚠️  [Test/Interrupt] Test interrupted by user")
    except Exception as e:
        print(f"❌ [MQTT/Error] Connection error: {e}")
        sys.exit(1)
    finally:
        print("\n🛑 [MQTT/Cleanup] Cleaning up...")
        client.loop_stop()
        client.disconnect()
        print("✅ [Test/Complete] Test completed")

if __name__ == "__main__":
    main()
