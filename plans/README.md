# Implementation plans

## Start here

The active implementation sequence is [v1/](./v1/README.md). Complete the steps in
numeric order and work on only one step at a time.

The v1 plans support both macOS and desktop Linux. They build one public Facebook Page
crawler before adding general-purpose architecture.

## Next

[v2/](./v2/README.md) puts a local web face on the finished v1 crawler: an HTTP server
over `data/social.db`, a plain server-rendered UI, and a data viewer for posts,
snapshots, and runs. Start it only after the v1 reliability gate passes.

## Later reference

The older numbered files in this directory describe the original multi-source design.
They are retained as reference for later expansion and are **not prerequisites for v1**.
Some of them contain macOS-only decisions such as Keychain, FileVault, and launchd; do not
copy those assumptions into the cross-platform v1.
