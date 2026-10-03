# Bundle Sync Data-Loss Prevention

## Observed behavior

Renaming a completed transcript and its speakers can rename the bundle directory
while a filesystem reconciliation pass is still holding the old path. The pass
can then treat that stale path as an external deletion, soft-delete the active
job, and delete related records such as speaker mappings.

## Invariants

- Filesystem observation must never delete a transcription job or related data.
- A missing or transiently renamed bundle is an observable inconsistency, not
  authorization to delete data.
- Deletion remains available only through the explicit transcription delete API.
- A preserved orphan must remain recoverable even when its bundle is temporarily
  unavailable.

## Implementation

1. Make `BundleSyncService.SyncFromFilesystem` non-destructive.
2. Count and log missing bundle paths without mutating jobs or related records.
3. Keep the existing path-reconciliation behavior for bundles found under a new
   path with the same metadata ID.
4. Replace the destructive orphan test with a regression test proving that the
   job, speaker mappings, summaries, and notes survive.

## Verification

- Run the focused bundle sync and watcher tests.
- Run all tests in `internal/transcription`.
- Confirm the restored Abraham/Leona/Maxi job and speaker mappings remain active.
- Do not restart the installed Plume app until the active transcription batch is
  finished.

## Rollback

Reverting this change restores external-folder-deletion propagation, but that
behavior is intentionally considered unsafe. Explicit in-app deletion is not
affected by this change.
