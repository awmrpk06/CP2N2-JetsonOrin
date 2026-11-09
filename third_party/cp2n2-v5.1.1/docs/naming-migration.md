# CP²N² naming migration

The project formerly published as **phys-MCP** is now named **CP²N²**:

> **CP²N² — Control Plane for Physical Neural Networks**

Use the following forms consistently:

| Context | Canonical form |
|---|---|
| Paper, prose, headings, and figures | `CP²N²` |
| Repository, package, server, paths, and configuration | `cp2n2` |
| Python class prefix | `CP2N2` |
| Environment-variable prefix | `CP2N2_` |

The previous name remains only where required for historical attribution,
external references, or backward compatibility. It must not be confused with
the independent **PhysMCP** open-standard proposal at `physmcp.org`.

## Compatibility window

- Existing `PhysMCP*` Python class names remain importable as aliases during
  the migration window.
- Existing `PHYSMCP_*` environment variables remain accepted as fallbacks;
  `CP2N2_*` takes precedence when both are set.
- Existing `.physmcp/` data is not moved or deleted. New local state is written
  to `.cp2n2/`.
- Releases and citations published before the rename keep their historical
  names. Current documentation should say “formerly phys-MCP” once where
  continuity matters.

## External rename checklist

- Rename the GitHub repository from `phys-mcp` to `cp2n2`.
- Update the local `origin` URL and verify GitHub's redirect.
- Verify README, schema IDs, badges, release text, and archive metadata.
- Check availability of `cp2n2` on GitHub, PyPI, npm, Zenodo, and relevant
  trademark databases before treating it as a protected product name.
- Keep the May 2026 arXiv record unchanged; use the new name in a revised or
  replacement submission according to arXiv's versioning rules.
