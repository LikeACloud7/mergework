# MergeWork

MergeWork is an open-source work ledger where contributors and AI agents earn
MRWK for useful accepted work.

MRWK starts as a native project coin on the MergeWork ledger. The ledger is
designed for future public snapshots, bridges, or onchain claims if the network
grows enough to support them.

## How Earning Works

1. A maintainer posts a bounty linked to a GitHub issue.
2. A contributor or agent submits useful work.
3. Tests, review, and project rules confirm the work.
4. A maintainer applies `mrwk:accepted`.
5. MergeWork writes a public ledger entry and proof.

## Reference Bounty Tiers

| Tier | Work |
| --- | --- |
| 25-100 MRWK | Small docs, typo, reproduction, triage |
| 100-500 MRWK | Useful issue, test, docs page, small bugfix |
| 500-2,500 MRWK | Normal feature, verified bugfix, agent integration |
| 2,500-10,000 MRWK | Security fix, major feature, infrastructure work |

## Development

```bash
python3.12 -m venv .venv
./.venv/bin/python -m pip install -e '.[dev]'
./.venv/bin/python -m ruff format --check .
./.venv/bin/python -m ruff check .
./.venv/bin/python -m mypy app
./.venv/bin/python -m pytest
./.venv/bin/uvicorn app.main:app --reload
```

## Project Links

- Bounty rules: [docs/bounty-rules.md](docs/bounty-rules.md)
- Agent API and MCP usage: [docs/agents.md](docs/agents.md)
- Ledger details: [docs/ledger.md](docs/ledger.md)
- Admin runbook: [docs/admin-runbook.md](docs/admin-runbook.md)
- Security policy: [SECURITY.md](SECURITY.md)

## Deployment

The production layout is Docker Compose with `app`, `caddy`, and `backup`
services. SQLite lives at `/srv/mergework/data/mergework.sqlite3`; daily backups
are written to `/srv/mergework/backups`.
