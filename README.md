# jfg64: Jet Force Gemini levels in Blender

A Blender add-on (Blender 5.2 or newer) for making level geometry for
[JFG-Studio](https://github.com/VibinToast/JFG-Studio), the level and modpack editor for
[Jet Force Gemini: Recompiled](https://github.com/VibinToast/JetForceGemini-Recomp). It makes the common steps quick
and shows the game's budgets while you model. It writes nothing Studio can't also read from a plain glTF export.

**Full guide: [the jfg64 add-on](https://jfg.vibintoast.com/blender) and
[Modelling a level](https://jfg.vibintoast.com/blender/modelling) on the JFG Modding Wiki.**

## Install

1. Download `jfg64.zip` from the [Releases](https://github.com/VibinToast/jfg64-blender/releases) page (or zip the
   `jfg64` folder of this repo yourself, so the zip holds `jfg64/__init__.py`).
2. Blender › Edit › Preferences › Add-ons, the ▾ menu at the top right › **Install from Disk…**, pick the zip, and tick **jfg64: Jet Force Gemini levels**.
3. In the 3D View press N and open the **JFG** tab.

GitHub's green "Download ZIP" button gives a folder named `jfg64-blender-main`, which Blender can't load as an add-on:
use the release zip.

## What it does

- **Surface**: makes the active material Solid, Water, Lava, Kill (a pit floor), Collider (invisible but solid),
  Nocollide (drawn, not solid) or Ledge. It puts the word in the material's name and sets the custom
  properties (`batchFlags`, `pass`, `triangleFlags`) the game's track importer reads.
- **Lava needs ground under it**: Juno passes through lava (it doesn't hurt him), so a Lava surface over nothing is a
  hole he falls through. Put a solid floor a little below every lava surface (about 30: he wades in it).
- **Ledges**: the heroes grab an edge only where it is made a ledge: give a block's top the Ledge surface. The walls
  under its edges need nothing, as long as their top edges sit exactly on the top's corners: the game's track
  importer (jfgformats 0.3.0 and newer, which JFG-Studio uses) finds them. The hero catches the edge while falling
  past it. Everything else has no ledges. (Ledgewall flags such walls by hand, for older importers.)
- **Vertex light**: bakes a sun and an ambient colour into the selected meshes' vertex colours (a `Col` byte colour
  attribute on face corners). A Sun lamp in the scene gives the direction. The game's levels are lit this way, and
  the game draws only these colours, not a material's colour: tick **Tint by material** to bake each face's
  material colour (its Base Color) in.
- **Game textures**: paint with the game's own level textures, from JFG-Studio's library of your own ROM (Studio
  builds it in its data folder the first time you open a project). Pick one by colour or by the game level that uses
  it, see it as a thumbnail, and **Use on active object**. A level painted with them carries none of them: Studio
  names them by number and each player's game shows them. Don't edit their pixels: an edited copy is a new image,
  and the game's art can't go in a pack (Studio refuses it). Most of the game's textures don't repeat (its levels map them once per polygon): the export cuts faces using them along their repeat lines, so they tile anyway.
- **Budgets**: triangles, game textures (not carried), images (each becomes a new texture of the game's, at most 2048 pixels: 64 x 32 ...; bigger
  ones are shrunk), the farthest vertex (the game
  takes up to 32767 units from the origin) and the surfaces in use.
- **Markers**: empties at the 3D cursor that Studio turns into objects when it makes a level from the file:
  Startpoint (its arrow is the facing), Exit, Health, Regionpoint, or Other (set Type to any `stock:objtype/0x..`).
  Each has a Fields text (JSON) for its fields, e.g. `{"startPoint": 1}`.
- **Export for JFG-Studio**: a `.glb` with +Y up, custom properties, applied modifiers and vertex colours, no
  animation or lights.

Then in JFG-Studio: **File › New project…** with the `.glb` as Geometry, or **File › Import geometry…**.

## Scripting

Everything the panel does is also a function, for scripts and AI agents (through a Blender MCP server):
`set_surface(material, kind)`, `bake_light(mesh, matrix_world, sun_dir, sun_colour, ambient, tint=False)`,
`bake_scene(objects, tint=None)`, `budgets(objects)`, `export(path, objects=None, collection=None)` and
`add_marker(context, kind)`; the game textures: `library_folder()`, `game_textures(words, level, size, limit)` and
`game_texture_material(tex_id, surface)`; organic shapes: `terrain(...)` (ground from layered noise: hills, ridges
or terraces, with flat clearings, carved paths, a rim of hills round the edge and walkable slopes),
`terrain_report(obj)`, `rock(...)` (a lumpy boulder), `ground_height(obj, x, y, below=None)` (under a roof, pass `below` for the floor) and
`scatter_points(obj, count, ...)` (spots on its gentle ground); levels laid out as the game's are: `layout(name, rooms, links, floor, wall, ceiling)` (rooms of their own shapes and floor heights, joined by tunnels, corridors and canyons that cut doorways in them, with the game's own sizes) and `layout_report(obj)`. `export(collection=...)` works from any scene: it shows the collection's scene for
the export and goes back, and nothing of another scene goes out. `budgets` names the images the game would
shrink (`shrunk`). `help(jfg64)` lists them with the spaces and units each one takes; `export` returns
what it wrote (path, objects, triangles). `jfg64.__version__` says which version is loaded: after updating the
add-on, restart Blender, as a running Blender keeps the version it started with.

## Test

Headless, with Blender installed:

```
blender --background --factory-startup --python <a script that imports jfg64, calls register(), builds a scene and calls jfg64.export(path)>
```

Studio's import check then reports what the game sees.

Nothing from the game is in this add-on or in what it exports: you model your own levels.

## License

GPL-2.0-or-later, the licence Blender uses: see [LICENSE](LICENSE). You may use it under version 2 of the GNU General
Public License or any later version.
