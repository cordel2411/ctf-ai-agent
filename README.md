# CTF AI Agent Tester (Project 3)

AI agent + harness that plays a CTFd-based CTF platform like a student would, and records
**completion time, number of attempts, tokens and transcripts** per challenge so results can be
compared with student data.

Research question: *How can AI agents help identify needed improvements in a CTF platform's content and protections?*

## Quick start (Mac)
    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env     # fill in keys / URL / tokens
    python seed_challenges.py            # optional: create 4 practice challenges
    python agent.py --list
    python agent.py --run-name baseline-1
    python analyze.py                    # needs student_data.csv

## Files
| File | Purpose |
|---|---|
| agent.py | agent loop + harness (limits, scope guard, logging) |
| seed_challenges.py | creates practice challenges via CTFd admin API |
| analyze.py | agent-vs-student comparison table |
| student_data.example.csv | format for student results |

Use only on platforms you are authorized to test. Never commit `.env`.
