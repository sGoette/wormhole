# Backlog

Features we decided to build later. One section per story: why, the agreed approach, and what
it touches. Move a story out (or mark it done with the commit) once it is built.

## Updates run install.sh themselves

**Why:** an update that changes `server/` or `config/owntone.conf.in` still needs
`/opt/wormhole/server/install.sh` run by hand as root in the VM console. The web app runs as
the unprivileged `wormhole` user and cannot do that itself.

**Agreed approach:** a root helper started by systemd; the web app never gets root.
- The web UI only writes a request (`check`, `apply` or `rollback`) to a file under
  `/var/lib/wormhole/`.
- A systemd `.path` unit watches that file and starts a oneshot service as root
  (`server/update.sh`). It runs the git update, runs `install.sh` if server files changed,
  restarts `wormhole`, and writes the outcome (including `install.sh` errors) to a status file
  that the Software panel shows.
- `/opt/wormhole` becomes owned by root, read-only for `wormhole`: the web app can no longer
  change the code. What runs as root is exactly GitHub `main`, as with the manual install.
- On the Mac (no systemd) updates keep running in-process as now.

**Rejected:** a sudoers rule letting `wormhole` run `install.sh`. While `wormhole` owns the
checkout, anyone who takes over the web app (no login) could edit `install.sh` and run it as
root.

**Touches:** `src/wormhole/update.py`, `src/wormhole/web.py` (Software panel: waiting state,
status file), `server/install.sh` (repo owned by root, install the units),
new `server/update.sh` + `server/wormhole-update.path` / `.service`, tests.

**Trade-offs:** a check takes 2–3 s instead of being instant (it goes through the helper).
Rollout: after updating to the version that brings this, run `install.sh` by hand once more;
from then on every update works from the web UI alone.
