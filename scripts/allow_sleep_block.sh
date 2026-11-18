#!/usr/bin/env bash
# Let this user keep the PC awake while the exhibit runs (run ONCE, needs sudo).
#
# The exhibit holds a logind "block" inhibitor (sleep / idle / lid switch) for
# as long as it runs, and drops it when it stops -- so the PC behaves normally
# the rest of the time. Over SSH or with someone else at the desktop, logind
# refuses that by default ("Access denied"); this polkit rule allows it for
# this one user and these three actions only.
#
#   sudo scripts/allow_sleep_block.sh            install
#   sudo scripts/allow_sleep_block.sh remove     uninstall
set -euo pipefail
user="${SUDO_USER:-$USER}"
file=/etc/polkit-1/localauthority/50-local.d/lta-exhibit-inhibit.pkla
if [[ "${1:-}" == "remove" ]]; then
    rm -f "$file"; echo "Removed $file"; exit 0
fi
cat > "$file" <<PKLA
[Leading Through AI exhibit: $user may block sleep while the exhibit runs]
Identity=unix-user:$user
Action=org.freedesktop.login1.inhibit-block-sleep;org.freedesktop.login1.inhibit-block-idle;org.freedesktop.login1.inhibit-handle-lid-switch
ResultAny=yes
ResultInactive=yes
ResultActive=yes
PKLA
chmod 644 "$file"
echo "Installed $file"
# Check as the user (polkit reads the file at once).
if sudo -u "$user" timeout 3 systemd-inhibit --what=sleep:idle --mode=block --who=check --why=check true; then
    echo "OK: $user can now block sleep."
else
    echo "Still refused -- check the file above."; exit 1
fi
