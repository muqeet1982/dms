#!/usr/bin/env bash
# Installs the DMS security layer: hardened fail2ban jails, ufw port policy and the root helper the
# admin Security pages call. Run once as root:   sudo bash deploy/security/install-security.sh
#
# It is idempotent; re-run it after changing the jail files in this folder.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_USER="${APP_USER:-django}"

echo "==> packages"
command -v ufw >/dev/null || apt-get install -y ufw
command -v fail2ban-client >/dev/null || apt-get install -y fail2ban

echo "==> root helper /usr/local/bin/dms-secops (only this one command is sudo-able by $APP_USER)"
install -m 0755 -o root -g root "$HERE/dms-secops" /usr/local/bin/dms-secops
printf '%s ALL=(root) NOPASSWD: /usr/local/bin/dms-secops\n' "$APP_USER" > /etc/sudoers.d/dms-secops
chmod 0440 /etc/sudoers.d/dms-secops
visudo -cf /etc/sudoers.d/dms-secops

echo "==> fail2ban jails and filters"
install -m 0644 "$HERE/jail-dms.local"        /etc/fail2ban/jail.d/dms.local
install -m 0644 "$HERE/filter-dms-auth.conf"  /etc/fail2ban/filter.d/dms-auth.conf
install -m 0644 "$HERE/filter-dms-probe.conf" /etc/fail2ban/filter.d/dms-probe.conf
install -m 0644 "$HERE/filter-dms-manual.conf" /etc/fail2ban/filter.d/dms-manual.conf
mkdir -p /etc/dms-secops
[ -f /etc/fail2ban/jail.d/dms-ignoreip.local ] || printf '# Managed by dms-secops (Admin → Security → Bans → Trust).\n[DEFAULT]\nignoreip = 127.0.0.1/8 ::1\n' > /etc/fail2ban/jail.d/dms-ignoreip.local
[ -f /etc/dms-secops/trusted.json ] || echo '[]' > /etc/dms-secops/trusted.json

echo "==> firewall policy (public: 22 SSH, 80 HTTP, 443 HTTPS, 8081 attendance device; everything else closed)"
ufw --force default deny incoming
ufw --force default allow outgoing
ufw limit 22/tcp   comment 'SSH (rate limited)'
ufw allow 80/tcp   comment 'HTTP / ACME'
ufw allow 443/tcp  comment 'HTTPS'
ufw allow 8081/tcp comment 'ZKTeco attendance push'
ufw --force enable

echo "==> restart fail2ban"
fail2ban-client reload || systemctl restart fail2ban
systemctl enable --now fail2ban ufw >/dev/null

echo
echo "Done. Check from the app server:  sudo -u $APP_USER sudo /usr/local/bin/dms-secops status | head -c 400"
echo "Admin UI: https://dms.invoiceflow.pk/admin/security  (Bans) and /admin/firewall (Firewall)"
