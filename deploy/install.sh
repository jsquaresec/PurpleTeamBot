#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/install.sh"
  exit 1
fi

APP_DIR=/opt/PurpleTeamBot
APP_USER=purpleteam
REPO_URL=https://github.com/jsquaresec/PurpleTeamBot.git

apt-get update
apt-get install -y --no-install-recommends git nmap python3 python3-venv ca-certificates

if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
fi

if [[ ! -d "$APP_DIR/.git" ]]; then
  git clone "$REPO_URL" "$APP_DIR"
else
  git -C "$APP_DIR" pull --ff-only
fi

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  echo "Created $APP_DIR/.env — add your Discord token/API keys before starting the service."
fi

chown -R "$APP_USER:$APP_USER" "$APP_DIR"
cp "$APP_DIR/deploy/purpleteambot.service" /etc/systemd/system/purpleteambot.service
cp "$APP_DIR/deploy/purpleteambot-restart.service" /etc/systemd/system/purpleteambot-restart.service
cp "$APP_DIR/deploy/purpleteambot-restart.timer" /etc/systemd/system/purpleteambot-restart.timer
systemctl daemon-reload
systemctl enable purpleteambot.service
systemctl enable --now purpleteambot-restart.timer

echo "Install complete."
echo "Daily restart timer enabled for 04:00 America/Chicago."
echo "Edit $APP_DIR/.env, then run: sudo systemctl restart purpleteambot"
echo "Check the timer with: systemctl list-timers purpleteambot-restart.timer"
