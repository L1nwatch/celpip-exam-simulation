# Development Cookbook

This is the workflow for changes to the CELPIP Practice Lab.

## Rules

- Work from `/Users/watch/Desktop/celpip_selenium`.
- Keep changes scoped to the requested behavior.
- Add or update tests for behavior changes.
- Do not edit generated `output/` data unless the task is extraction/data repair.
- Do not commit `.env`, API keys, SQLite DBs, recordings, private packs, caches, or generated build artifacts.
- Keep README user-facing. Put development workflow here.

## Dependencies

Use `uv` for local work on this machine and on the server. `uv` creates and manages the project `.venv`; do not run project commands with bare `python3`.

```bash
brew install uv
uv sync --frozen
uv run python --version
uv add <package>
```

Commit `pyproject.toml` and `uv.lock` with dependency changes. CI also installs and uses `uv`, so local and server commands should match the workflow.

## Runtime Data

Local development defaults:

```text
webapp/celpip_practice.db
webapp/recordings/
materials/private/packs/
```

Production should use persistent storage:

```bash
CELPIP_DATA_DIR=/var/lib/celpip-practice
CELPIP_HOST=0.0.0.0
PORT=8787
uv run python server.py
```

Use `.env.example` as the public template and `.env` only for local secrets/config. Production secrets should come from the host's secret manager or environment variable settings.

## Materials

Convert legacy `output/local_celpip*` snapshots into compact private packs:

```bash
uv run python scripts/convert_output_materials.py --clean
```

Optimize private-pack audio after conversion:

```bash
uv run python scripts/optimize_material_media.py
uv run python scripts/optimize_material_media.py --apply
uv run python scripts/optimize_material_media.py --media video
uv run python scripts/optimize_material_media.py --media video --apply
```

The dry run reports how many media files would be transcoded. Audio apply mode converts active pack audio to AAC `.m4a`, rewrites `questions.json` and `material.json`, and removes each replaced source audio file only after its replacement is created. Video apply mode keeps `.mp4` paths stable while recompressing large video tracks with conservative H.264 settings. Keep raw source material backups under `/Users/watch/Desktop/timeline/2026-07-04-celpip-materials-backup/` so packs can be regenerated if quality settings need to change.

Import the fake demo pack into legacy output shape:

```bash
uv run python scripts/import_materials.py materials/demo/local_celpip1_test1 --target output --clean
```

Build the public GitHub Pages preview:

```bash
uv run python scripts/build_pages_preview.py
```

Generated preview output goes to `build/pages-preview/` and must not be committed.

## Screenshots

Regenerate README screenshots from fake demo content:

```bash
uv run python scripts/build_pages_preview.py
uv run python -m http.server 8790 --bind 127.0.0.1 --directory build/pages-preview
uv run python scripts/capture_screenshots.py --base-url http://127.0.0.1:8790 --output-dir screenshots
```

The capture script uses Selenium and installed Google Chrome. It defaults to Selenium Manager; pass `--driver /path/to/chromedriver` only when needed.

## Tests

Run before reporting work done:

```bash
uv run python -m unittest discover -s tests
uv run python scripts/build_pages_preview.py
uv run python -m py_compile server.py scripts/extract_questions.py scripts/open_site.py scripts/convert_output_materials.py scripts/optimize_material_media.py scripts/import_materials.py scripts/build_pages_preview.py scripts/capture_screenshots.py
node --check webapp/app.js
node --check build/pages-preview/webapp/app.js
```

Test coverage expectations:

- `server.py`: drafts, submissions, recordings, API validation, storage config.
- `scripts/extract_questions.py`: timing extraction, speaking parsing, grouping.
- `webapp/`: DOM contracts, API endpoint contracts, public/private material paths.
- `materials/` and generated preview: demo pack shape and public-safe data.

For UI changes, manually verify the changed flow plus one adjacent flow.

## Commit Hygiene

Before committing:

```bash
git status
git diff
```

Commit only related source/test/doc files. Keep ignored local data out of commits:

```text
.env
webapp/celpip_practice.db
webapp/recordings/
materials/private/
build/
output/
```

## Push And Workflow Verification

When the user asks to push, treat that as permission to commit the scoped local changes, push the current branch to `origin`, and immediately verify the GitHub Actions run for the pushed commit. Do not stop after `git push` unless the user explicitly asks not to check Actions.

After pushing changes, check the GitHub workflow run for the exact pushed `HEAD` commit before reporting the work as done. A push is not complete until that workflow is green. If the workflow is red, fetch the failing job/annotations, fix the issue or rerun/retry a transient deploy failure, push again if needed, and then verify the new `HEAD` workflow. Do not report the push as done while the latest run for `main` is failed.

Preferred check:

```bash
git rev-parse HEAD
gh run list --repo L1nwatch/celpip-exam-simulation --commit "$(git rev-parse HEAD)" --limit 5
gh run watch --repo L1nwatch/celpip-exam-simulation
```

If `gh` is not installed or authenticated, use the GitHub Actions UI for the repository and verify the latest run for the pushed commit SHA. Report the workflow name, status, and run URL or failure summary.

For the GitHub Pages preview, the repository must have Pages enabled with Build and deployment source set to GitHub Actions. If the workflow fails at `actions/configure-pages` with `Get Pages site failed`, enable Pages in GitHub repository settings, then rerun the workflow. Do not add private materials or secrets to the workflow.

If the GitHub Pages workflow passes build and upload but `actions/deploy-pages` fails with `Deployment failed, try again later.`, treat it as a transient GitHub Pages deploy failure. Rerun the workflow when `gh` is authenticated; otherwise push a minimal retry commit only after confirming the working tree is clean and local checks pass. Verify the retry run is green and, when possible, confirm the deployed Pages asset changed before closing the task.
