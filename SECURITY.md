# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report privately through GitHub's
[**Report a vulnerability**](https://github.com/randallquesadaa/PixMatch/security/advisories/new)
form (Security → Advisories). You should get an acknowledgement within a few
days. Once a fix is available it is released and the advisory is published with
credit to the reporter (unless anonymity is requested).

## Supported versions

The latest released version receives security fixes. PixMatch is a desktop
application with no server component; there is no back-end to patch.

## Threat model / scope

PixMatch reads image and video files that the user points it at, and never
modifies them during analysis. Relevant risks:

| In scope | Not in scope |
|---|---|
| Parsing a crafted image/video to crash, hang, or execute code | Social-engineering the user into deleting their own files |
| Path handling that escapes the selected folder | Bugs that need a already-compromised machine |
| The delete / rename operations touching an unintended file | The optional `[ai]` extra downloading model weights over HTTPS |
| A dependency with a known CVE reaching a release build | Denial of service from an absurdly large library (bounded, documented) |

## Hardening already in place

- Decoded-pixel size is capped (`Image.MAX_IMAGE_PIXELS`) so a decompression
  bomb cannot exhaust memory.
- FFmpeg/ffprobe are invoked with a resolved absolute path and a fixed argument
  list, never through a shell.
- Every destructive action (trash, permanent delete, rename) is explicit,
  confirmed, re-validated immediately before it runs, and written to an
  undoable operation log.
- CI runs `ruff`, `bandit`, `pip-audit`, CodeQL and dependency review on every
  change; releases are built only from a commit that passed all of them.
