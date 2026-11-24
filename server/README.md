# Server: headless Debian VM on TrueNAS CORE

wormhole and OwnTone run in a small Debian 13 (trixie) VM without a desktop. The VM gets its
own LAN address, which multicast discovery needs (HomePods via mDNS, the turntable via SSDP).
You install from GitHub once; after that, updates come from the web UI.

| File | Purpose |
|---|---|
| `install.sh` | As root in the VM: packages, OwnTone, users, pipe, configs, services; re-runnable |
| `wormhole.service` | systemd unit (installed as `/etc/systemd/system/wormhole.service`) |
| `wormhole.env.example` | Settings, copied once to `/etc/wormhole/wormhole.env` |

On the VM: code in `/opt/wormhole` (a git clone owned by `wormhole`), OwnTone's config at
`/etc/owntone.conf` (rendered from `config/owntone.conf.in`), pipe at
`/var/lib/wormhole/library/turntable`.

## 1. Put the code on GitHub (once, on the Mac)

Create an **empty public** repository `wormhole` on github.com (no README, no license), then:

```bash
cd ~/Documents/git/wormhole
git remote add origin https://github.com/sGoette/wormhole.git
git push -u origin main
```

## 2. Create the VM (TrueNAS CORE web UI)

1. Download the **Debian 13 netinst ISO** for amd64 from https://www.debian.org/distrib/netinst
2. **Virtual Machines → Add**:
   - Guest OS **Linux**, name `wormhole`, boot method **UEFI**, VNC display **on**
   - **2 vCPUs** (1 works), **1024 MiB** memory
   - Disk: create a new zvol, type **VirtIO**, **10 GiB**
   - Network: adapter **VirtIO**, **attach NIC = your physical LAN interface** (bridged; the VM
     gets its own address from your router)
   - Installation media: upload or pick the ISO
3. Start the VM and open its **Display** (VNC) in the TrueNAS UI.

## 3. Install Debian headless

Normal installer. The important answers:
- Hostname **`wormhole`**: the web UI is then at `http://wormhole.local` (also on port 8766)
- **Root password**: set one (then you log in as `root`). If you leave it empty, `root` is
  disabled and the user you create next gets `sudo` instead (log in as that user, then
  `sudo -i`).
- **User account**: the installer always asks for one (name, username, password). Note it.
- **Software selection: untick "Debian desktop environment" and "GNOME"**; tick only
  **"SSH server"** (optional, for pasting commands from the Mac) and
  **"standard system utilities"**

After the reboot, remove the ISO from the VM's devices (CDROM).

If the **VNC picture is garbled** (lines shifted into each other): power off the VM, set the
Display device's resolution to **1024x768**, and/or choose **"Install"** (text) instead of
"Graphical install" in the boot menu.

If **keys come out wrong in the VNC console** (e.g. `-` gives `'` on a Swiss keyboard): VNC
sends US key positions. Run `loadkeys us` (permanently:
`sed -i 's/^XKBLAYOUT=.*/XKBLAYOUT="us"/' /etc/default/keyboard && setupcon`), or use the
VM's **Serial Shell** in TrueNAS or `ssh <user>@wormhole.local` from the Mac instead, where
copy-paste works too.

If the VM has **no network after the install** (card DOWN in `ip link`, no DHCP client:
`dhcpcd`/`dhclient` missing): first check in TrueNAS that its NIC's MAC address differs from
your other VMs'. Then, with your card name, router and a free address:
`ip link set enp0s4 up`, `ip addr add 192.0.2.50/24 dev enp0s4`,
`ip route add default via 192.0.2.1`, `echo nameserver 192.0.2.1 > /etc/resolv.conf`,
`apt-get update`, `apt-get install -y dhcpcd-base`,
`echo allow-hotplug enp0s4 >> /etc/network/interfaces`,
`echo iface enp0s4 inet dhcp >> /etc/network/interfaces`, `reboot`.

If you **don't know any password** after the install: in the boot menu press `e`, add
` rw init=/bin/bash` to the line starting with `linux`, boot with Ctrl+X, run
`passwd root`, then `sync` and `reboot -f`.

If the VM boots into a **UEFI shell** instead of Debian (a known snag with bhyve): type
`FS0:\EFI\debian\grubx64.efi` to boot once, then as root:
`mkdir -p /boot/efi/EFI/BOOT && cp /boot/efi/EFI/debian/grubx64.efi /boot/efi/EFI/BOOT/BOOTX64.EFI`

## 4. Install wormhole (VM console, as root)

Log in as `root`, or as your user and then `sudo -i` (if you left the root password empty).

```sh
apt-get update && apt-get install -y git
git clone https://github.com/sGoette/wormhole.git /opt/wormhole
/opt/wormhole/server/install.sh
```

It ends with the web UI address. Open it on the Mac or iPhone and:
- switch on the HomePods (an Apple TV asks for the PIN it shows; press **Pair**)
- in the Pro-Ject Control app (or Bubble UPnP, or `make turntable` on the Mac),
  choose the speaker **"Wormhole"**. To rename it: web UI → settings (gear icon) → Renderer.
  The app then lists it as a new device; select it once more.

On the iPhone, Safari → Share → **Add to Home Screen** makes the web UI an app.

## Updates

Web UI → settings (gear icon) → **Software** → *Check for updates* → *Update now*. wormhole
pulls the newest commit of `main` from GitHub and restarts itself; *Roll back* returns to the
previous version.

If the update touches `server/` or `config/owntone.conf.in`, the panel says so. Then run
once, in the VM console as root: `/opt/wormhole/server/install.sh`. It also adds settings that
are new in `wormhole.env.example` to `/etc/wormhole/wormhole.env`, keeping your values.

Console alternative (git refuses to run as root on a checkout owned by another user):
```sh
runuser -u wormhole -- /opt/wormhole/bin/wormhole update check   # or: apply / rollback
systemctl restart wormhole
```

Debian itself: `apt-get update && apt-get upgrade` now and then (as root).

## Settings, logs, troubleshooting

- Settings: `/etc/wormhole/wormhole.env`, then `systemctl restart wormhole`. The renderer name
  set in the web UI is kept in `/var/lib/wormhole/settings.json` and wins over
  `RENDERER_NAME`. Pipe or OwnTone keys (`PCM_PIPE`, `PIPE_*`, `OWNTONE_URL`, `LOG_LEVEL`):
  re-run `install.sh` instead.
- Logs: `journalctl -u wormhole -f` · OwnTone: `/var/log/owntone.log`
- Status: `systemctl status owntone wormhole` · `/opt/wormhole/bin/wormhole info`
- "Wormhole" not visible in the turntable app, or no HomePods listed: check that the VM's NIC
  is attached to the LAN interface (bridged) and that it has an address in your LAN
  (`hostname -I`).

## OwnTone notes (verified 2026-11-13 with OwnTone 29.3; 29.4 since 2026-11-24)

- Installed from OwnTone's own Debian build (github.com/owntone/owntone-debian, version and
  sha256 pinned in `install.sh`); the stock config is kept as `/etc/owntone.conf.dist`.
- **HomePods on software 27+** need `user_agent = "AirPlay/999.0.0"` (template, section
  `airplay_shared`; OwnTone 29.4's default, in 29.3 it was in `general`).
  Otherwise they answer `403 Forbidden` to `GET /info` (OwnTone issue #2042).
- **Pipe autostart is off** (`pipe_autostart = false`). wormhole starts the pipe itself
  (`play_pipe()` in `src/wormhole/owntone.py`) when the **first audio** arrives; starting
  earlier left the HomePods silent with FLAC (~3 s to first data, longer than OwnTone's
  2250 ms start buffer). An autostarted queue item had no artwork, title or artist.
- **A speaker can pause everything** (e.g. an Apple TV falling asleep, seen with 29.3; with
  29.4's MediaRemote "now playing" it stays awake). Play from Control Center resumes. After a
  *stop*, nothing restarts the pipe (autostart is off): stop and start the turntable.
- **Lossless from the turntable** (`LOSSLESS_STREAM_PORT=8146`): the Pro-Ject T2 W pushes MP3
  to every renderer and FLAC only to a list of brands, but serves FLAC 24 bit / 48 kHz on port
  8146 to anyone (undocumented). The renderer plays that instead when it answers as FLAC, else
  the pushed stream. Settings → Stream shows what is decoded.
- **PIN devices** (Apple TV): switch on → PIN shown → send the PIN alone → switch on again
  (OwnTone issue #1967). The web UI does this with its *Pair* button.
- **Local audio output is off** (`audio { type = "disabled" }`): the VM has no sound card, and
  OwnTone otherwise lists a speaker "Computer" (ALSA) that fails with
  `laudio: cannot find card '0'`.
- **Speakers you don't use** (e.g. Macs with AirPlay Receiver): hide them in the web UI's
  settings → Speakers. Stored by name in `/var/lib/wormhole/settings.json`.
- **"Now playing" artwork** (Apple TV and other AirPlay devices with a screen): wormhole sets
  `NOW_PLAYING_ARTWORK` as the pipe's queue item `artwork_url` (`file:` path) before pressing
  Play, because OwnTone sends metadata only when playback starts. OwnTone reads the file itself,
  so it must be readable by user `owntone`. It only uses per-track artwork with
  `artwork_individual = true` (in the template) and scales it to 600x600 for AirPlay. An image
  next to the pipe is *not* picked up for pipes (only for files).
- Pipe format: `pipe_sample_rate` 44100/48000/88200/96000, `pipe_bits_per_sample` 16/32,
  always stereo, host byte order. The pipe must be inside a library directory.

## Moving to TrueNAS SCALE later

VMs don't migrate from CORE (bhyve) to SCALE (KVM), but the VM's disk (zvol) does. Create a
new VM on SCALE with that disk, or a fresh Debian VM / LXC container and repeat step 4 (about
10 minutes). The only state outside `/etc/wormhole/wormhole.env` is OwnTone's speaker pairing:
pair the Apple TV again.
