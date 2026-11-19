# OpenCV runtime path compatibility

The bundled `main` and `crawler` Python runtimes retain the upstream OpenCV
licenses and binaries. `scripts/build_windows_exe.py::patch_runtime_paths`
prepends a small compatibility check to each runtime's `cv2/config-3.py`.

On Windows, OpenCV's loader resolves its package directory to the full path.
This expands an NTFS short alias and can exceed the native DLL loader limit,
even when Python was started through the short alias. If that resolved path is
at least 240 characters, the patch uses the existing `cv2` directory alongside
the running embedded interpreter when present. This does not move/copy runtime
files, change native binaries, enable short names, or change OS settings.

The build patch is idempotent and rejects unrecognized loader configurations.
The final runtime manifest records the patched config hashes. Ordinary paths
and source installations of OpenCV are unchanged.
