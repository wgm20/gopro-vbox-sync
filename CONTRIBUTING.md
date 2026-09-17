# Contributing

Issues and pull requests are welcome. Include the app version, Windows version, camera model, steps to reproduce and the exact error. Do not upload recordings, scene artwork or GPS logs unless you own the rights and intend to make them public. Reports can include personal paths and location data; review and redact them first.

Use synthetic fixtures for automated tests. Run `python -m unittest discover -s tests -v`; FFmpeg and FFprobe are needed for media integration tests. See BUILDING.md for packaging verification.

Preserve original files and exact VBO measurements. Never silently fabricate samples, bridge gaps, suppress checksum failures or label a generated file genuine. Keep UI text understandable without coding knowledge. Document unsupported scene features explicitly.

By submitting a contribution you agree to license it under this project's MIT licence. Contributions must not include proprietary vendor code, keys, unlicensed artwork or private recordings.
