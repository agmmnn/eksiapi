# Releasing eksiapi

Releases are built once and promoted from the same GitHub Actions artifact. API
tokens are not used: GitHub Actions authenticates to TestPyPI and PyPI through
OpenID Connect Trusted Publishing.

## One-time setup

1. In the GitHub repository, create environments named `testpypi` and `pypi`.
2. Add a required reviewer to the `pypi` environment so production publication
   needs manual approval. TestPyPI can remain automatic.
3. On both PyPI and TestPyPI, create a pending trusted publisher with:

   - PyPI project name: `eksiapi`
   - GitHub owner: `agmmnn`
   - GitHub repository: `eksiapi`
   - Workflow filename: `release.yml`
   - Environment: `pypi` on PyPI, `testpypi` on TestPyPI

4. Protect the default branch with a GitHub ruleset: require pull requests,
   require the `Build and inspect distributions` CI check, and block force
   pushes. That build job depends on linting, the full Python matrix, and the
   coverage gate, so it represents the complete CI result.

No PyPI API token or GitHub Actions secret is required.

## Release checklist

1. Set the target version in `pyproject.toml` and refresh `uv.lock`.
2. Move the relevant entries from `Unreleased` to a dated changelog section.
3. Merge the change only after CI succeeds.
4. Run the `Release` workflow manually and verify the TestPyPI package.
5. Create and push an annotated tag matching the package version:

   ```bash
   git tag -a v1.2.0 -m "eksiapi 1.2.0"
   git push origin v1.2.0
   ```

6. Approve the `pypi` environment deployment. The workflow publishes to PyPI
   and then creates a GitHub Release containing the wheel, source archive, and
   checksums. GitHub also publishes provenance attestations for the artifacts.

The workflow rejects a tag whose version differs from `pyproject.toml`. PyPI
versions are immutable; never reuse or overwrite a published version.
