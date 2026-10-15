# Public I/O Safety CI

This repository tests a standalone local filesystem and Git-read utility using synthetic fixtures.

It has a fresh Git history and contains no production application, runtime state, user data, authentication material, or private-repository checkout.

Standard GitHub-hosted Ubuntu and Windows runners execute local unit tests with read-only permissions.

Passing these tests does not mean that an unrelated private project has passed its regression tests.

To run locally: python -B scripts/verify_public_payload.py and python -B -m unittest discover -s tests -v.

The helper's source digest is pinned. Publishing future changes requires separate review.
