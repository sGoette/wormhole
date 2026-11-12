# NAS jails

Notes and (later) scripts for the jails. Nothing here is applied automatically — every change
to the NAS is confirmed with the user first (STRATEGY.md §6).

## Requirements from the strategy

- **VNET jail with its own LAN address** (iocage or bastille). SSDP (UPnP) and mDNS (AirPlay)
  are multicast and do not cross a NAT bridge.
- Renderer jail (Phase 4) and OwnTone jail (Phase 3/5). Could be one jail if the named pipe
  is simpler that way — decide in Phase 5.

## Getting deployed code into jails (proposal, not yet done)

Deploy once to a host directory (`NAS_PATH`, e.g. `/usr/local/wormhole`) and nullfs-mount it
read-only into each jail at the same path. Then a single `make deploy` updates every jail and
`current/` resolves identically inside and outside the jails. Alternative: run sshd in the
jail and point `NAS_SSH` at the jail's own IP.

Record the chosen jail manager, jail names, IPs and mounts here once they exist.
