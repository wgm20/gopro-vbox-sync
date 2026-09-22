Free, open-source Windows app for matching GoPro GPS video with VBOX telemetry.

**Updated in 1.6.0b1:** Output now uses sound from your original VBOX video by default. Keep the VBOX MP4 files beside their VBO files. Sound is aligned through the VBO video references and GoPro GPS clock, including prebuffer, trimmed clips, chapter changes and clock drift. The **Sound** choice lets you select GoPro instead. GoPro sound fills missing VBOX coverage; the report identifies any fallback periods. Sound timing is tested using decoded recordings, including the end of each clip. The scene rounding and FFmpeg 7 fixes are included.

**Start here:** download **GoPro-VBOX-Sync-1.6.0b1-Setup.exe**, install, then choose **Help → Try practice recordings**. No Python, payment, activation or GitHub account is required. Video tools download on request (110 MB). An illustrated guide is included offline.

**Beta limitation:** Circuit Tools 3 can reject generated VBO files with an authenticity/checksum error and may close. The issue remains unresolved; re-encoding is not a proven fix. Test a short recording first. Originals are preserved.

Windows 10/11 x64. Installer is unsigned. Portable ZIP: extract the entire folder, then open GoProVBOXSync.exe. Source is MIT licensed. Full scene overlays need a supported scene and VBOX Video Setup installed separately.

SHA256SUMS.txt contains integrity checks for the downloadable packages. build-info.json records build versions; validation.json records the installed application's smoke test. Automated checks do not prove Circuit Tools acceptance or replace a fresh-PC trial.
