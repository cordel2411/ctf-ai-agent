#!/usr/bin/env bash
# Provision an Amazon Linux 2023 instance: Docker, CTFd, the agent, and a `ctf-agent` command.
# Run as root (EC2 user-data does this). Safe to re-run to update.
#   env: REPO_URL BRANCH AWS_REGION BEDROCK_MODEL_ID
set -euo pipefail
exec > >(tee -a /var/log/ctf-bootstrap.log) 2>&1
echo "=== ctf-ai-agent bootstrap $(date -u +%FT%TZ) ==="

APP_DIR=/opt/ctf-ai-agent
RUN_USER=ec2-user
BRANCH="${BRANCH:-main}"
AWS_REGION="${AWS_REGION:-us-east-2}"
BEDROCK_MODEL_ID="${BEDROCK_MODEL_ID:-global.anthropic.claude-sonnet-5-5}"

fail() { echo "BOOTSTRAP FAILED: $*" | tee /opt/ctf-bootstrap-status; exit 1; }
trap 'fail "line $LINENO (see /var/log/ctf-bootstrap.log)"' ERR

# ---- packages -----------------------------------------------------------------
dnf install -y docker git openssl python3.12 python3.12-pip
systemctl enable --now docker
usermod -aG docker "$RUN_USER"

if ! docker compose version >/dev/null 2>&1; then
  mkdir -p /usr/local/lib/docker/cli-plugins
  curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m)" \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
  chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
fi
docker compose version

# ---- code ---------------------------------------------------------------------
if [ ! -d "$APP_DIR/.git" ]; then
  [ -n "${REPO_URL:-}" ] || fail "REPO_URL is not set and $APP_DIR does not exist"
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
else
  git -C "$APP_DIR" pull --ff-only || echo "git pull skipped"
fi
chown -R "$RUN_USER:$RUN_USER" "$APP_DIR"
cd "$APP_DIR"

as_user() { sudo -u "$RUN_USER" -H env CTF_AGENT_HOME="$APP_DIR" PATH="$APP_DIR/.venv/bin:$PATH" "$@"; }

# ---- config -------------------------------------------------------------------
as_user ./scripts/init-env.sh
as_user ./scripts/fetch-ctfd.sh
upsert() { # upsert KEY VALUE in .env
  if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi
}
upsert CTFD_URL http://localhost
upsert CTFD_PORT 80
upsert AWS_REGION "$AWS_REGION"
upsert BEDROCK_MODEL_ID "$BEDROCK_MODEL_ID"
chown "$RUN_USER:$RUN_USER" .env

# ---- python env ---------------------------------------------------------------
[ -d .venv ] || as_user python3.12 -m venv .venv
as_user .venv/bin/pip install -q --upgrade pip
as_user .venv/bin/pip install -q -e .
docker pull python:3.12-slim   # sandbox image for the agent's run_python tool

# ---- CTFd ---------------------------------------------------------------------
docker compose up -d --build

# convenient wrapper so Session Manager users (ssm-user) can just type `ctf-agent`
cat > /usr/local/bin/ctf-agent <<WRAP
#!/usr/bin/env bash
cd $APP_DIR
exec sudo -u $RUN_USER -H env CTF_AGENT_HOME=$APP_DIR $APP_DIR/.venv/bin/ctf-agent "\$@"
WRAP
chmod +x /usr/local/bin/ctf-agent

ctf-agent bootstrap

PUBLIC_IP="$(curl -s -m 3 -H "X-aws-ec2-metadata-token: $(curl -s -m 3 -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')" http://169.254.169.254/latest/meta-data/public-ipv4 || true)"
cat > /etc/motd <<MOTD

  CTF AI Agent lab
  CTFd:        http://${PUBLIC_IP:-<instance public ip>}   (admin password: sudo grep ADMIN_PASSWORD $APP_DIR/.env)
  Check setup: ctf-agent check
  Run agent:   ctf-agent run
  Logs:        /var/log/ctf-bootstrap.log   |   cd $APP_DIR && docker compose logs ctfd

MOTD

trap - ERR
echo "OK $(date -u +%FT%TZ)" > /opt/ctf-bootstrap-status
ctf-agent check || echo "ctf-agent check reported problems (usually Bedrock access); see README troubleshooting"
echo "=== bootstrap finished ==="
