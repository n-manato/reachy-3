#!/usr/bin/env bash
# Install / enable the resident exhibit as a systemd user service.
#   scripts/install_service.sh            install + enable + start
#   scripts/install_service.sh uninstall  stop + disable + remove
# Logs:   journalctl --user -u lta-exhibit -f
# Control: systemctl --user {status|restart|stop} lta-exhibit
set -euo pipefail
cd "$(dirname "$0")/.."
unit_dir="$HOME/.config/systemd/user"

if [[ "${1:-}" == "uninstall" ]]; then
    systemctl --user disable --now lta-exhibit.service || true
    rm -f "$unit_dir/lta-exhibit.service"
    systemctl --user daemon-reload
    exit 0
fi

mkdir -p "$unit_dir"
cp deploy/lta-exhibit.service "$unit_dir/"
systemctl --user daemon-reload
systemctl --user enable --now lta-exhibit.service

# Without linger the user manager (and this service) only runs while the user
# is logged in. Linger starts it at boot.
if [[ "$(loginctl show-user "$USER" -p Linger --value)" != "yes" ]]; then
    loginctl enable-linger "$USER" \
        || echo "Could not enable linger; run: sudo loginctl enable-linger $USER"
fi
systemctl --user --no-pager status lta-exhibit.service | head -15
