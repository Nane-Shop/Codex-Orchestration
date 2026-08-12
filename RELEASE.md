# Release process

1. Replace `Unreleased` in `CHANGELOG.md` with the release date.
2. Confirm `.codex-plugin/plugin.json`, the changelog, the installed package, and lifecycle fixture all use the same new semantic version. Never publish different plugin behavior under a version already used by another bundle; fix forward with a new version so Codex cannot reuse the old cache identity.
3. Run the source-of-truth local release gate:

   ```bash
   python3 scripts/preflight.py full
   ```

   Local results are PARTIAL; required hosted checks are authoritative. Behavior fixes require an exact regression test, and every plugin payload change requires a strictly greater semantic version. Security or state changes additionally require a threat model, malformed and negative tests, and a fresh final-tree review attested against the exact head SHA. Any later head change invalidates that review.

4. From a new Desktop task, verify one direct same-provider child route. Record `route accepted`; record `used and confirmed` only if the client exposes effective child model/provider/effort metadata.
5. No separate written plan means no Advisor call. Qualify one written-plan Advisor call, one consolidated correction path, and one post-implementation Reviewer call. Confirm no automatic replay or re-review; extra review requires a direct current-task user instruction. If Claude Fable 5 is included, verify its supported seat paths, pinned primary/helper identities, effort, status, process bounds, and disable/restore.
6. If Claude Opus 5 is included in the release, separately qualify its live seat paths from a first-party Claude login. Confirm the pinned `claude-opus-5` primary, exact ten-key `canonicalModel=claude-opus-5` and `provider=firstParty` identity, configured effort, no tools/session persistence, `status --require-effective`, and disable/restore. Keep the bridge's five-attempt transport ceiling distinct from the one-shot orchestration workflow. Bind schema-3 qualification to the exact reviewed head SHA. A local fake-model pass or configured route is readiness only and never live Opus proof; local preflight must never invoke Opus.
7. Merge only after every protected check passes.
8. Create a signed annotated tag named `v<manifest-version>` at the reviewed merge commit.
9. Re-run `python3 scripts/preflight.py full` on the tagged tree, then run `python3 scripts/release_check.py --require-tag` and publish a GitHub release from that tag using the matching changelog section.
10. Upgrade from the previous public version in a clean Codex home, reinstall the plugin, and verify the installed version and skill contents changed before starting a new task. Then verify setup, `status --require-effective`, and disable.

Never move a published release tag. If a release is bad, fix forward with a new version and retain the old tag as provenance.

Before downgrading to a release that predates Planner/state-schema-3 support, run `disable` with the current release. Older versions must fail closed on the unknown state schema rather than guessing how to restore it.
