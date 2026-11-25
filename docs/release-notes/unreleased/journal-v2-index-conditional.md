## Journal catalog: the signed index is no longer downloaded in full on every refresh

The built-in journal picker and the Agent journal tools used to download the whole signed v2 journal index (a few hundred KB) each time they refreshed it. The app now remembers the index's ETag and asks the server whether it changed (a "not modified" answer carries no body), accepts gzip-compressed responses, and reuses an index it confirmed within the last five minutes without any request. Signature checks, rollback protection and the offline fallback are unchanged.
