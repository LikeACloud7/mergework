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
