## Relay endpoints that reject an optional field no longer stop the AI on the first request

When a third-party AI endpoint (for example a one-api or new-api relay) rejects the first request because of an optional field such as `include` or the reply length limit, the built-in assistant now retries once without that field (or with the other name for the length limit) and keeps that choice for the rest of the task. Endpoints that reject the key or are out of quota still stop at once with the same message as before.
