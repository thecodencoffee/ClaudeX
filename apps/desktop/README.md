# ClaudeX desktop

The Windows and Linux desktop window is a native [Iced](https://iced.rs/) application. It starts the Python backend as a child process and embeds the existing ClaudeX interface with `iced_wry`; this preserves the current chat UI while Iced owns the native application window and lifecycle.

## Development

From this directory, run:

```sh
npm run desktop:dev
```

The Iced launcher uses the repository `.venv` Python in debug builds when it is available, and falls back to `python` on Windows or `python3` on Linux. Configure NVIDIA credentials in ClaudeX settings as usual.

## Release build

Activate the repository Python environment first so PyInstaller is available, then run:

```sh
npm run desktop:build
```

This builds the Python backend sidecar and then the optimized Iced executable. The backend is copied beside the executable under `resources/backend`. Linux builds require the native WebKitGTK development libraries used by Wry; Windows builds use the system WebView2 runtime.

Android uses the separate native Slint application in `../android`; the Android scripts in this package call that application. The older Tauri Android scaffold remains in the tree but is no longer the configured Android app.
