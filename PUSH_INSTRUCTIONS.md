# Pushing to https://github.com/aaravr/document_anonymisation

The sandbox can't reach GitHub with credentials, so the local commit was made
in this repo and a `git bundle` was written for you. There are two equivalent
ways to push from your machine.

## Option A — clone the bundle, set the remote, push

```powershell
# from any folder on your machine:
git clone F:\workspace\projects\docment_anonymisation\document_anonymisation\document_anonymisation.bundle document_anonymisation_repo
cd document_anonymisation_repo
git remote remove origin
git remote add origin https://github.com/aaravr/document_anonymisation.git
git push -u origin main
```

## Option B — push from `_repo_for_push/` directly

The `_repo_for_push/` folder contains the same commit and already has the
remote set. From your machine:

```powershell
cd F:\workspace\projects\docment_anonymisation\document_anonymisation\_repo_for_push
git push -u origin main
```

(If the sandbox didn't manage to copy every git object over,
prefer Option A.)

## What's in the commit

Single commit on `main`:

```
4ae02b5 Initial commit: idp_anonymiser + sanitiser packages
```

Tree:

```
idp_anonymiser/        full IDP anonymisation package
sanitiser/             strict test-data sanitiser
tests/                 IDP package tests
tests_sanitiser/       sanitiser tests
seed_entities.example.yaml   demo seed list (CIBC profile)
seed_entities.mercedes.yaml  demo seed list (Mercedes-Benz profile)
pyproject.toml
README.md
.gitignore
```

The `.gitignore` excludes `__pycache__`, `*.egg-info`, output dirs
(`out/`, `sanitised_out_*/`), helper scripts, and local mapping stores.

## Auth

If `git push` prompts for credentials and you don't have a credential helper
configured, the easiest path on Windows is GitHub CLI:

```powershell
gh auth login
```

Or use a personal access token: when prompted for password, paste the token
(your username is `aaravr`).
