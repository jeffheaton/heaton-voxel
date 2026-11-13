# Privacy Policy for Heaton Voxel

Effective 13 November 2026.

**Heaton Voxel collects no data of any kind.** No account, no sign-in, no
analytics, no telemetry, no crash reporting, no advertising identifiers. There
is nothing to opt out of, because there is nothing collected.

This policy covers the Heaton Voxel application for macOS and Windows and the
`heaton-voxel` library it is built on.

## Nothing leaves your computer

The app has no network code and no network permission. On macOS it is
sandboxed, and the only entitlements it requests are:

```
com.apple.security.app-sandbox
com.apple.security.files.user-selected.read-write
com.apple.security.files.bookmarks.app-scope
```

There is no `com.apple.security.network.client`, so the app cannot open a
network connection even if it tried. The library it is built on has no
dependencies at all — it reads and writes the save format with the Python and
.NET standard libraries — and makes no network calls either.

## What it reads and writes

Heaton Voxel opens Minecraft: Java Edition save folders that **you** choose
through the standard system file picker. It reads the world's files to show
them to you, and writes to them only when you save.

- The folder you pick is remembered as a security-scoped bookmark so the app
  can reopen it next time. The bookmark stays on your computer.
- Before the app replaces any file in a world, it copies the original into a
  backup kept in its own application data folder, so an edit can be undone.
  Backups are plain copies of your own world files. They stay on your
  computer, and you can delete them at any time.
- Settings — your render distance, interface size, saved places and recent
  folders — are stored in a local settings file.
- A sample world ships inside the app and is installed to its own data folder
  on first launch. It can be reset or removed from Settings.

None of this is transmitted anywhere. Your worlds are your files, on your
disk, and the app is one more program on your computer that opens them.

## Children

The app collects nothing, from anyone, at any age.

## Changes

If this policy ever changes, the revised version will be published here with a
new effective date. A change can only ever narrow what is described above,
because anything broader would require adding data collection to an app that
has none.

## Contact

Questions about this policy: <jeff@jeffheaton.com>

---

Heaton Voxel is not an official Minecraft product. It is not approved by or
associated with Mojang or Microsoft. Minecraft is a trademark of Mojang AB.
