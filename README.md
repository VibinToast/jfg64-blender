# jfg64: Jet Force Gemini levels in Blender

A Blender add-on (Blender 3.5 or newer) for making level geometry for
[JFG-Studio](https://github.com/VibinToast/JFG-Studio), the level and modpack editor for
[Jet Force Gemini: Recompiled](https://github.com/VibinToast/JetForceGemini-Recomp). It makes the common steps quick
and shows the game's budgets while you model. It writes nothing Studio can't also read from a plain glTF export.

**Full guide: [the jfg64 add-on](https://jfg.vibintoast.com/blender) and
[Modelling a level](https://jfg.vibintoast.com/blender/modelling) on the JFG Modding Wiki.**

## Install

1. Download `jfg64.zip` from the [Releases](https://github.com/VibinToast/jfg64-blender/releases) page (or zip the
   `jfg64` folder of this repo yourself, so the zip holds `jfg64/__init__.py`).
2. Blender › Edit › Preferences › Add-ons › Install…, pick the zip, and tick **jfg64: Jet Force Gemini levels**.
3. In the 3D View press N and open the **JFG** tab.

GitHub's green "Download ZIP" button gives a folder named `jfg64-blender-main`, which Blender can't load as an add-on:
use the release zip.

## What it does

- **Surface**: makes the active material Solid, Water, Lava, Kill (a pit floor), Collider (invisible but solid) or
  Nocollide (drawn, not solid). It puts the word in the material's name and sets the custom properties (`batchFlags`,
  `pass`) the game's track importer reads.
- **Vertex light**: bakes a sun and an ambient colour into the selected meshes' vertex colours (a `Col` byte colour
  attribute on face corners). A Sun lamp in the scene gives the direction. The game's levels are lit this way.
- **Budgets**: triangles, images (about three new ones fit the game's texture table), the farthest vertex (the game
  takes up to 32767 units from the origin) and the surfaces in use.
- **Markers**: empties at the 3D cursor that Studio turns into objects when it makes a level from the file:
  Startpoint (its arrow is the facing), Exit, Health, Regionpoint, or Other (set Type to any `stock:objtype/0x..`).
  Each has a Fields text (JSON) for its fields, e.g. `{"startPoint": 1}`.
- **Export for JFG-Studio**: a `.glb` with +Y up, custom properties, applied modifiers and vertex colours, no
  animation or lights.

Then in JFG-Studio: **File › New project…** with the `.glb` as Geometry, or **File › Import geometry…**.

## Test

Headless, with Blender installed:

```
blender --background --factory-startup --python <a script that imports jfg64, calls register(), builds a scene and calls jfg64.export(path)>
```

Studio's import check then reports what the game sees.

Nothing from the game is in this add-on or in what it exports: you model your own levels.
