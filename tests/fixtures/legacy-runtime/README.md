# Retained v0.1.7 runtime fixture

`v0.1.7.tar.gz` is `git archive 0e6c0313904316e893a8e606a2557f016571412e studio`, compressed
with gzip mtime zero. Its SHA-256 is
`05bc76e372eab2e5cba895a6b34906b5b02014783becafda73afcbc88a785223`.
`v0.1.7.commit` is that commit's exact raw Git commit object.

The boundary tests verify both identities and run these unmodified Python
modules after current dispatcher preflight. Retaining the source fixture keeps
legacy compatibility coverage offline and independent of shallow CI history.
No dependencies, compiled outputs, credentials or provider resources are included.
