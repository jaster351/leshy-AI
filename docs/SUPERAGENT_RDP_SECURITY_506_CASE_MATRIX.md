# RDP credentials and owner approval — 506 security-matrix inputs

Status: stacked DRAFT change set atop PR #377, main remains untouched.

## A coordinated, individually collected security test matrix

| Boundary | Negative input cases | Positive input cases |
|---|---:|---:|
| UTF-8 JSON credential fields (username/password/domain/host) | 225 | 0 |
| Invalid JSON shape, encoding, duplicates, missing/extra keys | 25 | 0 |
| Owner-entered independently verified TLS certificate SHA-256 | 74 | 0 |
| Owner credential file basenames and traversal/control attempts | 59 | 0 |
| TERMSRV DNS identity and type/format errors | 22 | 0 |
| Windows usernames including Unicode and UPN | 0 | 78 |
| Domain names including Unicode and local accounts | 0 | 7 |
| Passwords including punctuation, Unicode and length limit | 0 | 13 |
| BOM, equivalent IPv6 representation, Windows DOMAIN\user | 0 | 3 |
| **Subtotal** | **405** | **101** |

**506 distinct parameterized/individual input scenarios**, each with an expected result.
Counts are explicitly checked by `test_security_matrix_case_counts_are_stable`.
This is a count of authored **test cases**, not 506 separately implemented
product features and not a claim of 506 passed tests.

## Additional production hardening

1. Stat and refuse an empty or oversized credential file before opening.
2. Open exactly the previously chosen owner credential file with no-follow where supported.
3. Ensure the opened file is still a regular file and still has matching device/inode.
4. Read no more than 8,193 bytes, rejecting any input exceeding 8 KiB.
5. Recheck file descriptor identity, size, mtime and ctime after the read.
6. Recheck the directory entry again after the read, before parsing secrets.
7. Reject swapped directory entries and in-place rewrites during the read.
8. Reject a byte count different from the final on-disk regular-file size.
9. Close the descriptor on every read/parse failure.
10. Clear the mutable temporary byte array in a finally block on success and failure.
11. Keep owner path, username and password out of Web responses and exception messages.
12. Test modified, swapped, appended, empty and partly-read broken secret files.
13. Keep the original SSH credential reader unchanged; Windows RDP has its own parser.
14. Preserve legacy host/username/password JSON compatibility.
15. Preserve Windows UPN and DOMAIN\user login forms.
16. Preserve UTF-8 BOM compatibility and equivalent canonical IPv6 addresses.
17. Fail closed for every typed and duplicate-key JSON ambiguity.
18. Reject invalid Unicode surrogate halves before CredSSP encodes the password.
19. Ensure every owner TLS pin, DNS identity and file basename is checked before TCP.
20. Include the full matrix in both existing **manual** Linux and Windows CI.

## Verification status and next acceptance step

- GitHub source readback and static case counting completed.
- GitHub Actions `workflow_dispatch` remains manual because of the repository
  usage policy through 2026-12-01. We have not changed automatic triggers.
- The actual 506-case pytest suite has **not run**. A passing green badge
  is not claimed.
- No outbound RDP connection, certificate enrollment, real Windows
  authentication, framebuffer decode or Stop E2E was attempted.
- This draft must be reviewed and run in the owner's approved Windows
  environment before merging the PR chain to `main`.
- Android and the separate Gusli repo and endpoints were not changed.
