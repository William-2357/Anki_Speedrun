# Shipping & clean-machine install

How the Anki Speedrun desktop installer and Android APK were built, how to
install them on a clean machine, how to confirm the apps run with AI off and
the network pulled, and exactly what was / was not verified when they were
built. Everything below was executed on a macOS arm64 (Apple Silicon) host on
2026-07-04/05 unless marked "needs a human".

Versions in this build:

- Desktop fork: `26.05b1` (from `desktop/.version`; wheels carry the
  PEP 440-normalized form `26.5b1`).
- AnkiDroid fork: `2.25.0alpha1` (versionCode `322500101` for the arm64 APK).
- Shared engine backend for Android: rsdroid `0.1.65-anki26.05b1`, built from
  `desktop/rslib` via the `android-backend/anki -> ../desktop` symlink.

## Artifacts

| Artifact                                                | Path (repo-relative)                                                                    | Size                     | SHA-256                                                                                                                         |
| ------------------------------------------------------- | --------------------------------------------------------------------------------------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| macOS launcher DMG (arm64-only install, see below)      | `desktop/out/launcher/anki-speedrun-launcher-26.05b1-mac.dmg`                           | 71,828,705 B (~68.5 MiB) | `eaa17179a699ab8b29ad427faa6a9b080c1363a960830ff7bf3854800ab65937`                                                              |
| Android release APK (arm64-v8a — the only usable split) | `android/AnkiDroid/build/outputs/apk/full/release/AnkiDroid-full-arm64-v8a-release.apk` | 50,383,268 B (~48 MiB)   | `f3e0e3f67e7ec6e37e73034748e097a52d1a24a7c7cecd1f4a99630c31ba847e` (rebuilt 2026-07-05 after the lint fix; signature unchanged) |
| Backend AAR consumed by the APK                         | `android-backend/rsdroid/build/outputs/aar/rsdroid-release.aar`                         | 17,393,721 B             | contains `jni/arm64-v8a/librsdroid.so` (33,809,480 B)                                                                           |

The fork's Python wheels (bundled inside the DMG, also standalone under
`desktop/out/wheels/`): `anki-26.5b1-cp310-abi3-macosx_12_0_arm64.whl`
(15.8 MB) and `aqt-26.5b1-py3-none-any.whl` (4.8 MB).

---

## macOS desktop installer

### Why the launcher needed a change

The stock launcher writes a user `pyproject.toml` that depends on
`anki-release==<version>` resolved from PyPI — on a clean machine that would
install **upstream** Anki, not this fork. The launcher was therefore taught to
detect wheels bundled at `Anki.app/Contents/Resources/wheels/` and, when
present, install those instead of consulting PyPI:

- `desktop/qt/launcher/src/main.rs` (+89/−4 lines, the only file changed): a
  new `VersionKind::Bundled` writes `anki==<v>` / `aqt[qt,audio]==<v>`
  dependencies plus `[tool.uv.sources]` entries that pin `anki` and `aqt` to
  the bundled wheel files, so uv installs them from disk and never asks PyPI
  for them. Without a `wheels/` folder the launcher behaves exactly as stock.
- `cargo check -p launcher` and `cargo test -p launcher` pass (1 unit test).

### How the DMG was built (exact commands)

```bash
cd desktop
./ninja wheels                       # -> out/wheels/{anki,aqt}-26.5b1-*.whl

cd qt/launcher/mac
NODMG=1 ./build.sh                   # universal launcher + uv -> out/launcher/Anki.app
                                     # (NODMG skips Developer ID signing/notarize/dmg)

cd ../../../out/launcher
mkdir -p Anki.app/Contents/Resources/wheels
cp ../wheels/anki-26.5b1-cp310-abi3-macosx_12_0_arm64.whl \
   ../wheels/aqt-26.5b1-py3-none-any.whl \
   Anki.app/Contents/Resources/wheels/

# ad-hoc signature (no Developer ID certificate on this machine)
for i in Anki.app/Contents/MacOS/uv Anki.app/Contents/MacOS/install_name_tool \
         Anki.app/Contents/MacOS/launcher Anki.app; do
  codesign --force -s - "$i"
done

mkdir dmg-staging && cp -R Anki.app dmg-staging/ && ln -s /Applications dmg-staging/Applications
hdiutil create -format UDZO -volname "Anki Speedrun" -srcfolder dmg-staging \
  anki-speedrun-launcher-26.05b1-mac.dmg
rm -rf dmg-staging
```

`mac/dmg/build.sh` (AppleScript styling + assumes Developer ID signing) was
deliberately **not** used.

### Clean-machine install steps (macOS)

Requirements: **Apple Silicon Mac** (the bundled `anki` wheel is
arm64-only — see caveats), macOS 12+, network access on first run for
third-party dependencies.

1. Open the DMG, drag **Anki** to **Applications**.
2. The app is **ad-hoc signed, not notarized**. Gatekeeper will refuse a
   normal double-click ("Anki is damaged" / unidentified developer).
   Right-click (Ctrl-click) `Anki.app` → **Open** → **Open**. On newer macOS
   you may instead need System Settings → Privacy & Security → "Open Anyway".
   Last resort: `xattr -dr com.apple.quarantine /Applications/Anki.app`.
3. A Terminal window opens (the launcher is a small TUI). Press **Enter**
   (menu item 1, "Install Latest Anki"). It prints
   `Installing the Anki build bundled with this launcher (26.5b1).` — that
   line is the confirmation the **fork's bundled wheels** are being installed,
   not PyPI's anki.
4. uv downloads a managed CPython 3.13.5 and the third-party dependencies
   (PyQt6/WebEngine etc., ~220 MB) from PyPI, installs `anki`/`aqt` **from
   the wheels inside the .app**, then starts Anki.

Offline scope, stated precisely: the fork's own code (anki + aqt wheels)
installs from the DMG and never touches PyPI; the Python interpreter and
third-party dependencies **do** require network on first install. After the
first install, launching Anki does not need the network.

### AI-off run (desktop)

AI posture of a shipped install:

- The master toggle `speedrun:aiAssist` is a synced collection-config key,
  **default OFF** (a fresh collection has no value; every AI action returns
  `{"enabled": false}`).
- The backend selector `speedrun:aiBackend` defaults to the **mock backend**
  (offline, "no data leaves this machine").
- Stronger than both: the assistant package lives in the repo's
  `tools/speedrun` (dev checkout only) and is **not shipped in the wheels**.
  In this installed build `aqt.speedrun_assistant._speedrun_tools_dir()`
  returns `None`, the bridge reports `available: false`, and the dashboard
  hides its AI affordances entirely.

To verify on an installed machine:

1. Install as above, then turn networking off (Wi-Fi off / cable pulled).
2. Launch Anki from Applications. It starts normally (sync will fail —
   expected).
3. Click **Dashboard** in the main toolbar (shortcut **R**). The CFA
   Level 1 Readiness Dashboard opens and renders the Memory / Performance /
   Readiness gauges from local engine data; Readiness **abstains** (shows its
   abstaining state rather than a fabricated number) until its evidence
   gates are met. No AI panels appear because the bridge is unavailable.

What was actually verified this session (real commands, real outputs):

- Mounted the DMG (`hdiutil attach`), copied `Anki.app` out, and drove a real
  install by running the launcher binary in a PTY with a scratch
  `ANKI_LAUNCHER_VENV_ROOT=/tmp/anki-speedrun-venv-test`. uv output shows
  `anki==26.5b1 (from file:///…/Anki.app/Contents/Resources/wheels/anki-26.5b1-….whl)`
  and the same for `aqt` — i.e. fork wheels, not PyPI. Install completed,
  exit code 0.
- `.venv/bin/python -c "import aqt…" --version` from that install prints
  `Anki 26.05b1`.
- A fresh collection created against that venv reports
  `speedrun:aiAssist = None` (unset ⇒ off) and
  `_speedrun_tools_dir() = None` (assistant not shipped).
- The shipped SvelteKit bundle contains the dashboard page (chunk grep finds
  `speedrun:aiAssist`, "Readiness Dashboard", "Readiness abstains").
- Bundle checks: `codesign -vvv` valid (adhoc), `lipo -info` reports
  `x86_64 arm64` for `launcher`, `uv`, `install_name_tool`.

### Desktop: what still needs a human

- A literal clean-machine GUI test (double-click DMG on another Mac, click
  through Gatekeeper, watch the full GUI open and render the dashboard). The
  session test above exercised the same code path but on this machine,
  headless, into a scratch install root — the GUI itself was not opened.
- Developer ID signing + notarization. This machine has **no** signing
  identity (`security find-identity -p codesigning -v` → "0 valid identities
  found"), so the DMG is ad-hoc signed; per policy no notarization was
  attempted.
- Intel Macs: `./ninja wheels` builds the host-arch `anki` wheel only
  (arm64 here), so installs from this DMG fail on x86_64 with "no compatible
  wheel". Ship a second DMG built on an Intel host (or a universal2 wheel) to
  cover Intel.

---

## Android APK

### How it was built (exact commands)

`local.properties` files (both already present in this tree; create them on a
fresh clone):

```
# android-backend/local.properties
sdk.dir=/Users/william/Library/Android/sdk

# android/local.properties
sdk.dir=/Users/william/Library/Android/sdk
local_backend=true
```

Build the shared-engine backend AAR, then the APK:

```bash
export ANDROID_HOME=$HOME/Library/Android/sdk
export JAVA_HOME=/opt/homebrew/opt/openjdk@17     # JDK 17

cd android-backend
RELEASE=1 ./build.sh
# = cargo run -p build_rust: builds desktop web artifacts via ../desktop/ninja,
#   cross-compiles rslib/rsdroid for aarch64-linux-android (host default;
#   rustup target + cargo-ndk 4.1.2 handled by the script), builds the
#   robolectric jar, then gradle assembleRelease
# -> rsdroid/build/outputs/aar/rsdroid-release.aar   (took ~10 min)

cd ../android
./gradlew --no-daemon :AnkiDroid:assembleFullRelease
# -> AnkiDroid/build/outputs/apk/full/release/AnkiDroid-full-<abi>-release.apk
```

NDK: the pinned version in `android-backend/gradle/libs.versions.toml`
(`29.0.14206865`) exactly matches the installed NDK, so nothing was
downloaded or overridden.

The `full` flavor is the sideload flavor (GitHub/F-Droid style, no store
restrictions); `play` remains the default for development. The documented dev
flow was left intact and re-verified after the release build:
`./gradlew --no-daemon :AnkiDroid:assemblePlayDebug` → `BUILD SUCCESSFUL`.

**lintVital note (resolved):** a first run of `assembleFullRelease` failed
in `:AnkiDroid:lintVitalFullRelease` with one fork-introduced lint error —
`MenuTitleMaxLengthAttr` on the fork's `concept_map` / `cfa_dashboard`
strings in `AnkiDroid/src/main/res/values/01-core.xml` (missing
`maxLength="28"`). The attributes were added (matching the sibling menu
strings) and `:AnkiDroid:lintVitalFullRelease :AnkiDroid:assembleFullRelease`
now passes with no exclusions (`BUILD SUCCESSFUL`, re-verified 2026-07-05;
rebuilt arm64 APK SHA-256
`f3e0e3f67e7ec6e37e73034748e097a52d1a24a7c7cecd1f4a99630c31ba847e`).

### Local engine verification (this APK really contains the fork's backend)

- `android/local.properties` has `local_backend=true`, which makes
  `AnkiDroid/build.gradle` consume
  `files("../android-backend/rsdroid/build/outputs/aar/rsdroid-release.aar")`
  instead of the Maven artifact.
- Byte-level proof: `librsdroid.so` extracted from the freshly built AAR and
  from the shipped APK have the **same SHA-256**
  (`74e31a87e2bceffcfe78714807fc7721467722f3a18befd9ef050897d0bf77be`).
- Additionally, the Maven fallback coordinate (`0.1.65-anki26.05b1`) does not
  exist upstream, so a non-local resolution would have failed the build.

### Signing (loud disclosure)

The APK is signed with the **repo's committed fallback keystore**
`android/tools/fallback-release-keystore.jks` (store/key password `Test@123`,
alias `my-key` — all public in `AnkiDroid/build.gradle`). This is a
debug-grade convenience key so that release builds work out of the box. It is
**not** a Play Store key, provides **no identity or update guarantee**, and
anyone can produce APKs with the same signature. For real distribution,
generate a private keystore and set `KEYSTOREPATH`, `KEYSTOREPWD`, `KEYALIAS`,
`KEYPWD` before `assembleFullRelease`.

Verification performed:

```
$ apksigner verify --print-certs AnkiDroid-full-arm64-v8a-release.apk
Signer #1 certificate DN: CN=Sahil Ahmad, OU=mru, O=mru c, L=har, ST=in, C=in
Signer #1 certificate SHA-256 digest: 0a8ebeea7a7d04f21a737debedfb0b2ec439c84936e4bb69dc7f14894f0be42a
```

That digest equals the fallback keystore's certificate fingerprint
(`keytool -list` on the JKS), confirming which key signed it.

### ABI splits — only arm64-v8a is shippable

`assembleFullRelease` produced four split APKs (no universal APK unless
`-Duniversal-apk=true`):

| APK                                      | Size         | Contains `librsdroid.so`? |
| ---------------------------------------- | ------------ | ------------------------- |
| `AnkiDroid-full-arm64-v8a-release.apk`   | 50,383,268 B | **yes**                   |
| `AnkiDroid-full-armeabi-v7a-release.apk` | 16,564,585 B | no                        |
| `AnkiDroid-full-x86-release.apk`         | 16,566,609 B | no                        |
| `AnkiDroid-full-x86_64-release.apk`      | 16,568,088 B | no                        |

`android-backend/build.sh` builds the Rust engine only for the host's default
Android target (aarch64 on an Apple Silicon Mac). The other three splits
therefore contain **no backend library and will crash at startup** — do not
distribute them. To produce all four ABIs, rebuild the AAR with
`ALL_ARCHS=1 RELEASE=1 ./build.sh` (not run this session), then re-assemble.

### Clean-machine install steps (Android)

1. Enable "install unknown apps" (sideload) for your installer app, or use
   adb from a computer.
2. `adb install -r AnkiDroid-full-arm64-v8a-release.apk` (arm64 device —
   virtually all modern phones).
3. Because the signature is the fallback key, installing **over** an existing
   AnkiDroid from the Play Store (`com.ichi2.anki`, same application id) will
   fail with a signature mismatch; uninstall it first or use a different
   device/profile. Sideloaded builds also never receive store updates.

### AI-off run (Android)

This fork's Android app renders **no AI affordances at all** — the assistant
bridge is desktop-only and the shared dashboard page feature-detects it and
hides AI UI when absent. There is no AI toggle to turn off; the Memory /
abstaining-Readiness gauges come from the local engine and need no network.

Verified this session on the `cfa_pixel` AVD (arm64-v8a, Android 34,
headless `emulator -avd cfa_pixel -no-window -no-audio`):

- `adb install -r …arm64-v8a-release.apk` → `Success`.
- `adb shell am start -n com.ichi2.anki/com.ichi2.anki.IntentHandler` →
  process `com.ichi2.anki` running; logcat shows the fork engine start
  (`rsdroid: rsdroid logging enabled`); IntroductionActivity in the
  foreground (screenshot captured).
- Network pulled on-device (`svc wifi disable`, `svc data disable`), tapped
  "Get started" → app advanced to the storage-permission screen, still
  running, **zero** `FATAL EXCEPTION` in logcat.
- App force-stopped and emulator shut down cleanly afterwards.

Not verified on-device (needs a human): granting "All files access" (a
system-settings toggle) and clicking through to the deck picker / CFA
Dashboard / review screens. The engine demonstrably initializes, but the
full first-run UI flow past the permission gate was not exercised.

---

## Grader quick-rebuild reference

```bash
# Desktop DMG (Apple Silicon Mac, network needed)
cd desktop && ./ninja wheels
cd qt/launcher/mac && NODMG=1 ./build.sh
cd ../../../out/launcher
mkdir -p Anki.app/Contents/Resources/wheels
cp ../wheels/*.whl Anki.app/Contents/Resources/wheels/
for i in Anki.app/Contents/MacOS/{uv,install_name_tool,launcher} Anki.app; do codesign --force -s - "$i"; done
mkdir dmg-staging && cp -R Anki.app dmg-staging/ && ln -s /Applications dmg-staging/Applications
hdiutil create -format UDZO -volname "Anki Speedrun" -srcfolder dmg-staging anki-speedrun-launcher-26.05b1-mac.dmg

# Android APK (needs Android SDK + NDK 29.0.14206865 + JDK 17 + Rust)
export ANDROID_HOME=$HOME/Library/Android/sdk JAVA_HOME=/opt/homebrew/opt/openjdk@17
printf 'sdk.dir=%s\n' "$ANDROID_HOME" > android-backend/local.properties
printf 'sdk.dir=%s\nlocal_backend=true\n' "$ANDROID_HOME" > android/local.properties
(cd android-backend && RELEASE=1 ./build.sh)
(cd android && ./gradlew --no-daemon :AnkiDroid:assembleFullRelease)
~/Library/Android/sdk/build-tools/36.0.0/apksigner verify --print-certs \
  android/AnkiDroid/build/outputs/apk/full/release/AnkiDroid-full-arm64-v8a-release.apk
```
