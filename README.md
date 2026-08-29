# asl-shutdown-monitor

Shut down a headless AllStarLink 3 (ASL3) node with three quick microphone
key-ups — no keyboard, monitor, or DTMF mic required.

A small Python service watches the node's AMI (Asterisk Manager Interface)
event stream for local carrier key-ups. When it sees `REQUIRED` key-ups
within `WINDOW_SEC` seconds, it plays a short "goodbye" telemetry message
and shuts the node down cleanly.

Background, reasoning, and the original walkthrough:
[EtherHam — TechNote: Shutdown an ASL3 Node with Three Key-Ups](https://etherham.com/technote-shutdown-an-asl3-node-with-three-key-ups/)

Tested on a Raspberry Pi Zero 2W running ASL3 on Debian 13 with an AllScan
UCI90 interface. It should work on any ASL3 node regardless of radio
interface, since it keys off the AMI event stream rather than hardware GPIO
— see [Troubleshooting](#troubleshooting) below if key-ups aren't being
recognized on your setup.

## How it works

`app_rpt` fires an AMI event, `RPT_RXKEYED` with `EventValue: 1`, whenever
your node's *local* receiver detects a carrier — not when a remote/linked
node's audio comes through. The script logs in to the local AMI, watches for
that event tagged with your node number, and keeps a rolling list of
timestamps. If three of them land within the configured window, it triggers
the shutdown. Because it's keyed off local carrier detection specifically,
someone keying up on a distant linked node can't trigger your local node's
shutdown.

## Prerequisites

- An ASL3 node with SSH/console access
- Python 3 (present by default on ASL3 images)
- `sudo` access to configure AMI, sudoers, and systemd

## Install

### 1. Configure AMI access

Edit `/etc/asterisk/manager.conf` and add a dedicated user for this script:

```ini
[general]
enabled = yes
port = 5038
bindaddr = 127.0.0.1

[shutdownmon]
secret = yourpasswordhere
read = all
write = command
```

Pick your own secret — don't reuse another AMI user's password. After
saving, **reboot the node**. A `manager.conf` reload alone has been reported
to not pick up a new/changed AMI user reliably; a full reboot does.

### 2. Install the script

Copy [`shutdown_monitor.py`](shutdown_monitor.py) to `/usr/local/bin/shutdown_monitor.py`,
then edit the constants at the top of the file:

```python
AMI_PASS = 'CHANGE_ME'   # must match the secret= line above
NODE = '588412'          # your node number
```

`WINDOW_SEC` (default `2.5`) and `REQUIRED` (default `3`) are also
configurable there — see [Configuration](#configuration) and
[Troubleshooting](#troubleshooting) before changing `WINDOW_SEC`.

### 3. Configure sudo permissions

The script needs passwordless permission to run `shutdown` (and `asterisk
-rx` if you enable the optional voice announcement below):

```bash
sudo visudo
```

Add:

```
asterisk ALL=(ALL) NOPASSWD: /sbin/shutdown
```

Or, if you're using the voice announcement feature too:

```
asterisk ALL=(ALL) NOPASSWD: /sbin/shutdown, /usr/sbin/asterisk
```

### 4. Install the systemd service

Copy [`shutdown-monitor.service`](shutdown-monitor.service) to
`/etc/systemd/system/shutdown-monitor.service`, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable shutdown-monitor
sudo systemctl start shutdown-monitor
sudo systemctl status shutdown-monitor
```

Watch it live with:

```bash
sudo journalctl -fu shutdown-monitor
```

### 5. Test it

Key up three times quickly on the node's local mic. You should see the
key-up count climb in the journal log, then a shutdown. Confirm the node
actually reboots/powers down as expected before you rely on this in the
field.

## Configuration

Both are constants at the top of `shutdown_monitor.py`:

| Setting | Default | Meaning |
|---|---|---|
| `WINDOW_SEC` | `2.5` | Key-ups must land within this many seconds of each other |
| `REQUIRED` | `3` | Number of key-ups needed to trigger shutdown |

Start with the defaults. If quick key-ups aren't being recognized on your
node, read [Troubleshooting](#troubleshooting) below before just enlarging
`WINDOW_SEC` — a longer window changes the symptom but may not fix the
underlying cause, and makes an accidental triple-kerchunk shutdown more
likely.

## Optional: voice announcement before shutdown

`shutdown_monitor.py` already calls out to `rpt localplay` before shutting
down:

```python
subprocess.run(['sudo', 'asterisk', '-rx', f'rpt localplay {NODE} goodbye'])
time.sleep(6)
```

Drop a `goodbye.ulaw` file in `/usr/share/asterisk/sounds/en/` to have it
play a short message before the 6-second pause and shutdown. Requires the
extra sudoers line above.

**Note:** if your node has `duplex = 0` set in `rpt.conf`, that suppresses
all telemetry and local audio playback, including this announcement.

## Troubleshooting

### Service connects then immediately disconnects, retrying every 10s

AMI credential mismatch. Double check the `secret=` in `manager.conf`
matches `AMI_PASS` in the script exactly, and that you rebooted after
adding the AMI user (see step 1).

### Rapid key-ups not being counted

Reported independently by a reader migrating a SHARI Pi3U (kits4hams) node
from HamVOIP to ASL3: three quick key-ups weren't registering, but keying
up and *waiting for the node's courtesy/ack tone to finish* after each
key-up before keying up again did work — and increasing `WINDOW_SEC` to 10
made that slower cadence catch it. On the maintainer's own node, three fast
back-to-back key-ups at the 2.5s default work as documented, so this
appears to be config-dependent rather than a bug in the detection logic
itself.

The leading suspect is `hangtime` in `rpt.conf` — the repeater's
squelch-tail hang time, which defaults to 5000ms if unset. A key-up that
lands while the node is still inside that hang window (courtesy tone
playing, channel considered busy) may not produce a clean, distinct
`RPT_RXKEYED` transition the way one does after the node has fully returned
to idle. A HamVOIP-tuned node and a freshly-provisioned ASL3 node can easily
end up with different `hangtime` values, which would explain why the same
physical hardware behaves differently after a HamVOIP → ASL3 migration.

**This is a working hypothesis, not confirmed against the app_rpt source.**
If you hit this:

1. Check `hangtime` in your node's stanza in `rpt.conf`. If it's long
   (multiple seconds), try shortening it (e.g. `500`–`1000`) and re-test
   at the default `WINDOW_SEC = 2.5` before resorting to a longer window.
2. Use [`tools/ami_discover.py`](tools/ami_discover.py) to capture raw,
   timestamped AMI events while doing three deliberately fast key-ups.
   If the second or third key-up produces **no** `RPT_RXKEYED` event at
   all (rather than one just outside your window), that confirms it's
   being absorbed by hang state rather than merely mistimed.

If you can reproduce this and narrow it down further, please open an issue
— a confirmed root cause (and a documented `hangtime` fix) is exactly the
kind of thing worth folding back into this README.

### Indentation errors when pasting the script

If you hand-copy the script instead of using this repo directly, mixed
tabs/spaces from a copy-paste can break Python's indentation. Recovering:

```bash
expand -t 4 shutdown_monitor.py > shutdown_monitor.py.fixed
```

Then diff it against the original before replacing it.

## Diagnostic tool

[`tools/ami_discover.py`](tools/ami_discover.py) connects to the local AMI
and dumps every raw event block — with a timestamp — to the console and to
`ami_events.txt` as you key up and unkey. Useful any time the monitor isn't
reacting the way you expect, or your hardware/interface uses different
event names or values than described above.

```bash
python3 tools/ami_discover.py
# key up and release a few times, then Ctrl+C
cat ami_events.txt
```

## Uninstalling

```bash
sudo systemctl stop shutdown-monitor
sudo systemctl disable shutdown-monitor
sudo rm /etc/systemd/system/shutdown-monitor.service
sudo rm /usr/local/bin/shutdown_monitor.py
sudo systemctl daemon-reload
```

Then remove the `asterisk ALL=(ALL) NOPASSWD: ...` line from sudoers
(`sudo visudo`) and the `[shutdownmon]` block from `manager.conf`.

## Safety note

This script runs `shutdown -h now` with no confirmation once the key-up
threshold is hit. Test it deliberately before relying on it, and don't set
`REQUIRED` so low that routine kerchunking could trigger it by accident.

## Issues

Please file bugs, hardware/config reports, and questions as
[GitHub issues](../../issues) on this repo rather than as comments on the
EtherHam article — it's much easier to track and follow up on here.

## License

MIT — see [LICENSE](LICENSE).
