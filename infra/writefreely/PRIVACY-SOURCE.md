# Source for WriteFreely 0.17.2-creator-privacy1

Upstream: https://github.com/writefreely/writefreely
Pinned tag: v0.17.2
Pinned commit: acdc6b9ea4a6b7727c211cb1dcb405a12c87fe12
License: GNU AGPL, included as LICENSE in the upstream source.

The source archive contains only the tracked upstream tree at that commit,
creator-privacy.patch and these instructions. It contains no production database,
configuration, keys, credentials, logs, Git history or compiled binaries.

Apply the patch from the source directory:

    git apply creator-privacy.patch

Build prerequisites: Go 1.25 or newer, C compiler, SQLite support via CGO.
The deployed build used the official Go 1.25.0 toolchain and the module versions
locked by the unchanged upstream go.mod/go.sum.

    GOMAXPROCS=2 go test -vet=off -p 2 -tags sqlite -run TestCreatorViewPrivacy .
    GOMAXPROCS=2 go build -p 2 -tags sqlite \
      -ldflags='-s -w -X github.com/writefreely/writefreely.softwareVer=0.17.2-creator-privacy1' \
      -o writefreely ./cmd/writefreely

The targeted test uses -vet=off because the pinned unmodified upstream contains
nonconstant fmt/log format calls rejected by the newer Go printf vet check.
That upstream issue is not changed by the privacy patch.

Use templates from the patched source tree. The remaining distribution assets
come from the official WriteFreely v0.17.2 Linux amd64 release:
https://github.com/writefreely/writefreely/releases/tag/v0.17.2
SHA-256 of its tarball:
332efcf09236a8ca5ffd91379c00d96a6f59ac52812d792cfcf6ee49a67dd3d9

The patch omits view counts from responses to readers while retaining them for
authenticated owners/admins. It does not alter article bodies, stored analytics,
other API fields, or the content of numeric tables.
