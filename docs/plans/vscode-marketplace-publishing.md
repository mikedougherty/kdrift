# VS Code Marketplace Publishing

**Status**: Not started

## Steps

1. Create a publisher on the [VS Code Marketplace](https://marketplace.visualstudio.com/manage)
   - Publisher ID: `mikedougherty`
   - Display name: as desired

2. Get a Personal Access Token from Azure DevOps
   - Organization: `mikedougherty` (or create one)
   - Scopes: Marketplace > Manage

3. Set up trusted publishing via GitHub Actions (similar to PyPI)
   - Add the PAT as a GitHub Actions secret (`VSCE_PAT`)
   - Create a publish workflow triggered on release

4. Add an extension icon (`vscode-kdrift/media/icon.png`, 128x128)

5. Bump `vscode-kdrift/package.json` version to match the release

6. Test with `npx vsce publish --dry-run` before the first real publish

## Considerations

- The extension currently bundles screenshots (hero, generator-matching, error-state) at ~750KB total. Marketplace has a 10MB limit so this is fine.
- The `LICENSE` warning from `vsce package` needs fixing: either copy the root LICENSE into `vscode-kdrift/` or add it to the VSIX include list.
- Consider whether the extension version should track the Python package version or be independent.
