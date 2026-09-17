# Release notes

## 1.4.0b1 — first public beta

- Windows installer and portable app; no separate Python installation.
- MIT-licensed source and an explicit public source archive.
- First-run guidance, offline illustrated help and two synthetic practice recordings.
- Optional verified FFmpeg/FFprobe setup, stored for the current user.
- Help links for updates, source, support and licences.
- Installed-app self-test covering real GPS parsing, partial overlap, selection and overlay export.

Includes the existing GPS matching, orientation checks, proportional scene overlays without rear-camera footage, and per-video Include checkboxes.

**Known limitation:** Circuit Tools 3 can reject generated VBO files with an authenticity/checksum error and may close. This is unresolved. Re-encoding is not a proven fix. All current output modes encode video; Full resolution preserves dimensions, not the original compressed stream.

Windows x64 only. Unsigned installer. Full scenes require supported VBOX scene data and a separately installed VBOX Video Setup library. No real recordings or vendor artwork are distributed.
