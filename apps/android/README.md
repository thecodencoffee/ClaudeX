# ClaudeX Android

This is the Android-only native UI, implemented in Rust with Slint. It connects to the existing Python ClaudeX server over HTTPS; the Python backend and tools remain on the configured server. The Android app never starts a local Python process.

The first native client supports connecting with the server origin and access token, loading and creating chats, selecting Chat/Research/Council mode, and streaming responses. Its connection settings are stored in Android's private app data. This is an initial mobile client; it does not yet reproduce every desktop feature such as attachments, generated files, and settings management.

## Build environment

Slint's Android backend requires Android API 26 or later, the Android NDK r30 or later for the prebuilt Skia renderer, and the Rust Android targets. Install `cargo-apk2`, then add the targets:

```sh
rustup target add aarch64-linux-android armv7-linux-androideabi x86_64-linux-android
```

From `apps/desktop`, `npm run android:dev` builds, installs, and launches on a connected device. `npm run android:build` creates a release APK. No APK is built as part of this setup change.

The Android server must be reachable over HTTPS and configured with `CLAUDEX_ACCESS_TOKEN` of at least 32 characters. The server's NVIDIA API key stays on the Python backend.
