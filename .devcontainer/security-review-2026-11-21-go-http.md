# Go HTTP server advisory review — 2026-11-21

The updated image scans found `CVE-2026-97054` in Go executables. The
[Go advisory](https://pkg.go.dev/vuln/GO-2026-6859) describes an HTTP/1 server crash
requiring a handler using `MaxBytesReader` or `MaxBytesHandler`, an exceeded body limit,
and a concurrent body read and response-header write. Go 1.26.11 and 1.27.4 fix the
issue. The latest stable releases checked for RootlessKit, beads, Dolt, Syft, and Task
do not provide a confirmed patched binary.

PR #562's [image scan](https://github.com/ff-fab/cosalette/actions/runs/44911654814)
reports this advisory for the following exact executables and Go versions:

- `usr/bin/rootlessctl`, `usr/bin/rootlesskit`, and `usr/bin/rootlesskit-docker-proxy`:
  v1.26.9.
- `usr/local/bin/bd`: v1.26.10.
- `usr/local/bin/dolt`: v1.26.2.
- `usr/local/bin/syft`: v1.27.3.
- `usr/local/bin/task`: v1.27.1.

PR #558's [image scan](https://github.com/ff-fab/cosalette/actions/runs/44911676823)
reports the same advisory for its replacement `usr/local/bin/dolt` at v1.27.3. The scan
reports fixed versions `1.26.11, 1.27.4` for every fingerprint.

RootlessKit's
[API server](https://github.com/rootless-containers/rootlesskit/blob/v3.3.0/pkg/parent/parent.go)
uses a Unix socket. Its
[handlers](https://github.com/rootless-containers/rootlesskit/blob/v3.3.0/pkg/api/router/router.go)
do not explicitly call either affected body-limiting function; the project source review
does not rule out indirect calls. The verified amd64 rootlesskit executable contains the
affected reader symbol; the companion control and proxy executables do not expose that
symbol in the inspected function tables. The corresponding pinned arm64 binaries have
the same symbol results. This is not proof of unreachability through inlining or other
paths.

Beads 1.3.4 explicitly uses `MaxBytesReader` in its
[batch-create](https://github.com/gastownhall/beads/blob/v1.3.4/internal/httpapi/batch_create.go)
and [claim](https://github.com/gastownhall/beads/blob/v1.3.4/internal/httpapi/claim.go)
handlers. Source inspection shows synchronous decoding before the error response, but
middleware and indirect paths have not been fully assessed. Its optional
[`bd serve`](https://github.com/gastownhall/beads/blob/v1.3.4/cmd/bd/serve.go) API
defaults to loopback and can be configured otherwise. Avoid exposing it to untrusted
clients while the affected binary is in use.

Project-source searches found no explicit affected calls in Dolt 2.7.6, Syft 1.57.2, or
Task 3.54.0. Dolt does have HTTP server features; a source search does not rule out
indirect middleware calls. Avoid exposing optional development servers to untrusted
clients. These observations do not prove absence of vulnerable code or behavior. The
affected reader symbol is present in the verified amd64 Syft binary and both Dolt 2.7.6
architecture binaries; it was not found in the inspected amd64 Task binary. The beads
binary contains both the reader and its API JSON-decoding function. The inspected arm64
Syft and beads binaries also contain the affected reader symbol; beads includes its API
JSON-decoding function. Symbol inspection does not replace an arm64 image scan. The
pinned arm64 Task binary also lacks a visible reader symbol; it is stripped, so inlining
and symbol omission limit that observation.

Recheck upstream releases and rebuild or replace every affected binary when a patched
version is available. Follow-up `cos-vpbv` blocks the parent review `cos-nxu6`; the
existing temporary image acceptance expires at 23:59 UTC on 2026-11-21. The policy
continues to reject these new fingerprints pending their specific disposition.

The project owner approved temporary acceptance of the eight exact scanned fingerprints
listed above, including both Dolt versions, through 23:59 UTC on 2026-11-21. The entries
use the existing `go_binaries` group and expiry. This accepts residual risk under the
stated restrictions; new or changed fingerprints and secret findings remain blocking.
Remove each entry when its affected binary is replaced.
