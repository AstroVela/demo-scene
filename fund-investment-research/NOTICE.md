# Synthetic Fixture and Runtime Notices

This demo does not commit generated fixture audio, PDFs, PNGs, model weights, or
model outputs. `fixture` creates synthetic assets at runtime and writes them to
the configured MinIO bucket.

Runtime asset generation uses:

- **eSpeak NG** through pyttsx3 for synthetic speech. The installed Debian
  package identifies the upstream project as
  <https://github.com/espeak-ng/espeak-ng> and the main project license as
  GPL-3.0-or-later. See the distribution's `espeak-ng-data/copyright`.
- **Noto Sans CJK** for document text. The installed Debian package identifies
  the font license as SIL Open Font License 1.1 and the upstream project as
  <https://github.com/notofonts/noto-cjk>.
- **DejaVu Sans Mono** for synthetic document metadata. The installed Debian
  package uses the Bitstream Vera / DejaVu font terms documented in
  `fonts-dejavu-core/copyright`.
- **ffmpeg** to convert the generated WAV to 16 kHz mono, and **Poppler
  `pdftoppm`** to render the synthetic PDF page for OCR.

The Whisper, RapidOCR, ONNX Runtime, Qwen, Ray, DuckDB, Vane, Pillow, MinIO and
PostgreSQL components are runtime dependencies; this demo does not redistribute
their model weights or service binaries. Operators are responsible for
reviewing the licenses of the exact runtime builds and model weights they use.

All company names, product names, identifiers, documents, audio scripts and
business values in this demo are fictional and generated solely for
demonstration. They are not investment advice or real research material.
