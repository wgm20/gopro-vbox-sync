# Six 7 branding

The owner supplied the existing white Six 7 artwork for use in this project. The built-in image generation tool replaced its baked-in grey checkerboard with a navy background. The original source file was left unchanged.

The selected artwork is `goprovbox/assets/brand.png`. `scripts/build_release.py` packages it as `icon.png` and a Windows `icon.ico` containing 16, 24, 32, 48, 64, 128 and 256-pixel sizes. The download page uses copies in `docs/brand.png` and `docs/favicon.ico`.

Final image-edit prompt:

```text
Use case: precise-object-edit
Asset type: square Windows application icon and branding badge.
Input image: the original supplied PNG is the edit target.
Replace ALL the grey checkerboard with one completely flat, opaque dark navy background, exact color #172d42. Do NOT make a transparent image. Preserve the white SIX lettering, the 7, every drip and the white outline surrounding the 7, keeping the existing design faithfully. The inside of the outlined badge and all gaps between letters must also be completely flat #172d42. White logo only, flat navy background everywhere else. Clean smooth edges; NO texture, NO speckles, NO checkerboard residue, NO thin dark outlines around white strokes, NO glow, no shadow, no gradient. Center the complete original logo with balanced 8% outer padding in a square image. Do not redesign the lettering, add or delete drips, or add any new elements. Opaque navy square.
```
