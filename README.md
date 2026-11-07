# Custom Alarm Clock

A local Windows desktop alarm clock built with Python, PySide6 / Qt 6 and SQLite. All alarm images, animated GIFs and audio come from files you select. The project contains **no alarm media, sample alarms, generated content, default sounds or substitute assets**.

## Open the application

### Build the Windows executable

1. Extract `CustomAlarmClock_FINAL_Optimized.zip`.
2. Install **64-bit Python 3.12** on the Windows build computer. Python 3.10+ is accepted by the build script.
3. Open the extracted `CustomAlarmClock` folder and double-click **`build.bat`**.
4. Run **`dist\CustomAlarmClock.exe`**.

The script creates an isolated environment, installs the pinned requirements, checks Qt image plugins and FFmpeg audio decoder registrations, runs the automated tests, builds a single-file executable, then launches it with a disposable empty profile. It stops and reports any failure. `build.bat --no-pause` runs unattended.

The target computer does **not** need Python. The build downloads dependencies once; the application runs offline. Keep the executable at a stable path if you enable **Start with Windows**.

Target: **Windows 10 version 1809 or later, or Windows 11, x64**. Windows native build and hardware checks are described in [docs/WINDOWS_ACCEPTANCE.md](docs/WINDOWS_ACCEPTANCE.md). This ZIP is source code and build tooling; a Windows executable is not included.

### Run from source

Double-click **`run.bat`**, or use:

```bat
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

The runtime requirements use the official `PySide6_Essentials` and `PySide6_Addons` distributions. These provide the `PySide6` Python package and the required Qt modules without installing unused WebEngine/PDF distributions.

## Create your first alarm

1. Select **+ Add alarm**. Give it a name and choose **One time**, **Every day**, **Selected weekdays**, or **Custom interval**.
2. In **Banner**, select your image or GIF. **Preview Banner** opens the actual popup with the current settings. No selected file means no image.
3. In **Audio / playlist**, import your tracks. Drag rows or use **Move up/down** to arrange them. No tracks means silence.
4. Choose the playback mode and total cycle count. Set independent re-alerts and the default snooze duration on the final tab.
5. Select **Save**. The dashboard shows the next activation. **Trigger now** activates the selected alarm immediately without changing its future schedule.

Alarms are persisted immediately on Save. Duplicated alarms start disabled. Editing, disabling or deleting an alarm stops its active occurrence. Save an enabled one-time alarm with a future date/time to schedule it; an expired one-time schedule displays **No future occurrence**.

## Banner controls

**Where the banner appears:** the selected alarm's banner image/GIF is drawn as the main window's background, behind the clock and alarm list (it follows the alarm you select in the list; no image means no background). Alarm activation no longer opens a popup. Appearance settings that apply to a background (fit, background colour, transparency, opacity, border, rounded corners, custom text, GIF speed/looping) are used; popup-only settings (size, position, monitor, always-on-top, full screen, duration, buttons) are kept in your saved alarms but have no effect on the background. **Preview Banner** and **Show Banner** still open the popup window on demand.

Supported image formats: GIF, PNG, JPG/JPEG, WEBP, BMP, TIFF, ICO and SVG. GIFs use **QMovie**, including real frame updates, adjustable speed and optional looping. SVGs use Qt SVG. Image files are opened read-only.

Each alarm stores its own dimensions, locked width/height ratio, fit/fill/stretch setting, monitor, preset/custom position, topmost/full-screen mode, background, transparent background, opacity, border, corner radius, text, text position, font, font size, text color, text/button visibility, GIF speed, display duration and timeout action.

- **Use image ratio** adjusts the height to the selected image's intrinsic ratio.
- Custom X/Y are relative to the chosen monitor's work area; negative coordinates are allowed.
- **Image / GIF only** hides text and buttons, removes the border/corner clipping, and uses a transparent background.
- A duration of zero means manual close. A positive duration either closes the banner or dismisses the occurrence, according to your selected action.
- Right-click any banner for controls. **Esc closes only that banner**.
- Image-only alarms remain accessible through **Active alarms** and the **system tray**.
- If a selected monitor is disconnected, the app reports it and places the window on the primary monitor so the controls remain reachable.

Invalid, missing or unsupported images produce a clear error. No image is substituted.

## Audio and exact playlist counts

MP3, WAV, FLAC, OGG and other files accepted by Qt Multimedia can be selected. Playback uses **QMediaPlayer + QAudioOutput**. Each advance is driven by **EndOfMedia**, never a timer guessing the track length. Timers are used only for the pauses you configure and volume fades.

| Mode | Behavior |
| --- | --- |
| Single selected track | Plays the currently selected row once. |
| Sequential playlist | Plays the complete playlist once. |
| Cyclic playlist | Plays the complete playlist for the chosen total cycles, or infinitely. |
| Shuffle playlist | Plays every track once per cycle in shuffled order. Optionally reshuffles each cycle. |
| Repeat selected track | Repeats the selected row for the chosen total cycles, or infinitely. |
| Repeat playlist a custom number of times | Plays the complete playlist for 1–999 total cycles, or infinitely. |
| Infinite playlist until stopped | Repeats the entire playlist until stopped, snoozed or dismissed. |

For three selected tracks, **Count 2 = A → B → C → A → B → C**. It does not mean an initial cycle plus two repeats. The cycle pause replaces the between-track pause at the end of a cycle.

Use **Preview track** or **Preview playlist**, followed by **Pause**, **Resume** or **Stop**. Volume changes apply immediately to an active editor preview. Fade-in applies at activation; fade-out applies at the end of the final track and on an explicit stop. Replacement by a re-alert stops the old player immediately before restarting.

If a track is missing, unreadable or rejected by the decoder, the playlist stops and identifies the file. It does not skip to another track or play a replacement. An unavailable audio output is reported as an error.

## Active alarm actions

| Action | Result |
| --- | --- |
| Pause / Resume | Pauses or resumes the current audible playlist, including a configured gap. |
| Stop Sound | Stops this occurrence's audio and removes it from the audio queue. Pending re-alerts remain. |
| Snooze | Hides the banner, stops the current audio, and delays activation by a duration you choose. The additional re-alert count is preserved. When snooze ends, a fresh playlist activation begins and the next re-alert interval starts then. |
| Dismiss Current | Stops this occurrence and cancels its pending snooze/re-alerts. Future recurring schedules remain. |
| Close Banner | Closes only the popup. Audio and pending re-alerts remain. |
| Show Banner | Opens the configured banner again for an active, non-snoozed occurrence. |

Re-alert count means **additional activations**. With an 08:00 alarm, a five-minute interval and count 2, activations are 08:00, 08:05 and 08:10 unless dismissed or snoozed. Audio cycle count is independent.

Choose whether each re-alert restarts the playlist, reopens a previously manually opened banner, or keeps the banner visible after playback ends. Initial activation does not open a normal popup. Once **Show Banner** is used, its reopen and restart-restoration settings apply to that occurrence. For silent alarms, banner lifetime follows the configured display duration/manual close setting.

**Simultaneous alarms:** every occurrence has its own banner and controls. One audio queue serializes their playlists so audio never unintentionally stacks. An infinite or paused playlist holds that queue until stopped, resumed, snoozed or dismissed. Live alarms interrupt editor previews. A new scheduled occurrence of the *same alarm* supersedes the old occurrence.

## Scheduling, restart and sleep behavior

- Calendar schedules use the current Windows local time zone. The time zone is reloaded once per minute. Daily/weekly/day-interval/week-interval schedules preserve local wall-clock time; second/minute/hour intervals use elapsed time from their anchor.
- A fall-back DST time rings at its **first instance only**. A nonexistent spring-forward time is shifted forward by the actual DST gap, including half-hour transitions.
- Persistent occurrence keys prevent the same scheduled activation from ringing again after a clock rewind or restart.
- In **Settings**, choose whether to ring the latest missed scheduled occurrence within the configured lookback or skip missed schedules. Missed occurrences are coalesced instead of producing a burst.
- An overdue pending snooze or re-alert resumes once. It does not burn through all remaining repetitions while the computer was asleep.
- With active-occurrence restoration enabled, manually opened, unclosed banners and audio resume on restart. Corner images and message notifications stay hidden on restoration. Completed tracks/cycles are saved. An interrupted track restarts from its beginning; already completed cycles are preserved. Paused playback remains paused. Stopped audio and manually closed banners remain stopped/closed.
- The application must be running for alarms to activate. It continues scheduling in the tray. It cannot execute while Windows is asleep or shut down; it catches up on resume according to the selected policy. It does not install a service or wake the PC.

## Tray and Windows startup

The tray menu restores the dashboard, controls each active occurrence, dismisses all active occurrences, and provides **Exit**. The close/minimize-to-tray options are configurable. If no tray is available, closing the main window exits instead of hiding an unreachable app.

**Start with Windows** uses the current user's `Run` registry entry; no administrator rights are required. **Exit** stops timers and players, closes windows, and ends the process. Opening the application again restores an existing instance rather than starting duplicate schedulers for the same settings directory.

## Corner image overlay (multi-image carousel, alarm-only)

**Edit alarm → Corner Image** manages that alarm's own imported pictures, playback order, progression and display settings. The Dashboard has no global Corner Image button. One physical overlay window is shared; simultaneous activations choose the first eligible alarm in the batch and advance only its carousel. Supported images include PNG, GIF, JPG, WEBP, BMP, TIFF, ICO and SVG. The overlay is placed at the exact bottom-right monitor corner using `QScreen.geometry()`, including the taskbar corner. Source files are never changed.

**It appears ONLY when an alarm activates.** *Enable Corner Overlay* merely arms it. The only things that open it are a scheduled alarm, **Trigger Now**, a re-alert, and the end of a snooze. It never opens at start-up, restart, tray restore, Dashboard/Edit Alarm/Corner Image settings, importing or changing images, changing size/opacity/monitor/mode, monitor changes, previews, or when a persisted active alarm is merely *restored* (`reason="restore"`). A scheduled alarm that was missed while the app was closed and fires when it starts is a real activation and does show it. The overlay is closed by dismissing or snoozing the alarm(s) that opened it (an unrelated alarm does not close it), by the × button, or by exiting. There is one shared window; simultaneous alarms use it once.

**Hover × close button.** The window receives mouse input (no more click-through). Normally only the picture is visible; when the cursor is over it, a small × appears in its top-right corner (inside its bounds). Clicking it closes the picture only: it does not dismiss, snooze or stop the alarm or its sound. It reopens at the next genuine activation or re-alert.

**Carousel.** Add, remove and reorder images; choose sequential or shuffle order, and **Loop** or **Hold last image**. The default advances once per activation: first activation shows image 1, first re-alert image 2. Pressing Snooze does not advance; snooze expiry does. Settings, restart, monitor changes and restoration never advance it. Each alarm's position and shuffle cycle are saved independently.

**Optional timed cycling** (off by default): every N seconds while an alarm-opened overlay is on screen; stops when it is closed, dismissed or snoozed, and can never open the overlay.

- Sizes are Windows display-scaled (device-independent) pixels. Scaling modes: **Original size**, **Fit** (window shrinks to the picture so it still touches the corner), **Fill** (crops to Width × Height), **Stretch**. Opacity, Always on Top and optional in-memory **Trim transparent edges** are available.
- If an imported file is missing or unreadable when its turn comes, the overlay is skipped for that activation (status bar message and log); no other picture is substituted.
- Use **Preview Image** to inspect a file. Hover over the alarm-triggered overlay and click × to close only that picture.

## Settings, logs and portability

Windows data is stored in:

```text
%APPDATA%\CustomAlarmClock\alarms.sqlite3
%APPDATA%\CustomAlarmClock\application.log
```

The database stores alarms, preferences, immutable occurrence snapshots, re-alert/snooze state, track/cycle checkpoints and the event history. Ordered database migrations preserve existing settings. The database uses SQLite transactions and WAL.

The application stores **paths to your media**; it never changes or copies your originals. Moving your media requires selecting the new paths in the editor. Moving only the EXE to another PC does not transfer settings or media. To back up settings, Exit first and copy the application data folder. **Event log → Open settings & logs folder** opens it.

For isolated development or a separate profile:

```bat
CustomAlarmClock.exe --data-dir "C:\YourFolder\AlarmProfile"
CustomAlarmClock.exe --minimized
```

## Tests and structure

```bat
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe scripts\check_runtime.py --windows
```

Tests use isolated databases, controlled engine clocks, temporary test-only media, real Qt widgets/GIF/WAV decoding, and audio playback test doubles. No fixtures are application defaults. See [ENGINEERING_REPORT.md](ENGINEERING_REPORT.md), [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md), and [docs/WINDOWS_ACCEPTANCE.md](docs/WINDOWS_ACCEPTANCE.md).

| File/module | Responsibility |
| --- | --- |
| `ui/corner_settings.py` | Private Corner Image draft controls in each alarm editor. |
| `ui/corner_overlay.py` | Alarm-gated bottom-right corner overlay window (hover × button, timed cycling) and its saved configuration. |
| `carousel.py` | Qt-free, SQLite-persisted carousel state (sequential/shuffle, loop/finite, position). |
| `models.py`, `storage.py` | Serializable settings, SQLite persistence and migrations. |
| `scheduling.py`, `engine.py` | DST-aware recurrence, occurrence claims, re-alert/snooze state. |
| `playlist.py`, `media.py` | Exact cycle sequencing, completion events, fades, serialized playback. |
| `controller.py` | Qt timers/signals and integrated alarm controls. |
| `ui/` | Dashboard, editor, banners, themes and preferences. |
| `platform_windows.py`, `main.py` | AppData, Windows startup, single instance and lifecycle. |
| `build.bat`, `CustomAlarmClock.spec` | Native Windows single-file packaging and Qt/FFmpeg collection. |
| `scripts/package_source.py` | Reproduce the clean source ZIP. |

Primary implementation references: [Qt QMovie](https://doc.qt.io/qtforpython-6/PySide6/QtGui/QMovie.html), [Qt QMediaPlayer](https://doc.qt.io/qtforpython-6/PySide6/QtMultimedia/QMediaPlayer.html), [Qt for Windows](https://doc.qt.io/qt-6.12/windows.html), and [PyInstaller](https://pyinstaller.org/en/stable/).
