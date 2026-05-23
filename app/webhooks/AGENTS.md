# Webhook Rules

- Verify `X-Hub-Signature-256` before processing any payload.
- Use GitHub delivery ids for idempotency.
- Maintainer label `mrwk:accepted` authorizes payout; merge or CI alone does not.
- Add replay tests for every new payout path.
