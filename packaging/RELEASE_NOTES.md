## Downloads

| Platform | File |
| --- | --- |
| Windows 10/11 (64-bit) | `MovieMerger-<version>-windows-x64.zip` |
| macOS 12+ (Intel; Apple Silicon Macs run it through Rosetta 2) | `MovieMerger-<version>-macos-x64.zip` |
| Linux x86-64 (glibc 2.35+, e.g. Ubuntu 22.04+, Debian 12+) | `MovieMerger-<version>-linux-x64.tar.gz` |

Everything is included, ffmpeg too: nothing else needs installing. One executable is both the desktop app
(start it without arguments) and the command-line tool (start it with arguments, e.g.
`MovieMerger streams session.xdf`).

### First start

The builds are not signed with a paid developer certificate, so the operating system asks once.

* **Windows:** unzip and run `MovieMerger\MovieMerger.exe`. If SmartScreen warns, choose
  *More info* > *Run anyway*.
* **macOS:** unzip and move `MovieMerger.app` to *Applications*. On first start macOS refuses it; open
  *System Settings* > *Privacy & Security* and click *Open Anyway*, or run
  `xattr -dr com.apple.quarantine /Applications/MovieMerger.app` once. The command-line tool is
  `/Applications/MovieMerger.app/Contents/MacOS/MovieMerger`.
* **Linux:** `tar xzf MovieMerger-*-linux-x64.tar.gz` and run `MovieMerger/MovieMerger`. Qt needs the
  X11 cursor library: `sudo apt install libxcb-cursor0` (Debian/Ubuntu) or `sudo dnf install xcb-util-cursor`
  (Fedora).

Every archive contains `LICENSE.txt` and `THIRD-PARTY-NOTICES.txt` with the licenses of the bundled software.
