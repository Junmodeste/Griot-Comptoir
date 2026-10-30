# Lessons

Format: `[date] | what went wrong | rule to avoid it`

- [2026-10-30] | dev.meta.ai and api.meta.ai were not reachable from the cloud session (network policy); dev.meta.ai is now allowed but only via curl (WebFetch still fails), api.meta.ai is still blocked | Read docs with curl through the session proxy; run live API calls on the Windows machine until api.meta.ai is allowed.
- [2026-10-30] | Commits must look like the user's own work | Commit as the repo owner's GitHub identity (`Jun <241959653+Junmodeste@users.noreply.github.com>`), no Claude co-author or session trailers, in commits or pushes.
