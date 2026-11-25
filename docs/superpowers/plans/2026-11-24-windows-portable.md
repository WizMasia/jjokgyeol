# Windows Portable Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single Windows x64 `.paf.exe` that runs 쪽결 and renders PDFs offline on a machine without system WebView2.

**Architecture:** Reuse the Windows installer build and staged typesetting runtime in a PortableApps.com-format directory. Pin Microsoft Fixed Version WebView2 and the unmodified PortableApps packaging tools, configure WebView2 and user data for only the portable launcher, and add the verified PAF to the existing Windows release job.

**Tech Stack:** PowerShell, Python standard library, NSIS, PortableApps.com Installer and Launcher, Tauri, Microsoft WebView2, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-24-windows-portable-design.md`

## Global Constraints

- Windows 10 22H2 x64 and Windows 11 x64 in a writable local NTFS folder. Release requires offline Windows acceptance with no system WebView2 and a standard user.
- One portable artifact named `JjokgyeolPortable_<version>.paf.exe`. Use the official PortableApps tools and include the fixed x64 WebView2, existing render runtime and applicable notices in that artifact.
- Keep the current regular Windows installer and use its same compiled Tauri app and runtime. Apply read/execute ACLs only to the portable runtime path and keep the WebView2 sandbox active.
- Preserve `Data` across a move and upgrade. Keep API keys session-scoped. Publish only after testing the exact draft artifact.

## Review Focus

- Missing or damaged dependencies fail before use, including a changed hash and an invalid runtime layout.
- System WebView2 cannot hide a broken portable build. Verify the running browser process image is within the portable bundle.
- Japanese or Korean names, spaces, and a relocated folder retain user settings and open and render a saved `.jjok`.
- Reinstall over a portable folder preserves existing `Data`. The ordinary installer retains its offline WebView2 mode.
- The release verifies the version and hash of the extra artifact, and cannot create a draft with a missing or mismatched PAF.

### Task 1. Stage the Windows portable bundle

**Files:** `desktop/release/windows.ps1`, `desktop/release/portable.ps1`, `desktop/release/portable.lock.json`, and the launcher and installer inputs under `desktop/release/portable/`. Add `desktop/release/test_portable.py` for portable metadata and any validation seams executed outside Windows.

**Interfaces:** `desktop/release/portable.ps1 -Executable <absolute file> -Runtime <absolute dir> -OutputDir <absolute dir>` consumes the same Windows GUI executable and staged typesetting runtime produced by `windows.ps1`. It creates `JjokgyeolPortable_<version>.paf.exe` under `OutputDir`, where the exact version comes from `JJOKGYEOL_RELEASE_TAG` with the leading `v` removed. Add a `-Portable` switch to `windows.ps1` to preserve its regular-only entry point.

- [ ] Add failing tests for the package version, launch configuration, data retention and rejection of missing or changed dependency inputs. Check `0.1.1-preview.7` yields numeric `PackageVersion=0.1.1.7` and public `DisplayVersion=0.1.1-preview.7`. Verify `AppID=JjokgyeolPortable` and both WebView2 paths are derived from the portable directory.
- [ ] Run the focused tests before implementation and observe the missing portable configuration or command.
- [ ] Identify source URLs from Microsoft and PortableApps.com and verify SHA-256 on the real downloads before pinning. Record exact versions, URLs and hashes in `portable.lock.json`. Refuse missing or mismatched files, invalid x64 runtime layout, output collisions and non-qualifying versions. Stage `App/Jjokgyeol/jjokgyeol-desktop.exe`, `App/Jjokgyeol/runtime`, `App/Jjokgyeol/WebView2`, `App/AppInfo`, and `Data`. Configure the official launcher to select the bundled runtime and place its WebView2 data under `Data`. Handle the Microsoft-required ACLs on the bundle path without changing system locations or disabling the sandbox. Retain `Data` on PAF updates.
- [ ] Invoke the pinned PortableApps tools as a documented, unattended build. Verify there is exactly one expected PAF output and stage it only after validation. Pass the compiled GUI and staged runtime from the existing Windows release script when `-Portable` is present.
- [ ] Run `python -m unittest discover -s desktop/release -p test_portable.py` and the existing `test_bundle.py`. Record both prerequisite failure and valid packaging evidence, then commit.

### Task 2. Verify and integrate the portable release artifact

**Files:** `desktop/qa/runtime/verify-portable.ps1`, `.github/workflows/native-release.yml`, `desktop/release/README.md` and `README.md`.

**Interfaces:** The Windows release job calls `windows.ps1 -Portable`, exports the installer and the exact versioned PAF from one build, and includes the two Windows files alongside Mac and Linux artifacts in the draft verification. The portable verification script consumes a final PAF file, verifies GUI and real PDF through the extracted runtime, and is runnable on a separate Windows environment.

- [ ] Add a regression showing the previous draft file inventory omits the PAF and would accept that missing artifact. Add a Windows acceptance probe requiring app launch, bundled WebView2 process path and a real PDF identical to the numbered fixture. Check an updated package retains an existing `Data` sentinel and the relocated folder keeps settings.
- [ ] Run the inventory check before the workflow change and record failure. Run the portable acceptance probe on a missing or non-portable package to verify a clear failure.
- [ ] Add the version-bound PAF to the Windows upload and full draft checksum verification. Document both installation flows, use of `Data`, the Windows versions and NTFS scope, the pinned runtime updates, and the clean standard-user offline validation. Keep the public prerelease version bump in the actual release preparation once the candidate can pass.
- [ ] Run Windows CI build and portable package validation with a disposable runner. Validate the ordinary installer separately. Use local Python verification and YAML lint or actionlint as appropriate. Commit after seeing results and have the feature branch independently reviewed.
- [ ] Obtain and use a clean Windows x64 environment with no system WebView2. Verify offline launch, settings relocation and upgrade, a `.jjok` save and reopen, exact PDF geometry, and menu installation through PortableApps.com Platform. Record environment, commands and exact artifact hash. An installed-system CI run can prove fixed-runtime selection, and does not replace the clean offline run.
- [ ] Use the repository release workflow to build the next preview from published `main`, verify all four asset hashes, and publish only after the final PAF passes the clean Windows checks.
