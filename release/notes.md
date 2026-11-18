# GoPro VBOX Sync 1.6.0b18 — 18 November 2026

- Export the fastest complete lap covered by the ticked videos as one MP4 and its VBO. Choose another filmed lap, all matched footage or full videos beside **Create files**. The app remembers the opt-out and honours an existing full-video preference.
- A lap starts and ends at the recorded start/finish crossings. Continuous GoPro chapters join into one video with aligned sound, gauges and data. **Options → Lap padding** adds up to two or five seconds on either side within continuous coverage.
- The summary shows the run and lap time before encoding. If the selection contains no complete filmed lap, it shows the matched-footage fallback. The preview follows the footage for the chosen lap.
- Supported full scenes can display calculated gear from speed and RPM using arithmetic and comparisons. Missing inputs or a telemetry gap leave the reading unavailable.

**Start here:** download **GoPro-VBOX-Sync-1.6.0b18-Setup.exe**, install, then choose **Help → Try practice recordings**. No Python, payment, activation or GitHub account is required. Video tools download on request (110 MB); the illustrated guide works offline. Windows 10/11 x64. The installer is unsigned. For the portable ZIP, extract the entire folder before opening GoProVBOXSync.exe.

**Beta limitation:** Circuit Tools 3 can reject generated VBO files with an authenticity/checksum error and may close. This remains unresolved; re-encoding is not a proven fix. Test a short recording first. Originals are preserved, and the exported MP4 also plays in ordinary video players.

Processing stays on your computer. Finished folders contain MP4 and VBO files. Reports, previews and diagnostic logs stay in the app's local data folder, accessible through **Report**. Reports can include source paths and GPS data. Share scene packages only when you have permission to share their artwork and fonts. Source is MIT licensed.

SHA256SUMS.txt contains package integrity checks. build-info.json records build versions; validation.json records the installed application's smoke test. These checks do not prove Circuit Tools acceptance.
