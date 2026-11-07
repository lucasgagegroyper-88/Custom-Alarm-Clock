# Final engineering pass

- Made alarm saves, deletion/enable state changes, carousel updates and occurrence supersession transactional.
- Preserved active alarms on validation and injected write failures.
- Tolerated malformed nested settings sections without losing valid existing records.
- Fixed native corner close cleanup, tiny close-button geometry, finite-cycle timer cleanup and scaled-image reuse.
- Fixed stale audio pause transitions, stale notification ownership, notification media errors and GIF loop control.
- Restored the existing manual-banner re-alert/restart behavior without adding initial popup activation.
- Removed the duplicate toggle button, verbose explanations, obsolete overlay signals and unused imports/wrappers.
- Tightened navy editor layouts and retained every schedule/audio/media setting.
- Added isolated Qt, decoder, lifecycle, persistence, fault-injection and DST regressions.
- Corrected the standalone notification test's QApplication setup.
- Added an executable launch check to the Windows build, plus reproducible source packaging and resource profiling.
- Kept schema 2 and existing alarm/occurrence JSON formats; the new optional manual-banner preference is documented in ENGINEERING_REPORT.md.
