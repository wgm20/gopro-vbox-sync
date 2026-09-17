# Third-party notices

The MIT licence at the repository root covers the app code, documentation and original synthetic practice media. It does not relicense third-party software.

| Component | Distribution / notice |
|---|---|
| Python runtime | Bundled in the Windows app; [Python licence](goprovbox/assets/licenses/Python.txt) |
| Tcl / Tk | Bundled with the GUI; [Tcl](goprovbox/assets/licenses/Tcl.txt) and [Tk](goprovbox/assets/licenses/Tk.txt) |
| Pillow and native dependencies | Bundled graphics support; [combined licence notices](goprovbox/assets/licenses/Pillow.txt) |
| OpenSSL | Python HTTPS support; [Apache licence](goprovbox/assets/licenses/OpenSSL.txt) |
| mimalloc | Python allocator; [MIT licence](goprovbox/assets/licenses/mimalloc.txt) |
| PyInstaller | App packaging and bootloader; [licence with distribution exception](goprovbox/assets/licenses/PyInstaller.txt) |
| Inno Setup | Installer builder, [licence](https://jrsoftware.org/files/is/license.txt); compiler is not bundled |
| FFmpeg / FFprobe | Separate optional direct download from [Gyan's official release](https://github.com/GyanD/codexffmpeg/releases/tag/8.1.2); GPLv3 build, not included in installer or portable ZIP |

Portions of this software are copyright © The FreeType Project (www.freetype.org). All rights reserved. FreeType is used under its FreeType licence through Pillow; its notice appears in Pillow's combined licence file.

FFmpeg's LICENSE and README remain beside its downloaded executables. Gyan's release provides build information and its FFmpeg source revision. The app calls FFmpeg as a separate process. The downloader pins release URL, byte length and published SHA-256. Redistributing your own combined package with FFmpeg would require addressing that build's separate licence and corresponding-source obligations; this release does not distribute it.

VBOX Video Setup, Circuit Tools, `.VVHSN` scenes and their artwork remain separate user-provided software/data. No Racelogic library, encryption key or scene artwork is included. The app's scene loader uses the user's locally installed library. Review applicable vendor terms for your use and distribution; this project does not grant rights to vendor materials.

GoPro and VBOX/Racelogic names are used only to describe inputs and related software. No affiliation or endorsement is claimed.
