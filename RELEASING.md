# Releasing

One release is two packages with one version: `heaton-voxel` on PyPI and
`HeatonVoxel` on NuGet.org. The conformance corpus makes them one library,
so they never ship apart. The **Release** workflow
([`.github/workflows/release.yml`](.github/workflows/release.yml)) publishes
both from the commit it runs on, with no API token stored anywhere. Both
registries take GitHub's OIDC token through Trusted Publishing.

## One-time setup

Each item is done once, by the account owner, on the service's own site.

1. **PyPI.** The project doesn't exist yet, so add a *pending publisher* at
   <https://pypi.org/manage/account/publishing/> with:
   - PyPI project name: `heaton-voxel`
   - Owner: `jeffheaton`
   - Repository name: `heaton-voxel`
   - Workflow name: `release.yml`
   - Environment name: `pypi`

   The first publish creates the project and turns the pending publisher into
   a trusted one.
2. **NuGet.org.** Under Account → **Trusted Publishing**, add a policy with:
   - Repository owner: `jeffheaton`
   - Repository: `heaton-voxel`
   - Workflow file: `release.yml`
   - Environment: `nuget`

   The workflow logs in as `jeffheaton` (`NuGet/login`'s `user`), and the
   package belongs to that account. As dynaface found, a new policy is
   provisional and expires if it isn't used within about a week, so add it
   close to the first release.
3. **Codecov.** Add `jeffheaton/heaton-voxel` at <https://app.codecov.io/gh>,
   or give the Codecov GitHub app access to it. The test workflows upload
   with OIDC, so no token is stored. Until the repo is added, the coverage
   badges read "unknown" and the uploads fail quietly; they never fail a
   test run.
4. **Optional: an approval gate.** The publish jobs run in the GitHub
   environments `pypi` and `nuget`, which the first run creates. Required
   reviewers added under *Settings → Environments* make every publish wait
   for a click.

## Making a release

1. Set the new version in all three places:
   - `pyproject.toml` (`version`)
   - `csharp/HeatonVoxel/HeatonVoxel.csproj` (`<Version>`)
   - `heaton_voxel/__init__.py` (`__version__`)

   `tests/test_architecture.py` fails while they disagree.
2. Commit and push to `main`, and let **Test Library** and
   **Test Library (.NET)** go green.
3. Run **Release** from the Actions tab on `main`, typing the version.

The workflow then does the following:

- **Checks before building.** The run must be on `main`. The version typed
  must be the one in all three files. Any `v<version>` tag that already
  exists must point at this commit. Both suites run: the Python one with
  `unittest`, the C# one through the conformance runner.
- **Builds.** It builds the wheel and sdist, checks them with
  `twine check --strict`, installs the wheel into an empty venv and runs
  both console scripts. It packs the `.nupkg` (DLL, IntelliSense XML,
  README, NOTICE) and its `.snupkg` of symbols, with Source Link pointing at
  the commit.
- **Publishes** to PyPI and NuGet.org, each in its own environment.
- **Records the release** by creating `v<version>` and a GitHub release that
  carries every file it published.

Either registry can be unticked. If one publish fails (a registry outage, or
a missing trusted publisher), rerun **Release** at the same version with only
that side ticked. The tag check lets a rerun through on the same commit, and
the GitHub release gains the new files.

A version, once on PyPI or NuGet.org, can't be uploaded again. NuGet's push
skips a duplicate rather than failing. To fix a bad release, publish the next
version.

## Before the first release

- The PyPI, NuGet and Python-versions badges read "not found" until the first
  publish. The others work as soon as the workflows have run on `main`.
- The Unity app takes the C# library as a DLL built from a checkout, not from
  NuGet. Its `tools/update_library.py` is how a release reaches it.
