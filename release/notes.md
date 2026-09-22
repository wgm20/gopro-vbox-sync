Free, open-source Windows app for matching GoPro GPS video with VBOX telemetry.

**Updated in 1.5.0b4:** Fixes the “Encoded video frame rate differs from the source” error when exporting cropped video with overlays using FFmpeg 7. The exporter now writes complete frame timing while retaining the original frame sequence and audio alignment. Tests cover both FFmpeg 7.1.1 and 8.1.2, including custom crops, overlays, rotation, fractional frame rates and trimmed clips. Drag-to-frame previews and the default upright rotation remain available.

**Start here:** download **GoPro-VBOX-Sync-1.5.0b4-Setup.exe**, install, then choose **Help → Try practice recordings**. No Python, payment, activation or GitHub account is required. Video tools download on request (110 MB). An illustrated guide is included offline.

**Beta limitation:** Circuit Tools 3 can reject generated VBO files with an authenticity/checksum error and may close. The issue remains unresolved; re-encoding is not a proven fix. Test a short recording first. Originals are preserved.

Windows 10/11 x64. Installer is unsigned. Portable ZIP: extract the entire folder, then open GoProVBOXSync.exe. Source is MIT licensed. Full scene overlays need a supported scene and VBOX Video Setup installed separately.

SHA256SUMS.txt contains integrity checks for the downloadable packages. build-info.json records build versions; validation.json records the installed application's smoke test. Automated checks do not prove Circuit Tools acceptance or replace a fresh-PC trial.
