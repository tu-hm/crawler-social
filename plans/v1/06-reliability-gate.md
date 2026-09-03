# Step 06 — Prove reliability before expanding

## Outcome

The simple crawler has evidence of reliable use on a supported desktop OS. Expansion is a
decision based on observed needs, not planned abstractions.

## Depends on

- [Step 05](./05-safe-repeat-runs.md) is complete.

## Work, in order

1. Run the crawler manually seven times over at least three days.
2. For every run, record:
   - OS and browser version;
   - elapsed time;
   - snapshots captured;
   - new and existing posts;
   - missing-field counts;
   - final status and error, if any.
3. After each run, verify:
   - raw snapshots were committed;
   - post IDs are unique;
   - state did not advance after failure;
   - every saved snapshot can be parsed offline;
   - database integrity is `ok`.
4. Run all unit tests without launching a browser.
5. Fix reliability issues without adding another source or a generic connector interface.
6. Update setup documentation from the platform actually tested.
7. Before declaring cross-platform verification complete, run the offline test suite on
   both macOS and Linux in CI or on two development environments. Live Facebook capture
   needs one successful manual smoke test per supported OS before claiming full runtime
   support.

## Verification checklist

- [ ] Seven manual runs completed over at least three days.
- [ ] No duplicate Facebook post IDs.
- [ ] Failed runs retained raw snapshots and did not advance state.
- [ ] Offline tests pass on macOS.
- [ ] Offline tests pass on Linux.
- [ ] One live capture smoke test passes on macOS.
- [ ] One live capture smoke test passes on Linux.
- [ ] Browser/profile setup is documented for both operating systems.

## Choose only one next feature

After the checklist passes, choose one:

1. scheduling: LaunchAgent on macOS and a `systemd --user` timer on Linux;
2. comments for the same public Facebook Page;
3. a second public source, followed by the smallest connector interface proven by the two
   implementations; or
4. private data, after implementing the separate governance, encryption, and secrets plan
   for both operating systems.

Do not start more than one expansion track at once.
