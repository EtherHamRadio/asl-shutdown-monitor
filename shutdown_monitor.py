#!/usr/bin/env python3
"""
shutdown_monitor.py

Watches an AllStarLink 3 node's AMI (Asterisk Manager Interface) event
stream for local carrier key-ups. If the local operator keys up REQUIRED
times within WINDOW_SEC seconds, the node plays a "goodbye" telemetry
message and shuts down cleanly.

Full write-up, install steps, and troubleshooting:
https://etherham.com/technote-shutdown-an-asl3-node-with-three-key-ups/
"""

import socket
import time
import subprocess
import threading
import logging
import sys

AMI_HOST = '127.0.0.1'
AMI_PORT = 5038
AMI_USER = 'shutdownmon'
AMI_PASS = 'CHANGE_ME'   # must match the secret= line for [shutdownmon] in manager.conf
NODE = '588412'          # your node number

# How many key-ups, within how many seconds, to trigger a shutdown.
# The article's default (2.5s / 3 key-ups) works as-is on most nodes.
# On some ASL3 configurations with a longer squelch-tail hang time, a
# key-up that lands before the previous hang time / courtesy tone has
# finished won't register as a clean new event. If quick key-ups aren't
# being recognized, see the "Troubleshooting: rapid key-ups not counted"
# section in the README before just enlarging this window.
WINDOW_SEC = 2.5
REQUIRED = 3

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

key_times = []
lock = threading.Lock()


def trigger_shutdown():
    log.info("Three rapid key-ups detected — initiating shutdown.")
    subprocess.run(['sudo', 'asterisk', '-rx', f'rpt localplay {NODE} goodbye'])
    time.sleep(6)
    subprocess.run(['sudo', '/sbin/shutdown', '-h', 'now'])


def record_keyup():
    now = time.time()
    with lock:
        key_times.append(now)
        cutoff = now - WINDOW_SEC
        while key_times and key_times[0] < cutoff:
            key_times.pop(0)
        count = len(key_times)
        log.info(f"Key-up detected. {count} within {WINDOW_SEC}s window.")
        if count >= REQUIRED:
            key_times.clear()
            return True
    return False


def is_unkey_event(block):
    return (
        'Event: RPT_RXKEYED' in block and
        'EventValue: 1' in block and
        NODE in block
    )


def run():
    log.info(f"Connecting to AMI at {AMI_HOST}:{AMI_PORT}")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((AMI_HOST, AMI_PORT))
    sock.settimeout(30)
    login = (
        f"Action: Login\r\n"
        f"Username: {AMI_USER}\r\n"
        f"Secret: {AMI_PASS}\r\n\r\n"
    )
    sock.sendall(login.encode())
    log.info("Connected to AMI. Monitoring for key-up events.")
    buf = ''
    while True:
        try:
            chunk = sock.recv(4096).decode('utf-8', errors='ignore')
            if not chunk:
                raise ConnectionError("AMI socket closed.")
            buf += chunk
            while '\r\n\r\n' in buf:
                block, buf = buf.split('\r\n\r\n', 1)
                if is_unkey_event(block):
                    if record_keyup():
                        trigger_shutdown()
        except socket.timeout:
            sock.sendall(b'Action: Ping\r\n\r\n')


if __name__ == '__main__':
    if AMI_PASS == 'CHANGE_ME':
        log.error(
            "AMI_PASS is still the placeholder value. Edit shutdown_monitor.py "
            "and set it to match the secret= line for [shutdownmon] in manager.conf, "
            "then restart the service."
        )
        sys.exit(1)
    while True:
        try:
            run()
        except Exception as e:
            log.error(f"Connection error: {e}. Retrying in 10s.")
            time.sleep(10)
