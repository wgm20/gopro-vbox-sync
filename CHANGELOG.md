# Release notes

## 1.4.0b3 — Six 7 branding

- Adds the supplied Six 7 artwork to the app header, welcome screen, download page and offline guide.
- Uses the matching navy-and-white icon for the app, installer, desktop and Start menu shortcuts, and browser tab.
- Packages Windows icons at seven sizes from 16 to 256 pixels.

Video processing is unchanged.

## 1.4.0b2 — clearer folder instructions

- Clarifies that GoPro MP4 files should be added to the folder containing your VBOX runs, alongside the original VBOX videos.
- Uses the same wording in the welcome screen, folder picker, quick-start guide and download page.
- Simplifies the installer introduction, welcome screen, About box and completion message by removing the repeated compatibility notices. Detailed limitations remain in the guide and reports.

Processing is unchanged from 1.4.0b1.

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
