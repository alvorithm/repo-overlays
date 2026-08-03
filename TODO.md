# TODO

## Follow-ups from the `init` bootstrap feature

`repo-overlay init` and `status --unmanaged` are implemented (design draft:
`work/wf-now/init-bootstrap/DESIGN.md`, overlay-managed via `defaults`). The
engine is in place; what remains is content and adjacent cleanups the design
(§4, §7) deliberately left out of the code change:

- **Ship `_template/` skeletons.** `init` is inert until a source has a
  `_template/`. `defaults` now ships one (a guidance stub plus the
  overlay-usage skills), so the remaining open decision is `memory-bus`:
  whether every new repo is opted into memory routing by default, which is its
  `POLICY.md` call, and the backfill of `dot_hindsight/config.json` for the
  `beadpot`/`penpot` keys.
- **`watch` auto-init is intentionally not built.** The slug choice is a human
  decision (cwd basename is not always right); keep `watch` surface-only. Revisit
  only if the on-demand flow proves too easy to forget.
- **Empty-directory materialisation.** Would delete the `dot_omp/DIR_MUST_EXIST.txt`
  workaround (overlays cannot currently materialise an empty dir) instead of a
  template enshrining it. A `.overlay-keep` marker, or "materialise declared
  directories even when empty". Independent of `init`.
- **Backfill the missing `dot_hindsight/config.json`** for `beadpot`/`penpot`
  once `memory-bus` ships its template — the first real use of the idempotent
  backfill. Check `project:beadpot-userlibs` against the bank first.
- **`.overlay-own` for `penpot/work/`**, matching `beadpot` (still per-file
  excludes today).
