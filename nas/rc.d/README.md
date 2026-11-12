# rc.d service scripts

FreeBSD `rc.d` scripts for the wormhole services (bridge in Phase 5, controller in Phase 6)
go here. They should run `$NAS_PATH/current/bin/wormhole <command>` so every deploy is picked
up by a plain `service wormhole restart` (set that as `RESTART_CMD` in `config/nas.env`).
