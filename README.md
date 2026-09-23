# asl-shutdown-monitor

Shut down a headless AllStarLink 3 (ASL3) node with three quick microphone
key-ups — no keyboard, monitor, or DTMF mic required.

A small Python service watches the node's AMI (Asterisk Manager Interface)
event stream for local carrier key-ups. When it sees `REQUIRED` key-ups
within `WINDOW_SEC` seconds, it plays a short "goodbye" telemetry message
and shuts the node down cleanly.

Background, reasoning, and the original walkthrough:
[EtherHam — TechNote: Shutdown an ASL3 Node with Three Key-Ups](https://etherham.com/technote-shutdown-an-asl3-node-with-three-key-ups/)

Tested on a Raspberry Pi Zero 2W and an x86 Intel Celeron N3450 laptop,
both running ASL3 on Debian 13 with AllScan UCI90 interfaces. It should work on any ASL3 node regardless of radio
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

ASL3 already ships a `manager.conf` with a `[general]` section. **Do not add
a second one.** First, confirm the existing one has `enabled = yes`:

```bash
sudo grep -nE '^\[|enabled|bindaddr|port' /etc/asterisk/manager.conf
```

You should see exactly one `[general]` line with `enabled = yes` under it.
Leave `[admin]` and any other existing sections alone — Allmon3 and AllScan
use them.

Then open the file and add **only** this block at the very bottom:

```bash
sudo nano /etc/asterisk/manager.conf
```

```ini
[shutdownmon]
secret = yourpasswordhere
read = all
write = command
```

Pick your own secret — don't reuse another AMI user's password. Save and
exit nano (Ctrl+O, Enter, Ctrl+X).

**Reboot the node now — this step is required.** A `manager.conf` reload
alone has been reported to not pick up a new or changed AMI user reliably;
a full reboot does. Your SSH session will drop; wait about a minute and
reconnect.

```bash
sudo reboot
```

After reconnecting, confirm Asterisk loaded the new user:

```bash
sudo asterisk -rx "manager show user shutdownmon"
```

If it prints details for `shutdownmon`, continue to step 2. If it reports
the user isn't found, recheck the block you added before going further.

### 2. Install the script

First, confirm `curl` is installed:

```bash
which curl
```

If that prints nothing, install it with `sudo apt install -y curl` before
continuing.

Download the script:

```bash
sudo curl -fsSL -o /usr/local/bin/shutdown_monitor.py https://raw.githubusercontent.com/EtherHamRadio/asl-shutdown-monitor/main/shutdown_monitor.py
```

Set ownership and permissions. The service runs as the `asterisk` user, so
it must be able to read the file; these settings also keep the AMI password
inside it away from other users:

```bash
sudo chown root:asterisk /usr/local/bin/shutdown_monitor.py
sudo chmod 750 /usr/local/bin/shutdown_monitor.py
```

Edit the constants at the top of the file:

```bash
sudo nano /usr/local/bin/shutdown_monitor.py
```

```python
AMI_PASS = 'CHANGE_ME'   # must match the secret= line from step 1
NODE = '588412'          # your node number
```

If your server hosts more than one node (for example, a public node plus a
private node used by DVSwitch), set `NODE` to the node your **radio
interface** is attached to. Save and exit nano (Ctrl+O, Enter, Ctrl+X).

`WINDOW_SEC` (default `2.5`) and `REQUIRED` (default `3`) are also
configurable there — see [Configuration](#configuration) and
[Troubleshooting](#troubleshooting) before changing `WINDOW_SEC`.

### 3. Configure sudo permissions

The script needs passwordless permission to run `shutdown` and
`asterisk -rx` (for the "goodbye" announcement). Put the rule in its own
drop-in file rather than editing the main sudoers file:

```bash
sudo visudo -f /etc/sudoers.d/shutdown-monitor
```

It opens in nano. Add this single line, then save and exit (Ctrl+O, Enter,
Ctrl+X):

```
asterisk ALL=(ALL) NOPASSWD: /sbin/shutdown, /usr/sbin/asterisk
```

If `visudo` reports a syntax error on save, press `e` to go back and fix
the line — don't press `Q`.

Confirm the rule took effect:

```bash
sudo -l -U asterisk
```

You should see both commands listed under `NOPASSWD`.

### 4. Test the script by hand

This confirms the password and node number are right before systemd is
involved. **Key up no more than twice during this test** — three key-ups
will really shut the node down.

```bash
sudo -u asterisk python3 /usr/local/bin/shutdown_monitor.py
```

- **Working:** you see `Connected to AMI`, and each key-up prints
  `Key-up detected. 1 within 2.5s window.`
- **Not working:** you see `Connection error… Retrying in 10s`. This
  almost always means `AMI_PASS` doesn't match the `secret=` line. Note
  that `Connected to AMI` prints even when the login is rejected, so the
  retry line is the one to watch for.

Press Ctrl+C to stop the test.

### 5. Install and start the systemd service

```bash
sudo curl -fsSL -o /etc/systemd/system/shutdown-monitor.service https://raw.githubusercontent.com/EtherHamRadio/asl-shutdown-monitor/main/shutdown-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable --now shutdown-monitor
sudo systemctl status shutdown-monitor
```

### 6. Test it for real

Open the live log:

```bash
sudo journalctl -fu shutdown-monitor
```

Key up three times quickly on the node's local mic. You should see the
key-up count climb in the log, hear "goodbye" (if you've set up the
announcement), and the node should power down. Confirm it actually shuts
down before you rely on this in the field.

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
adding the AMI user (see step 1). `sudo asterisk -rx "manager show user
shutdownmon"` confirms whether Asterisk has loaded the user at all.

Note that the log line `Connected to AMI. Monitoring for key-up events.`
appears as soon as the TCP connection opens, before Asterisk accepts or
rejects the login — so seeing it does not mean the password was accepted.

### Service fails with "Permission denied"

The service runs as the `asterisk` user, which must be able to read
`/usr/local/bin/shutdown_monitor.py`. Re-run the `chown root:asterisk` and
`chmod 750` commands from step 2.

### Key-ups on the wrong node (multi-node servers)

`NODE` must be the node your radio interface is attached to. Key-ups
arriving through another node on the same server (e.g. a private node used
by DVSwitch) are deliberately ignored.

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

Then remove the sudoers rule and the `[shutdownmon]` block from
`manager.conf`:

```bash
sudo rm /etc/sudoers.d/shutdown-monitor
sudo nano /etc/asterisk/manager.conf
```

(If you added the rule to the main sudoers file under an older version of
these instructions, remove that line with `sudo visudo` instead.)

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
