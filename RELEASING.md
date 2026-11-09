# Releasing

One release is two packages with one version: `heaton-voxel` on PyPI and
`HeatonVoxel` on NuGet.org. The conformance corpus makes them one library,
so they ship together. The release automation follows the release convention
in `~/.claude/CLAUDE.md`, which dynaface follows too, in two manual steps per
language:
- A **build** tests the code and stamps the version file. It stages the
  packages on S3 with a build record (`<name>-<version>-build.json`: the
  commit, run and build number).
- A **deploy** publishes what was staged, by version. It then tags
  `v<version>` at the commit the build recorded.

Nothing reaches a registry until you run the deploy, and what it publishes is
exactly what the build checked. A tag records a release; it never triggers
one.

| step | Python | .NET |
| --- | --- | --- |
| build and stage | **Build Library** ([`build-lib.yml`](.github/workflows/build-lib.yml)) | **Build Library (.NET)** ([`build-lib-dotnet.yml`](.github/workflows/build-lib-dotnet.yml)) |
| publish | **Deploy Library to PyPI** ([`deploy-lib.yml`](.github/workflows/deploy-lib.yml)) | **Deploy Library to NuGet (.NET)** ([`deploy-lib-dotnet.yml`](.github/workflows/deploy-lib-dotnet.yml)) |

## One-time setup

Each item is done once, by the account owner. Nothing in the workflows can
do it for you.

1. **AWS secrets.** The builds stage on, and the deploys read from,
   `s3://data.heatonresearch.com/library/`, the bucket dynaface uses. Add the
   same three repository secrets dynaface has. Each command prompts for its
   value, so nothing lands in your shell history:

   ```bash
   gh secret set AWS_ACCESS_KEY_ID -R jeffheaton/heaton-voxel
   ```
   ```bash
   gh secret set AWS_SECRET_ACCESS_KEY -R jeffheaton/heaton-voxel
   ```
   ```bash
   gh secret set AWS_DEFAULT_REGION -R jeffheaton/heaton-voxel
   ```

2. **PyPI.** The project doesn't exist yet, so add a *pending publisher* at
   <https://pypi.org/manage/account/publishing/> with:
   - PyPI project name: `heaton-voxel`
   - Owner: `jeffheaton`
   - Repository name: `heaton-voxel`
   - Workflow name: `deploy-lib.yml`
   - Environment name: leave blank

   The first deploy creates the project and turns the pending publisher into
   a trusted one. Dynaface's PyPI deploy uses a stored `PYPI_API_TOKEN`;
   this one uses Trusted Publishing, as both NuGet deploys already do. A
   token for a project not yet on PyPI could only have been an account-wide
   one, and this way no PyPI credential is kept anywhere.
3. **NuGet.org.** Under Account → **Trusted Publishing**, add a policy with:
   - Repository owner: `jeffheaton`
   - Repository: `heaton-voxel`
   - Workflow file: `deploy-lib-dotnet.yml`
   - Environment: leave blank

   The deploy logs in as `jeffheaton` (`NuGet/login`'s `user`), and the
   package belongs to that account. As dynaface found, a new policy is
   provisional and expires if it isn't used within about a week, so add it
   close to the first release.
4. **Codecov.** Add `jeffheaton/heaton-voxel` at <https://app.codecov.io/gh>,
   or give the Codecov GitHub app access to it. The test workflows upload
   with OIDC, so no token is stored. Until it is added, the coverage badges
   read "unknown". A failed upload never fails a run.

## Making a release

1. Set the new version in all four places:
   - `pyproject.toml` (`version`)
   - `csharp/HeatonVoxel/HeatonVoxel.csproj` (`<Version>`)
   - `heaton_voxel/version.py` (`VERSION`)
   - `csharp/HeatonVoxel/Version.cs` (`Version`)

   `tests/test_architecture.py` fails while they disagree.
2. Commit and push to `main`, and let **Test Library** and
   **Test Library (.NET)** go green.
3. Run **Build Library** and **Build Library (.NET)** from the Actions tab.
   - **Python:** static analysis reports (flake8, mypy, bandit, black,
     isort; reports, not gates); the suite under pytest, with JUnit results
     published as a check and coverage reports. Then it stamps
     `heaton_voxel/version.py` with the date and run number, builds the
     wheel and sdist, checks them with `twine check --strict`, and installs
     the wheel into an empty venv to run both console scripts.
   - **.NET:** the `dotnet format` check, which does gate, and a
     vulnerable-packages report. Then it stamps `Version.cs`, builds
     Release, runs the conformance suite over that build, and packs the DLL
     zip and the `.nupkg` and `.snupkg` (symbols, with Source Link).
   - **Both:** the results are uploaded as run artifacts and staged on S3.
4. Look at what the builds staged: the artifacts on each run's page, and the
   reports.
5. Run **Deploy Library to PyPI** and **Deploy Library to NuGet (.NET)**,
   each with the version (e.g. `0.1.0`). Each:
   1. downloads its files and build record for that version from S3;
   2. checks that `v<version>` is either untagged or already at the recorded
      commit, and refuses otherwise;
   3. publishes, then tags `v<version>` at that commit.

   The two builds of one release should come from one commit. The second
   deploy then finds the tag already where it belongs.

A version, once on PyPI or NuGet.org, can't be uploaded again. NuGet's push
skips a duplicate rather than failing; PyPI's refuses it. To fix a bad
release, publish the next version. Rerunning a build at the same version
replaces what is staged on S3, so a deploy always publishes the latest build
of that version.

## Before the first release

- The PyPI, NuGet and Python-versions badges read "not found" until the first
  deploy. The others work as soon as the workflows have run on `main`.
- The Unity app takes the C# library as a DLL built from a checkout, not from
  NuGet. Its `tools/update_library.py` is how a release reaches it.
