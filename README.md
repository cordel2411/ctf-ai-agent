# CTF AI Agent

Benchmarks an Amazon Bedrock (Claude) agent against a CTFd server and records time and attempts per
challenge, so it can be compared with student data.

```
 Browser ──▶ CTFd (Docker: CTFd + MariaDB + Redis) ◀── agent harness (ctf-agent run)
                                                          │  Converse API + tools
                                                          ▼
                                              Amazon Bedrock (Claude)   run_python ▶ Docker sandbox (no network)
```

## Deploy on AWS (recommended)

1. Push this repo to GitHub as a **public** repo (the instance clones it).
2. AWS Console → CloudFormation → Create stack → upload `infra/cloudformation.yaml`.
   Set `AllowedCidr` to your IP (`x.x.x.x/32`, see https://checkip.amazonaws.com) and `RepoUrl` to your repo.
   Tick the IAM acknowledgement box. Use the same region where you want Bedrock (e.g. us-east-2).
3. Wait ~10 minutes. Open the `ConnectInBrowser` output (Session Manager) and run:
   ```
   cat /opt/ctf-bootstrap-status        # prints OK when done
   ctf-agent check                      # CTFd, AWS identity, Bedrock call
   ctf-agent run                        # solves every unsolved challenge
   ```
4. Open the `CtfdUrl` output. Admin password: `sudo grep ADMIN_PASSWORD /opt/ctf-ai-agent/.env`.
5. Results are in `/opt/ctf-ai-agent/results/<run>/` (`results.csv`, `report.pdf`, `transcripts/`).

## Run locally (Docker Desktop)

```
python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
make up                       # .env with random secrets, fetch CTFd, docker compose up
# edit .env: CTFD_PORT=8000 and CTFD_URL=http://localhost:8000, then `make up` again
aws configure                 # or aws sso login; needs Bedrock access
ctf-agent bootstrap && ctf-agent check && ctf-agent run
```

## AWS Free plan notes

On the Free plan, EC2 is limited to free-tier-eligible types (t3.small, m7i-flex.large, c7i-flex.large, ...),
so the stack defaults to `m7i-flex.large`. If Bedrock denies Anthropic models, upgrading to the Paid plan
keeps your remaining credits usable. Delete the stack when idle (Elastic IP and disk bill while it exists).

## Bedrock access

- Call Claude through an inference profile ID (`us.…` or `global.…`). `ctf-agent models` lists what your
  account/region offers; set `BEDROCK_MODEL_ID` in `.env`.
- First-time Anthropic use can require a use-case form / Marketplace subscription and a verified AWS
  account. `ctf-agent check` prints the exact fix when access is denied.

## Student comparison

Copy `data/student_baseline.example.csv` to `data/student_baseline.csv` and enter real numbers
(`challenge,student_median_seconds,student_mean_attempts,student_solve_rate`; names must match CTFd).
Then `ctf-agent report`. Without that file the report says it uses EXAMPLE data.
Agent time is wall-clock from first model call to correct flag.

## Your own challenges

Add them in the CTFd admin panel (or `ctf_agent/challenges.py`, then `ctf-agent seed`).
`ctf-agent run -c "name"` runs one; `ctf-agent reset-agent` forgets the agent's solves to re-benchmark.

## Security notes

Port 80 is plain HTTP limited to `AllowedCidr`. The agent's code runs in a Docker container with no
network; without Docker it falls back to a plain subprocess (no isolation). The IAM role uses `Resource: "*"`
for Bedrock; remove the Marketplace statement after the first successful call.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `AccessDenied` from Bedrock | See "Bedrock access"; check role has `infra/bedrock-policy.json` |
| "on-demand throughput isn't supported" | Use the `us.`/`global.` model ID |
| Bootstrap failed | `tail /var/log/ctf-bootstrap.log`, fix, `sudo bash /opt/ctf-ai-agent/scripts/bootstrap-ec2.sh` |
| Stack fails: no default VPC | Create a default VPC or add VpcId/SubnetId to the template |
| Challenges show "skipped" | Already solved by the agent; `ctf-agent reset-agent` |

## Tests

`make test` (unit). `CTFD_TEST_URL=http://localhost:4000 pytest -m integration` runs against a fresh CTFd
with a scripted model. CI does both, plus cfn-lint and shellcheck.
