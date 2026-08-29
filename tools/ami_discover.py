#!/usr/bin/env python3
"""
ami_discover.py

Diagnostic helper: connects to the local AMI and dumps every raw event
block to the console and to ami_events.txt as you key up and unkey.

Use this when shutdown_monitor.py isn't reacting the way you expect —
it lets you see the actual event names, EventValue, and node number
your hardware/interface is sending, instead of guessing.

Usage:
    python3 ami_discover.py
    (key up and release a few times, then Ctrl+C)
    cat ami_events.txt

See the "Troubleshooting" section of the README for how to read the
output, in particular how to check whether rapid, back-to-back key-ups
are each producing a distinct RPT_RXKEYED event or getting merged into
one.
"""

import socket
import time

AMI_HOST = '127.0.0.1'
AMI_PORT = 5038
AMI_USER = 'shutdownmon'
AMI_PASS = 'CHANGE_ME'   # match manager.conf; only needs read access for this tool
OUTPUT_FILE = 'ami_events.txt'


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((AMI_HOST, AMI_PORT))
    sock.settimeout(60)
    login = (
        f"Action: Login\r\n"
        f"Username: {AMI_USER}\r\n"
        f"Secret: {AMI_PASS}\r\n\r\n"
    )
    sock.sendall(login.encode())
    print("Connected. Key up and release a few times. Ctrl+C when done.")

    buf = ''
    with open(OUTPUT_FILE, 'w') as f:
        while True:
            try:
                chunk = sock.recv(4096).decode('utf-8', errors='ignore')
                if chunk:
                    buf += chunk
                    while '\r\n\r\n' in buf:
                        block, buf = buf.split('\r\n\r\n', 1)
                        if block.strip():
                            stamped = f"[{time.time():.3f}] {block}"
                            f.write(stamped + '\n---\n')
                            f.flush()
                            print(stamped + '\n---')
            except (socket.timeout, KeyboardInterrupt):
                break

    print(f"\nSaved to {OUTPUT_FILE}")


if __name__ == '__main__':
    main()
