# MRWK Ledger

MRWK starts as a native project coin on the MergeWork ledger. The ledger is
designed for future public snapshots, bridges, or onchain claims if the network
grows enough to support them.

## Units

- Genesis supply: `100,000,000 MRWK`.
- Decimal places: 6.
- Storage unit: integer microunits.
- Treasury account: `treasury:mrwk`.

## Bounty Reserve Model

Posting a bounty creates a reserve ledger entry from treasury to
`reserve:bounty:{id}`. Accepted payout moves MRWK from that reserve account to
the contributor account.

This keeps treasury balance useful: it shows MRWK not already reserved for open
bounties.

## Hash Chain

Each ledger entry hashes canonical JSON containing:

- sequence
- entry type
- from account
- to account
- amount
- reference
- previous hash
- timestamp

Entries must be sequential, each `previous_hash` must match the prior entry, and
the stored `entry_hash` must recompute from the entry payload.

## Future Snapshot Path

If MergeWork grows enough, public ledger state can be snapshotted for bridge or
onchain-claim experiments. The public ledger and proof hashes are designed to
make that process auditable.

## Accounts and Sending

MRWK v0 uses native ledger account ids as addresses. A GitHub payout account
looks like `github:alice`; reserve accounts look like `reserve:bounty:1`.

Balances and proofs are inspectable in the explorer. External wallet sending is
not active in v0. The next sendable version should add a signed transfer module,
account key registration, and replay protection before any public transfer UI.
