# AGENTS.md

## What this is

Windows-native PySide6 (Qt Widgets) desktop app for detecting Google Pixel devices over ADB/Fastboot. The UI is custom-painted with `QPainter`; there is no HTML, QML, or embedded browser.

- `main.py` is the only current app entrypoint and owns window/detection lifecycle.
- `pixel_device_detector.py` and `theme_tokens.py` are GUI-free modules.
- `main_flet.py` is a preserved legacy Flet prototype. Don't delete it, don't treat it as the current app, and don't add Flet to `requirements.txt`.

## Commands

- Install: `python -m pip install -r requirements.txt` (PySide6 6.x; code requires Python 3.10+).
- Run from the repo root: `python main.py` (flags: `--dark`, `--light`, `--theme system|light|dark`).
- No test suite, lint, or build config is checked in. Minimum verification:

  ```powershell
  python -m py_compile main.py qt_material.py qt_widgets.py qt_pages.py theme_tokens.py pixel_device_detector.py
  python main.py
  ```

- Modules are flat top-level imports, not a package. Don't run as `python -m ...` or move them into `src/`.

## Backend contracts (easy to break)

- `detect_pixel_device()` returns `PixelDeviceInfo` for expected failures. Don't make it raise for no device, missing ADB/Fastboot, or unsupported commands.
- `detected=True` includes `unauthorized`, `offline`, `sideload`, and `recovery`: the device exists but cannot provide all data.
- Multiple devices produce `detected=False` with non-empty `available_devices` and an `error`. This is a device-selection state, not "no device". The UI relies on that distinction.
- `UNSUPPORTED_CYCLES_MESSAGE` must stay exactly `Не поддерживается для этой модели`; don't reword or derive it.
- `battery_cycles` returns `int` when known, otherwise a display-ready `str`. Branch on `battery_cycle_status` when behavior matters.

## UI and threading gotchas

- Detection runs on a daemon thread and posts results to `queue.Queue`; a `QTimer` polls it. Never call `detect_pixel_device()` on the GUI thread.
- Theme switching rebuilds the entire central widget. Keep persistent state in window attributes and re-render through `_render_body`; page-local state won't survive.
- For deterministic UI work use `PixelToolsWindow(..., auto_refresh=False)` plus `set_device_info(info)` and `set_tab(index)`.
- `qss_for()` sets only colors and scrollbars. Never add `font-size` or `font-family` to the global QSS: it overrides per-widget `ui_font()` and breaks all typography.
- Clickable custom controls should inherit `_PressFeedbackMixin`, call `_init_press_feedback()`, and use `_paint_press_feedback()` so they get the Material ripple and pressed state.
- Icons are procedural paths in `draw_icon()`; there are no external image assets. Add new icon names there.
- Layout is tuned for the 1024x720 minimum: 112 px navigation rail, 500 px phone card, 116 px info cards. Re-check screenshots after changing these.
- UI copy is Russian. Keep existing strings stable when touching states and errors.

## Screenshots

- Use `QT_QPA_PLATFORM=windows` when grabbing screenshots. On this Windows setup the `offscreen` platform finds no fonts and renders text as tofu boxes.
- Without `adb`/`fastboot` in `PATH` the app still runs and shows the empty/error state. Inject mock `PixelDeviceInfo` objects for device views.

## Theme

- `load_theme()` must never raise. Missing or malformed JSON falls back to `DEFAULT_LIGHT_PALETTE` / `DEFAULT_DARK_PALETTE` and sets `warning`; preserve that behavior.
- The bundled palette is a Material Theme Builder export under `schemes.light` and `schemes.dark`.
- Theme file overrides: `PIXELTOOLS_THEME_FILE` (current) and `PIXELTOOLS_THEME` (legacy). Appearance override: `PIXELTOOLS_APPEARANCE`.

User-facing launch and theme details are in `README.md`.
