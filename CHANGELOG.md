# Release notes

## 1.6.0b2 — match the VBOX track-map colour

- Draws the GPS track outline in yellow/gold, matching the original VBOX recording, and removes the added dark border. The scene's position marker is retained.
- Uses the corrected styling in previews and new full-scene exports; existing videos need to be exported again. Telemetry, map proportions and synchronisation are unchanged.
- Checks rendered map and marker colours at landscape, square and portrait sizes, plus the map colour in an encoded video.

## 1.6.0b1 — aligned VBOX sound by default

- Uses sound from the original VBOX videos by default. Keep them beside the VBO files. The new **Sound** choice remembers VBOX or GoPro.
- Aligns each original video chapter using the VBO video timestamps and the GoPro GPS clock, including prebuffer, trimming, fractional frame rates and drift in both video and audio clocks. Burned-in gauge readings are not timing anchors.
- Uses GoPro sound for uncovered periods, missing chapters, missing audio or unreliable VBOX timing; the report identifies the source files and fallback periods. Silence is used only when neither recording has sound. Ambiguous overlapping VBOX sources stop export.
- Prepares and verifies the complete sound track, then attaches it without altering encoded video frames. This avoids truncation of the audio tail when the video is trimmed to an exact frame count.
- Tests decoded waveform timing, independent clock drift, chapter boundaries, gaps, nonzero timestamps, cancellation, changed sources, unchanged video packets, preferences and the installed app. A private Silverstone recording was also used to check sound throughout a full run and a cropped scene export.

## 1.5.0b5 — round scene readouts correctly

- Rounds whole-number text in both four-channel and full-scene overlays, matching observed recorder readouts instead of truncating fractional values.
- Retains full precision in telemetry, interpolation and gauge bars; decimal text formatting and GPS/video alignment are unchanged.
- Tests rendered readouts for speed and RPM, other numeric formats, missing readings and unchanged timeline values.

## 1.5.0b4 — reliable frame timing with older video tools

- Fixes custom-crop/overlay exports rejected with “Encoded video frame rate differs from the source” under FFmpeg 7.
- Supplies the encoder frame timebase and each packet’s duration explicitly, preserving presentation/decode timestamps and B-frame order. Keeps strict frame-rate and frame-count validation.
- Adds real-video regression tests for complete final-frame timing, decoded frame order, integer/fractional rates, rotation, full-length and trimmed exports. Runs the full test suite with both FFmpeg 7.1.1 and 8.1.2.

## 1.5.0b3 — drag to frame your video

- Replaces the fixed GUI crop positions with **Frame & preview…**: drag a crop box over the full source image while watching the finished output, including the selected overlay.
- Retains the VBOX aspect ratio without stretching; supports vertical or horizontal positioning, arrow-key fine adjustment, centring, Apply and Cancel. Each video has its own framing.
- Uses the same source-pixel rectangle in preview, export and reports. Resolution changes and resizing the editor retain the crop position. Command-line crop presets remain supported.
- Defaults the desktop app to **Upright — no rotation** for every video, including videos not individually previewed. Manual rotation remains available.
- Tests dragging, bounds, rotation, resizing, cancellation, missing references, exported pixels/gauges/audio, reruns and the packaged editor.

## 1.5.0b2 — export only matching periods

- Adds **Only export video with VBOX data**, enabled by default. The app remembers the choice; untick it to retain full videos. The command-line equivalent is `--full-video`.
- Trims to matching periods with frame-aligned boundaries. Separate periods become separate numbered clips; continuous GoPro chapters retain their linked VBO data.
- Rebases VBOX video times and overlay playback to each clip while retaining GPS clock drift and full-session lap history. Audio is trimmed with the video.
- Uses the shorter duration for progress, space estimates and report ranges. The default folder ends in `Matched`, keeping earlier full-video exports separate.
- Tests first/last frame contents, sound alignment, telemetry gaps, fractional frame rates, nonzero source timestamps, adjacent chapters, cropping/overlays, opt-out and reruns.

## 1.5.0b1 — VBOX-shaped video cropping

- Adds a Crop choice for each GoPro video: No crop, cut off top, cut off bottom, or centre crop. The preview pane updates when the choice changes.
- Uses the display aspect ratio of the original VBOX videos linked to the overlapping data, including pixel shape and rotation. Keep those videos alongside the VBO files. Missing or conflicting reference shapes leave uncropped processing available.
- Corrects rotation before cropping, then scales and lays out overlays within the finished frame. Wider sources trim both sides equally. Cropped exports use a separate default output folder.
- Preview and export share crop coordinates; audio, frame cadence, GPS alignment and VBO telemetry are retained. Real-video tests check frame contents, rotation, overlays, missing/changed references and safe reruns.

## 1.4.0b5 — shortcut icon refresh

- Desktop and Start menu shortcuts point directly to the Six 7 icon file, avoiding the old executable icon cached by Windows after an upgrade.

App behaviour and video processing are unchanged.

## 1.4.0b4 — Help menu shortcuts

- Adds Help → Download page for the latest public installer.
- Adds Help → Uninstall app, using this installed copy's own uninstaller with its normal confirmation. Uninstall is unavailable during processing. Portable copies show removal instructions.
- Clarifies that video-tools setup downloads both FFmpeg and FFprobe.
- Tests missing-tools setup, cancellation and resuming, plus uninstall lookup, launch failures and active-work protection. Installer checks verify that the packaged app finds its uninstaller.

Video processing is unchanged.

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
