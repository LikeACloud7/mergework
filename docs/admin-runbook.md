# Admin Runbook

## Post a Bounty

1. Create or choose a GitHub issue.
2. Decide the MRWK amount using the reference tiers.
3. Add acceptance text that explains what counts as useful accepted work.
4. Use `/admin` or `POST /api/v1/bounties` with an admin token.
5. Add `mrwk:bounty` to the GitHub issue.

## Accept Work

1. Review the submission and test evidence.
2. Confirm the work matches the bounty acceptance text.
3. Apply `mrwk:accepted`.
4. Confirm the webhook records one ledger payment.
5. Add `mrwk:paid` after the explorer/API shows the proof.

If the contributor linked a wallet, the payout goes directly to that `mrwk1`
address. If not, it goes to `github:{login}` and can be claimed later after
GitHub OAuth and wallet-linking.

## GitHub OAuth

Production GitHub OAuth is configured for `https://mrwk.ltclab.site`.
Contributors use `/me` to sign in, link wallets, and claim older GitHub ledger
balances. If the GitHub app is rotated later, update deployment secrets outside
the repository and restart Docker Compose.

## Disputes

- Ask for concrete missing evidence with `mrwk:needs-info`.
- Use `mrwk:rejected` only when the submission is clearly not acceptable.
- Keep public comments short and specific.
- For security reports, keep private details out of public comments and proofs.

## Operations

- Database: `/srv/mergework/data/mergework.sqlite3`.
- Backups: `/srv/mergework/backups`.
- Health check: `GET /health`.
- Logs: `docker compose logs -f app caddy`.
