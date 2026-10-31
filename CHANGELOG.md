# Release notes

## 1.6.0b16 — released 2026-10-31

- Replaces individual crop choices with one shared crop for all ticked export videos. **Apply to selected videos** applies it to the selected batch; newly ticked files inherit the same crop, and turning **Crop to VBOX** off affects the whole selection. Different crops require separate exports.
- Renames the checked-only selector to **Preview video**. It chooses which selected file to inspect, while each finished **Export preview** still shows the shared crop. The framing editor states **Crop applies to all selected videos**.
- Removes the obsolete excluded-file crop warning and keeps the main window focused on one crop choice for the selected batch. Source selection, lap details, Options and scene import remain available.

## 1.6.0b15 — released 2026-10-31

- Adds **Export video**, containing only ticked, matched files, as the target for rotation, framing and the main preview. One file is selected automatically when it is the only choice; with several, the list or an included table row selects it. Highlighting an excluded row leaves the export target unchanged, while Details continues to inspect any highlighted row.
- Replaces the main camera image with a finished **Export preview** still, using the same crop, rotation, output resolution and overlay rendering as export. The heading shows output dimensions. Settings and scene changes refresh it directly; rendering errors show an explanation without falling back to an unfinished camera image.
- Clears the export preview and disables rotation/framing when no matched files are ticked. Discards a framing edit if its target or inclusion changes before Apply. Choosing a scene refreshes the main preview without opening the crop editor.
- Retains per-file **Apply to this video**, the excluded-crop confirmation and the earlier source, lap-selection and scene-import features.

## 1.6.0b14 — released 2026-10-31

- Fixes a selection mismatch where toggling **Include** could leave another file in the preview. Clicking its checkbox or pressing Space now selects, focuses and previews the file whose inclusion changes.
- Shows the filename and **Included** or **Not included** status in the main preview. The framing editor names the file being edited and uses **Apply to this video** to clarify that framing belongs to that file.
- Asks **Export without the saved crop?**, with **No** selected by default, when enabled crops exist only on files excluded from export. This lets users review their selection; it does not include a file automatically or copy its crop to another file.
- Retains the 1.6.0b13 source-folder, lap-selection, Options and scene-import features below.

## 1.6.0b13 — released 2026-10-30

- Publishes the improvements since 1.6.0b4: separate GoPro/SD-card sources, recording dates and per-file lap times, red-star lap markers, the **Options** menu, new supported `.VVHSN` imports and reusable `.gvscene` packages. The development milestones below describe these changes.
- Makes release validation consistent when Windows supplies a temporary folder through a short (8.3) alias. Test fixtures and the installed smoke test's destination comparison use canonical paths. Normal app scanning and export behaviour are unchanged.

## 1.6.0b12 — development milestone included in 1.6.0b13

- Corrects two Windows test fixtures that compared short (8.3) paths with canonical paths. Production path handling already uses canonical paths; this change fixes release validation without changing app behaviour.

## 1.6.0b11 — development milestone included in 1.6.0b13

- Moves **Resolution**, **Overlay**, **Sound**, **Only export video with VBOX data** and **Include GoPro subfolders** into **Options** beside **Help**. The same menu contains **Details…** and **What do red stars mean?**, keeping the main window focused on choosing, previewing and creating videos.
- Starts **Include GoPro subfolders** ticked for desktop users, including those with an older saved preference that disabled it, and remembers new choices. Removes the folder-reset helper text from the main window.
- Keeps **Scan for matches** and its explanation beneath both source paths. Clearing the GoPro path still restores the VBOX folder; separate locations remain remembered. Advanced command-line scans retain `--no-gopro-recursive`.
- Adds separate VBOX and GoPro source folders, including SD-card subfolders, without copying footage. GoPro videos defaults to the VBOX folder until a separate path is set; source recordings remain unchanged and duplicate filenames are reported.
- Adds GPS recording dates, local/UTC start times and sortable **Best in file** times before conversion. Only complete laps wholly inside each file qualify; a separate red star marks whole-run fastest-lap footage, including both parts of a split lap. Details retains the whole-run best and explains missing times.
- Imports new supported `.VVHSN` scenes through the installed VBOX Video Setup public importer and its .NET 8 Windows Desktop Runtime. The app supplies its own helper; users do not need PowerShell 7 or a .NET SDK. Supports more older scene layouts while reporting unsupported content.
- Adds reusable `.gvscene` scene packages containing artwork and fonts from a previous import, for full-scene previews and exports without VBOX Video Setup. Vendor libraries and private scenes are not bundled.

## 1.6.0b10 — development milestone

- Replaces the repeated **Run best** with **Best in file**: the fastest complete VBOX lap wholly within each GoPro file's GPS time range. Laps split across files do not supply a per-file time; **—** means no complete lap is available.
- Uses a separate red star to mark footage from the whole-run fastest lap. Both files are marked if that lap crosses a chapter boundary, independently of their per-file best times. Details retains the whole-run best and explains split-lap coverage.
- Keeps date/start columns, local/UTC display, folder defaults, **Scan for matches** and scene import.

## 1.6.0b9 — development milestone

- Groups the VBOX and GoPro paths under **Choose recording folders**, aligns their Browse buttons and places **Scan for matches** beneath both paths. The accompanying note explains that matching comes before choosing videos to convert.
- Retains the automatic GoPro folder default, remembered separate sources, SD-card scanning, run-selection details and scene import.

## 1.6.0b8 — development milestone

- Removes the **Same as VBOX folder** checkbox. **GoPro videos** automatically uses the chosen VBOX folder until a separate GoPro location is selected or typed, and the field stays editable.
- Remembers a separate GoPro folder across app restarts without changing it when the VBOX folder changes. Clearing the GoPro field restores the VBOX folder when scanning or choosing another VBOX folder. Existing folder preferences are retained.
- Keeps **Include GoPro subfolders**, SD-card scanning, run-selection details and scene import available.

## 1.6.0b7 — development milestone

- Adds GPS-based recording date and start time, local/UTC display and the fastest complete VBOX lap to the selection list before video conversion. Records start in chronological order and column headings can sort the list.
- Marks GoPro chapters containing footage from a run's fastest lap with **★**. The original repeated whole-run display is replaced by per-file best times and a separate red star in 1.6.0b10; Details retains the whole-run best and chapter coverage.
- Calculates lap details once per VBOX run during scanning. Missing start/finish lines, incomplete laps and telemetry gaps produce unavailable lap information rather than guessed results. Lap times use the overlay's reconstructed timing method.
- Retains new VVHSN import, saved scenes, separate GoPro sources, cropping and aligned VBOX sound.

## 1.6.0b6 — development milestone

- Imports new `.VVHSN` files through the installed VBOX Video Setup library's public scene importer. This replaces the incorrect direct call to its guarded low-level decryption method, so importing a new scene no longer depends on a previously saved copy.
- Runs the importer through a small app-owned .NET helper, using the editor's .NET 8 Windows Desktop Runtime. Users do not need PowerShell 7 or a .NET SDK. Vendor libraries and private artwork remain outside the distributed app.
- Supports common older scene layouts with needle dials, fewer than four driving channels, braking-G bars, alternate lap displays, clocks with seconds and resultant-G calculations. Scene expressions are never evaluated as code.
- Reports incompatible editor versions, missing runtime components and unsupported scene content clearly. Scene rendering still covers a tested subset; a successful file import does not guarantee every widget or data source can be rendered.
- Retains `.gvscene` saved scene packages, separate GoPro/SD-card sources, source preservation, cropping and aligned VBOX sound.

## 1.6.0b5 — development milestone

- Adds reusable `.gvscene` saved scene packages for full-scene previews and exports. A saved package contains the configuration, artwork and fonts from an earlier import and opens locally without the VBOX Video Setup library. Private scene packages are not bundled or published with the app.
- Extends the Scene picker to accept `.VVHSN` and `.gvscene`. Reports identify the selected saved scene and its contents remain unchanged during processing.
- Explains the signed-caller refusal encountered by the old low-level reader and restores reusable scene copies. The direct reader is replaced by the public scene importer in 1.6.0b6.

- Adds separate VBOX and GoPro folder choices, with an easy same-folder option and remembered locations. GoPro videos can be read directly from an SD card or any other folder without copying them beside the VBO files.
- Includes optional GoPro subfolder scanning for card layouts such as `DCIM/100GOPRO`. VBOX data and its original video chapters remain together; the default output folder stays under the VBOX folder.
- Adds command-line `--gopro-folder`, `--gopro-recursive` and `--no-gopro-recursive` options. Existing same-folder commands retain their top-level scan.
- Checks the actual source paths before export and reports duplicate GoPro filenames with their locations so conflicting clips cannot be mixed up.

## 1.6.0b4 — clean finished folders

- Finished exports contain only the MP4 videos and VBO data files needed by Circuit Tools.
- Keeps reports, preview images and diagnostic logs in the app's local data folder. The Report button and verified reruns still work, and report links open the exported VBO files.
- Copies and verifies supporting files before moving them out of an older export; video and telemetry files are retained unchanged. Failed exports still retain diagnostic information.

## 1.6.0b3 — export from long recording-folder paths

- Fixes the missing temporary audio-file error when exporting from a deeply nested recording folder. Shorter temporary folder and diagnostic log names leave room for aligned sound and video files within Windows path limits.
- Keeps temporary work on the destination drive and publishes the completed folder only after validation. Failed-export reports identify the intended output folder.
- Adds a regression that reproduces the previous failure, checks cropped exports with VBOX sound, and exercises the same long-path case in the installed app. Sound synchronisation, video and VBO filenames, and original recordings are unchanged.

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
