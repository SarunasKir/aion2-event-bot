#!/usr/bin/env bash
# Deploy the bot to a free-tier Google Cloud e2-micro VM, as a service that restarts on crashes and reboots.
#
# Run it in Google Cloud Shell (the >_ button at the top of console.cloud.google.com):
#   curl -fsSL https://raw.githubusercontent.com/SarunasKir/aion2-event-bot/main/deploy/gcp.sh | bash
# It asks for the Discord token and the optional Anthropic API key, so they never leave your Cloud Shell.
# Run it again to update the bot to the latest code; press Enter at the prompts to keep the saved keys.
#
# Optional settings: PROJECT (default: Cloud Shell's current project), ZONE (default us-central1-a;
# the free tier covers us-west1, us-central1 and us-east1), VM (default aion2-bot), BRANCH (default main).
set -euo pipefail

PROJECT=${PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}
ZONE=${ZONE:-us-central1-a}
VM=${VM:-aion2-bot}
BRANCH=${BRANCH:-main}
REPO=https://github.com/SarunasKir/aion2-event-bot.git

if [ -z "$PROJECT" ]; then
  echo "No Google Cloud project selected. Run: gcloud config set project YOUR_PROJECT_ID" >&2
  exit 1
fi
echo "Deploying branch '$BRANCH' to VM '$VM' in project '$PROJECT', zone '$ZONE'."

# Read keys from the terminal even when this script itself arrives through a pipe.
read -rsp "Discord bot token (Enter to keep the saved one): " DISCORD_TOKEN </dev/tty; echo
read -rsp "Anthropic API key, for screenshots and image styles (Enter to skip or keep): " ANTHROPIC_API_KEY </dev/tty; echo

gcloud services enable compute.googleapis.com --project "$PROJECT"

if ! gcloud compute instances describe "$VM" --zone "$ZONE" --project "$PROJECT" >/dev/null 2>&1; then
  if [ -z "$DISCORD_TOKEN" ]; then
    echo "A new VM needs the Discord bot token." >&2
    exit 1
  fi
  gcloud compute instances create "$VM" \
    --project "$PROJECT" --zone "$ZONE" \
    --machine-type e2-micro \
    --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud \
    --boot-disk-size 30GB --boot-disk-type pd-standard
  echo "Waiting for the VM to boot..."
  for _ in $(seq 1 30); do
    gcloud compute ssh "$VM" --zone "$ZONE" --project "$PROJECT" --quiet --command true >/dev/null 2>&1 && break
    sleep 10
  done
fi

# The keys go to the VM inside the script on stdin, never on a command line.
REMOTE=$(cat <<'EOF'
set -euo pipefail
DIR=/opt/aion2-event-bot
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3-venv git fonts-dejavu-core
id aion2bot >/dev/null 2>&1 || useradd --system --home-dir "$DIR" --shell /usr/sbin/nologin aion2bot
mkdir -p "$DIR" && chown aion2bot: "$DIR"
as_bot() { sudo -u aion2bot -H "$@"; }
if [ -d "$DIR/app/.git" ]; then
  as_bot git -C "$DIR/app" fetch -q origin "$BRANCH"
  as_bot git -C "$DIR/app" checkout -q -B "$BRANCH" "origin/$BRANCH"
else
  as_bot git clone -q --branch "$BRANCH" "$REPO" "$DIR/app"
fi
[ -x "$DIR/venv/bin/python" ] || as_bot python3 -m venv "$DIR/venv"
as_bot "$DIR/venv/bin/pip" install -q --upgrade pip
as_bot "$DIR/venv/bin/pip" install -q -r "$DIR/app/requirements.txt"

touch "$DIR/.env"
set_env() {  # set NAME=value in .env when a value was given, keeping other lines
  [ -n "$2" ] || return 0
  grep -v "^$1=" "$DIR/.env" > "$DIR/.env.new" || true
  printf '%s=%s\n' "$1" "$2" >> "$DIR/.env.new"
  mv "$DIR/.env.new" "$DIR/.env"
}
set_env DISCORD_TOKEN "$DISCORD_TOKEN"
set_env ANTHROPIC_API_KEY "$ANTHROPIC_API_KEY"
grep -q '^DATABASE_PATH=' "$DIR/.env" || echo "DATABASE_PATH=$DIR/bot.db" >> "$DIR/.env"
chown aion2bot: "$DIR/.env" && chmod 600 "$DIR/.env"
if ! grep -q '^DISCORD_TOKEN=.' "$DIR/.env"; then
  echo "No Discord token saved on the VM yet. Run the script again and paste it." >&2
  exit 1
fi

cat > /etc/systemd/system/aion2-bot.service <<UNIT
[Unit]
Description=Aion 2 event Discord bot
After=network-online.target
Wants=network-online.target

[Service]
User=aion2bot
WorkingDirectory=$DIR/app
EnvironmentFile=$DIR/.env
ExecStart=$DIR/venv/bin/python -m bot.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable -q aion2-bot
systemctl restart aion2-bot
sleep 15
systemctl is-active aion2-bot && echo "The bot is running."
journalctl -u aion2-bot -n 15 --no-pager
EOF
)

{
  printf 'BRANCH=%q\nREPO=%q\nDISCORD_TOKEN=%q\nANTHROPIC_API_KEY=%q\n' \
    "$BRANCH" "$REPO" "$DISCORD_TOKEN" "$ANTHROPIC_API_KEY"
  printf '%s\n' "$REMOTE"
} | gcloud compute ssh "$VM" --zone "$ZONE" --project "$PROJECT" --quiet --command "sudo bash -s"

echo
echo "Done. Logs:    gcloud compute ssh $VM --zone $ZONE -- sudo journalctl -u aion2-bot -f"
echo "Update later: run this script again."
