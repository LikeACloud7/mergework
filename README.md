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

Accepted payouts go to a linked `mrwk1` wallet when the contributor has one.
Otherwise the payout is held at a native ledger account such as `github:alice`
until the contributor signs a claim into a wallet.

## Wallets and Transfers

MRWK wallets use Ed25519 public keys. The address is `mrwk1` plus the first
160 bits of `sha256(public_key)`.

- Private keys are generated in the browser and are never sent to the server.
- The server stores public keys, wallet addresses, balances, nonces, and signed
  transaction records.
- Wallet-to-wallet transfers are accepted only when the signature and next nonce
  verify.
- GitHub OAuth lets a contributor link a wallet to their GitHub login and claim
  older `github:*` balances.

Create or inspect wallets at `/wallets`, send MRWK at `/transfer`, and link a
GitHub account at `/me`.

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

GitHub OAuth requires a GitHub OAuth app with callback URL
`https://mrwk.ltclab.site/auth/github/callback`. Set
`MERGEWORK_GITHUB_OAUTH_CLIENT_ID`, `MERGEWORK_GITHUB_OAUTH_CLIENT_SECRET`, and
`MERGEWORK_COOKIE_SECRET` in the deployment environment before enabling login.
