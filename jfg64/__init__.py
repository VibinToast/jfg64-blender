"""jfg64: a Blender add-on for making Jet Force Gemini levels for JFG-Studio (phase 2, task S21).

In the 3D View's sidebar (N), tab "JFG":
- Surface: makes the active material water, lava, a kill floor, an invisible collider or decoration (the material-name
  words and flags the game's track importer reads, docs/BLENDER_GUIDE.md).
- Light: bakes a sun and an ambient light into the selected meshes' vertex colours (the game's levels are lit by
  vertex colours).
- Budgets: triangles, new images, the vertex range, against what the game takes.
- Markers: start points and other objects as empties; Studio places them when it imports the file.
- Export: a .glb with the settings Studio needs.

Studio checks every file with the game's own importer; this add-on only makes the common things quick.

Scripting (an AI agent through a Blender MCP server, or any script): `import jfg64`, then
- set_surface(material, kind): kind is one of SURFACES (solid, water, lava, kill, collider, nocollide, ledge): a
  "ledge" top makes its edges grabbable where walls meet them exactly, sharing the top's corners (the track importer,
  jfgformats 0.3.0 and newer, finds and flags those walls; "ledgewall" flags them by hand for older importers);
- bake_light(mesh, world_matrix, sun_dir, sun_colour, ambient, tint=False): one mesh; bake_scene(objects, tint=False):
  several, with the panel's settings and the scene's Sun lamp;
- budgets(objects) -> {"triangles", "images", "far", "surfaces"};
- export(path, objects=None, collection=None) -> {"path", "objects", "triangles"}: the visible objects, or only
  these objects / this collection;
- add_marker(context, kind, name=None);
- game textures (the game's own level textures, from JFG-Studio's library of the player's ROM: a level painted with
  them carries none, each player's game shows them): library_folder(), game_textures(words=None, level=None,
  size=None, limit=None) -> [{"id", "width", "height", "words", "levels", "path"}], game_texture_material(tex_id,
  surface="solid") -> a material showing it. Studio names those images by number when it builds the pack. Don't
  edit their pixels: an edited copy is a new image, and the game's art can't go in a pack;
- organic shapes instead of boxes: terrain(...) -> a ground mesh from layered noise (hills, ridges, terraces), with
  flat clearings, carved paths, a rim of hills round the edge and its slopes kept walkable (terrain_report says how
  much); rock(...) -> a lumpy boulder (cover for squads); scatter_points(terrain, count, ...) -> spots on its
  walkable ground for rocks, pickups or Studio objects.
Check `jfg64.__version__` against what a guide asks for; after updating the add-on, restart Blender (a running
Blender keeps the version it loaded).
Lava: Juno passes through it (unhurt), so every Lava surface needs a solid floor a little below it (about 30).
Spaces: Blender's own (Z up, 1 unit = 1 game unit); the export turns it into the game's Y up. Colours are RGB 0-1.
The game takes its colour from the vertex colours alone (the "Col" layer): a material's base colour is not used, so
use bake_light's tint to carry it into the bake.
"""

import glob
import json
import math
import os

import bpy
import bpy.utils.previews
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, StringProperty
from mathutils import Vector

__version__ = "0.4.0"  # bl_info's version too: raise both with every change (scripts check it)

bl_info = {
    "name": "jfg64: Jet Force Gemini levels",
    "author": "VibinToast",
    "version": (0, 4, 0),
    "blender": (5, 2, 0),
    "location": "View3D > Sidebar > JFG",
    "description": "Surfaces, game textures, vertex light, budgets and markers for JFG-Studio levels",
    "category": "Import-Export",
}

# Surface presets: (material-name word, batch flags, render pass, blend). Ground and walls are 0x1008 in stock tracks.
# The importer ORs each name word's flag into batchFlags, so a kill floor keeps 0x1008 here and gets its 0x80000000
# from the word "kill": Blender's integer properties are signed 32-bit and can't hold 0x80001008.
SURFACES = {
    "solid": ("", 0x1008, "opaque", "OPAQUE"),
    "water": ("water", 0x1200D, "translucent", "BLEND"),
    "lava": ("lava", 0x9008, "opaque", "OPAQUE"),
    "kill": ("kill", 0x1008, "opaque", "OPAQUE"),
    "collider": ("collider", 0x1408, "opaque", "OPAQUE"),
    "nocollide": ("nocollide", 0x1008, "opaque", "OPAQUE"),
    "ledge": ("ledge", 0x1008, "opaque", "OPAQUE"),
    "ledgewall": ("ledgewall", 0x1008, "opaque", "OPAQUE"),
}
# Triangle flags a surface sets (extras.triangleFlags): 0x80 no collision. A grabbable ledge (the decomp, port
# request 10) is a top with 0x02 and the wall under each of its edges with 0x01, linked across the edge: the wall's
# top on the top's own corners, both in one piece of the level.
TRIANGLE_FLAGS = {"nocollide": 0x80, "ledge": 0x02, "ledgewall": 0x01}
WORDS = {"water", "lava", "damage", "kill", "collider", "hidden", "nocollide", "ledge", "ledgewall"}
VERTEX_MAX = 32767
IMAGE_PIXELS = 2048  # a new image is stored as one 16-bit texture of at most 4 KB: bigger ones are shrunk

# Marker types: (Studio object type, default fields, empty display, size)
MARKERS = {
    "startpoint": ("stock:objtype/0xD", {"player": 0, "startPoint": 0, "yaw": 0}, "SINGLE_ARROW", 40.0),
    "exit": ("stock:objtype/0xB", {"radius": 60, "startPoint": 0}, "CIRCLE", 60.0),
    "health": ("stock:objtype/0xA9", {"amount": 0}, "SPHERE", 15.0),
    "regionpoint": ("stock:objtype/0x5", {"region": 1, "corner": 0}, "PLAIN_AXES", 20.0),
    "other": ("", {}, "CUBE", 20.0),
}


def _words(name):
    return set("".join(ch if ch.isalnum() else " " for ch in name.lower()).split())


def surface_of(mat):
    words = _words(mat.name)
    for key in ("water", "lava", "kill", "collider", "nocollide", "ledgewall", "ledge"):
        if key in words:
            return key
    if "damage" in words:
        return "lava"
    if "hidden" in words:
        return "collider"
    return "solid"


def set_surface(mat, kind):
    """Renames the material with the surface's word (dropping other surface words) and sets the custom properties
    the importer reads."""
    word, flags, render_pass, blend = SURFACES[kind]
    parts = [p for p in mat.name.replace("-", "_").replace(" ", "_").replace(".", "_").split("_")
             if p and p.lower() not in WORDS]
    base = "_".join(parts) or "surface"
    mat.name = f"{base}_{word}" if word else base
    mat["batchFlags"] = flags
    mat["pass"] = render_pass
    mat.blend_method = "BLEND" if blend == "BLEND" else "OPAQUE"
    if kind in TRIANGLE_FLAGS:
        mat["triangleFlags"] = TRIANGLE_FLAGS[kind]
    elif "triangleFlags" in mat:
        del mat["triangleFlags"]


class JFG_OT_surface(bpy.types.Operator):
    bl_idname = "jfg64.surface"
    bl_label = "Set surface"
    bl_description = "Make the active object's active material this kind of surface"
    bl_options = {"REGISTER", "UNDO"}
    kind: EnumProperty(items=[(k, k.title(), "") for k in SURFACES])

    def execute(self, context):
        obj = context.active_object
        mat = obj.active_material if obj else None
        if mat is None:
            self.report({"ERROR"}, "Select an object with a material")
            return {"CANCELLED"}
        set_surface(mat, self.kind)
        self.report({"INFO"}, f"{mat.name}: {self.kind}")
        return {"FINISHED"}


def material_colour(mat):
    """A material's base colour (RGB 0-1): its Principled BSDF's Base Color, else its viewport colour; white for
    none."""
    if mat is None:
        return (1.0, 1.0, 1.0)
    if mat.node_tree:
        for node in mat.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                return tuple(node.inputs["Base Color"].default_value[:3])
    return tuple(mat.diffuse_color[:3])


def bake_light(mesh, world_matrix, sun_dir, sun_col, ambient, layer="Col", tint=False):
    """Writes (ambient + sun * max(0, n.l)) into the byte colour layer `layer` on face corners, one colour per face.

    world_matrix: the object's matrix_world. sun_dir: the direction the light travels, in Blender's world space (Z up;
    (0, 0, -1) is straight down). sun_col, ambient: RGB 0-1. tint: multiply each face by its material's base colour
    (material_colour), as the game draws only the vertex colours."""
    attr = mesh.color_attributes.get(layer)
    if attr is None or attr.domain != "CORNER" or attr.data_type != "BYTE_COLOR":
        if attr is not None:
            mesh.color_attributes.remove(attr)
        attr = mesh.color_attributes.new(layer, "BYTE_COLOR", "CORNER")
    mesh.color_attributes.active_color = attr
    normal_matrix = world_matrix.to_3x3().inverted_safe().transposed()
    to_light = -Vector(sun_dir).normalized()
    tints = [material_colour(m) if tint else (1.0, 1.0, 1.0) for m in mesh.materials] or [(1.0, 1.0, 1.0)]
    for poly in mesh.polygons:
        n = (normal_matrix @ poly.normal).normalized()
        d = max(0.0, n.dot(to_light))
        t = tints[poly.material_index] if poly.material_index < len(tints) else (1.0, 1.0, 1.0)
        col = [min(1.0, (ambient[i] + sun_col[i] * d) * t[i]) for i in range(3)]
        for li in poly.loop_indices:
            attr.data[li].color = (*col, 1.0)
    mesh.update()


def scene_sun(scene):
    """The direction the scene's light travels: its Sun lamp's, else the panel's Sun direction (Blender space)."""
    sun = next((o for o in scene.objects if o.type == "LIGHT" and o.data.type == "SUN"), None)
    return (sun.matrix_world.to_3x3() @ Vector((0, 0, -1))) if sun else Vector(scene.jfg64.sun_direction), sun


def bake_scene(objects, tint=None, scene=None):
    """Bakes the meshes among `objects` with the panel's sun and ambient colours and the scene's Sun lamp (tint: the
    panel's setting when None). Returns how many meshes were lit."""
    scene = scene or bpy.context.scene
    s = scene.jfg64
    direction, _ = scene_sun(scene)
    meshes = [o for o in objects if o.type == "MESH"]
    for o in meshes:
        bake_light(o.data, o.matrix_world, direction, s.sun_colour, s.ambient, tint=s.tint if tint is None else tint)
    return len(meshes)


class JFG_OT_bake(bpy.types.Operator):
    bl_idname = "jfg64.bake_light"
    bl_label = "Bake vertex light"
    bl_description = "Light the selected meshes' vertex colours from a sun and an ambient colour"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        _, sun = scene_sun(context.scene)
        lit = bake_scene(context.selected_objects, scene=context.scene)
        if not lit:
            self.report({"ERROR"}, "Select the level's meshes")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Lit {lit} meshes" + (f" from {sun.name}" if sun else ""))
        return {"FINISHED"}


GAME_TEXTURE = "jfg_game_texture"  # an image's custom property: the game texture id it shows


def library_folder(scene=None):
    """JFG-Studio's game-texture library (the "textures" folder of the player's catalog, built from their own ROM
    by Studio, game_textures.py): the panel's folder when set, else the newest one in Studio's data folder. None
    when there is none yet (open the level in Studio, or run its build, once)."""
    scene = scene or bpy.context.scene
    folder = bpy.path.abspath(scene.jfg64.textures_folder) if scene.jfg64.textures_folder else ""
    if folder and os.path.isfile(os.path.join(folder, "index.json")):
        return folder
    roots = [os.path.join(os.environ.get("LOCALAPPDATA", ""), "JFG-Studio"),
             os.path.join(os.path.expanduser("~"), ".local", "share", "JFG-Studio"),
             os.path.join(os.path.expanduser("~"), "Library", "Application Support", "JFG-Studio")]
    found = [f for r in roots for f in glob.glob(os.path.join(r, "catalog", "*", "textures", "index.json"))]
    return os.path.dirname(max(found, key=os.path.getmtime)) if found else None


_index_cache = {}


def _library_index(folder):
    path = os.path.join(folder, "index.json")
    stamp = os.path.getmtime(path)
    if _index_cache.get("key") != (path, stamp):
        with open(path, encoding="utf-8") as f:
            _index_cache.update(key=(path, stamp), textures=json.load(f)["textures"])
    return _index_cache["textures"]


def game_textures(words=None, level=None, size=None, limit=None, scene=None):
    """The game's level textures, filtered: words (all of them: a colour such as "green", "brown", "grey", and
    "dark" / "mid" / "bright" / "vivid"), level (a game level id that uses it), size ((width, height)). Each is
    {"id", "width", "height", "alpha", "words", "levels", "path"}; JFG-Studio's game_textures tool shows them."""
    folder = library_folder(scene)
    if folder is None:
        raise RuntimeError("no game-texture library yet: JFG-Studio builds it from your ROM (open a project, or run "
                           "its game_textures tool, once)")
    want = set(words or [])
    out = []
    for key, t in _library_index(folder).items():
        if want - set(t["words"]) or (level is not None and level not in t["levels"]):
            continue
        if size is not None and (t["width"], t["height"]) != tuple(size):
            continue
        out.append({"id": t["id"], "width": t["width"], "height": t["height"], "alpha": t["alpha"],
                    "words": t["words"], "levels": t["levels"], "path": os.path.join(folder, t["file"])})
        if limit and len(out) >= limit:
            break
    return out


def game_texture_material(tex_id, surface="solid", name=None, scene=None):
    """A material (made once, then reused) that shows game texture `tex_id` from the library, as the given surface.
    The image is marked as the game's (GAME_TEXTURE): budgets counts it apart from new images."""
    folder = library_folder(scene)
    if folder is None:
        raise RuntimeError("no game-texture library yet: open a project in JFG-Studio once")
    path = os.path.join(folder, f"{int(tex_id):04X}.png")
    if not os.path.isfile(path):
        raise ValueError(f"game texture {int(tex_id):04X} is not a level texture of the library")
    mat_name = name or f"game_{int(tex_id):04X}"
    mat = bpy.data.materials.get(mat_name)
    if mat is None:
        image = next((i for i in bpy.data.images if i.get(GAME_TEXTURE) == int(tex_id)), None)
        if image is None:
            image = bpy.data.images.load(path, check_existing=True)
            image[GAME_TEXTURE] = int(tex_id)
        mat = bpy.data.materials.new(mat_name)
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = image
        tex.interpolation = "Closest"
        mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        if _library_index(folder).get(f"{int(tex_id):04X}", {}).get("alpha") not in (None, "none"):
            mat.node_tree.links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
    set_surface(mat, surface)
    return mat


# ---------------------------------------------------------------------------------------------- organic shapes

WALKABLE = 45.0  # degrees: the heroes walk up ramps this steep (55 is too steep; Studio's guide, "Scale")


def _perlin(x, y, seed):
    """2D gradient noise in -1..1 (numpy arrays x, y): Perlin's, with a permutation from `seed`."""
    import numpy as np
    rng = np.random.default_rng(seed)
    perm = np.tile(rng.permutation(256), 2)
    angles = rng.random(256) * 2 * math.pi
    gx, gy = np.cos(angles), np.sin(angles)
    xi, yi = np.floor(x).astype(int) & 255, np.floor(y).astype(int) & 255
    xf, yf = x - np.floor(x), y - np.floor(y)

    def grad(ix, iy, dx, dy):
        h = perm[perm[ix] + iy]
        return gx[h] * dx + gy[h] * dy

    u, v = xf * xf * xf * (xf * (xf * 6 - 15) + 10), yf * yf * yf * (yf * (yf * 6 - 15) + 10)
    n00, n10 = grad(xi, yi, xf, yf), grad(xi + 1, yi, xf - 1, yf)
    n01, n11 = grad(xi, yi + 1, xf, yf - 1), grad(xi + 1, yi + 1, xf - 1, yf - 1)
    a, b = n00 + u * (n10 - n00), n01 + u * (n11 - n01)
    return (a + v * (b - a)) * 1.4


def _fbm(x, y, seed, octaves, style="hills"):
    """Layered noise in about -1..1: hills (smooth), ridges (sharp crests, as rocky mountains), terraces (steps)."""
    total, amp, freq, norm = 0.0, 1.0, 1.0, 0.0
    for o in range(octaves):
        n = _perlin(x * freq, y * freq, seed + o * 101)
        if style == "ridges":
            n = 1.0 - 2.0 * abs(n)
        total = total + n * amp
        norm += amp
        amp *= 0.5
        freq *= 2.0
    total = total / norm
    if style == "terraces":
        import numpy as np
        total = np.round(total * 4) / 4 + (total * 4 - np.round(total * 4)) * 0.05
    return total


def _smoothstep(t):
    import numpy as np
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def terrain(name="terrain", size=(2000.0, 2000.0), step=50.0, height=150.0, scale=700.0, octaves=4, seed=0,
            style="hills", flat=(), paths=(), path_width=120.0, path_depth=0.0, rim=0.0, rim_width=300.0,
            max_slope=40.0, uv_size=256.0, material=None, location=(0.0, 0.0, 0.0), collection=None):
    """A ground mesh shaped by noise instead of boxes, for levels that feel like places.

    size (x, y) and step: the grid (keep step 40-80: every grid square is two triangles of the game's budget).
    height: the hills' height either side of 0; scale: how far apart they are (bigger = gentler); octaves: detail;
    seed: another seed, another landscape; style: "hills", "ridges" or "terraces".
    flat: clearings [(x, y, radius) or (x, y, radius, height)] levelled for start points, squads and buildings;
    paths: lines [[(x, y), ...], ...] smoothed into walkable tracks path_width wide (path_depth sinks them, a gully);
    rim: hills this high round the edge (rim_width deep), so the level ends in slopes instead of walls; slopes
    steeper than max_slope (degrees, below WALKABLE) are relaxed, except in the rim. UVs repeat every uv_size units
    (game textures are small and tile). Returns the object; terrain_report(obj) says how walkable it is."""
    import numpy as np
    sx, sy = float(size[0]), float(size[1])
    nx, ny = max(2, int(round(sx / step)) + 1), max(2, int(round(sy / step)) + 1)
    xs, ys = np.linspace(-sx / 2, sx / 2, nx), np.linspace(-sy / 2, sy / 2, ny)
    gx, gy = np.meshgrid(xs, ys)
    h = _fbm(gx / scale, gy / scale, seed, octaves, style) * height * 2.2  # noise rarely reaches 1: about +-height
    gentle = _fbm(gx / (scale * 2.5), gy / (scale * 2.5), seed + 7, 2) * height * 0.3
    for line in paths:
        pts = [(float(p[0]), float(p[1])) for p in line]
        dist = np.full(gx.shape, np.inf)
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            dx, dy = bx - ax, by - ay
            t = np.clip(((gx - ax) * dx + (gy - ay) * dy) / max(dx * dx + dy * dy, 1e-9), 0, 1)
            dist = np.minimum(dist, np.hypot(gx - ax - t * dx, gy - ay - t * dy))
        w = 1 - _smoothstep((dist - path_width / 2) / path_width)
        h = h * (1 - w) + (gentle - path_depth) * w
    for c in flat:
        cx, cy, r = float(c[0]), float(c[1]), float(c[2])
        level = float(c[3]) if len(c) > 3 else float(_fbm(np.array(cx / scale), np.array(cy / scale), seed,
                                                           octaves, style) * height)
        w = 1 - _smoothstep((np.hypot(gx - cx, gy - cy) - r) / (r * 0.6 + step))
        h = h * (1 - w) + level * w
    edge = np.minimum(np.minimum(gx + sx / 2, sx / 2 - gx), np.minimum(gy + sy / 2, sy / 2 - gy))
    wobble = _perlin(gx / (rim_width * 1.5), gy / (rim_width * 1.5), seed + 13)  # the rim winds, not a box
    rim_w = _smoothstep(1 - (edge + wobble * rim_width * 0.45) / max(rim_width, 1e-9)) if rim else np.zeros_like(h)
    limit = math.tan(math.radians(min(max_slope, WALKABLE))) * step
    for _ in range(60):  # relax slopes steeper than max_slope (not in the rim, where steep is the point)
        hp = np.pad(h, 1, mode="edge")
        nb = (hp[:-2, 1:-1] + hp[2:, 1:-1] + hp[1:-1, :-2] + hp[1:-1, 2:]) / 4
        steep = (np.maximum.reduce([abs(hp[:-2, 1:-1] - h), abs(hp[2:, 1:-1] - h), abs(hp[1:-1, :-2] - h),
                                    abs(hp[1:-1, 2:] - h)]) > limit) & (rim_w < 0.05)
        if not steep.any():
            break
        h = np.where(steep, h * 0.5 + nb * 0.5, h)
    h = h + rim * rim_w * (0.75 + 0.45 * _perlin(gx / (rim_width * 2), gy / (rim_width * 2), seed + 29))
    verts = [(float(gx[j, i]), float(gy[j, i]), float(h[j, i])) for j in range(ny) for i in range(nx)]
    faces = [(j * nx + i, j * nx + i + 1, (j + 1) * nx + i + 1, (j + 1) * nx + i)
             for j in range(ny - 1) for i in range(nx - 1)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    uv = mesh.uv_layers.new(name="UV")
    for poly in mesh.polygons:
        for li in poly.loop_indices:
            co = verts[mesh.loops[li].vertex_index]
            uv.data[li].uv = (co[0] / uv_size, co[1] / uv_size)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    (collection or bpy.context.scene.collection).objects.link(obj)
    if material is not None:
        mesh.materials.append(material)
    obj["jfg_terrain"] = json.dumps({"seed": seed, "style": style, "step": step, "height": height, "scale": scale})
    return obj


def terrain_report(obj):
    """{"triangles", "lowest", "highest", "walkable" (share of the ground no steeper than WALKABLE), "steepest"
    (degrees)} of a mesh, in world space."""
    m = obj.data
    walk, total, steepest = 0.0, 0.0, 0.0
    zs = [(obj.matrix_world @ v.co).z for v in m.vertices]
    normal_matrix = obj.matrix_world.to_3x3().inverted_safe().transposed()
    for p in m.polygons:
        n = (normal_matrix @ p.normal).normalized()
        angle = math.degrees(math.acos(max(-1.0, min(1.0, n.z))))
        total += p.area
        steepest = max(steepest, angle)
        if angle <= WALKABLE:
            walk += p.area
    return {"triangles": sum(len(p.vertices) - 2 for p in m.polygons), "lowest": round(min(zs)),
            "highest": round(max(zs)), "walkable": round(walk / total, 3) if total else 0.0,
            "steepest": round(steepest, 1)}


def rock(name="rock", radius=60.0, seed=0, roughness=0.35, squash=0.7, location=(0.0, 0.0, 0.0), material=None,
         collection=None, sink=0.25):
    """A lumpy boulder (an icosphere pushed about by noise), squashed to `squash` of its width and sunk `sink` of
    its height into the ground at `location`: cover squads take, a landmark, a step to jump from. Its
    location's z is the ground there (ground_height). Two subdivisions: about 320 triangles."""
    import bmesh
    import numpy as np
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=2, radius=1.0)
    co = np.array([v.co[:] for v in bm.verts])
    n = _perlin(co[:, 0] * 1.7 + co[:, 2] * 0.9 + seed, co[:, 1] * 1.7 - co[:, 2] * 0.6, seed) * 0.6 + \
        _perlin(co[:, 0] * 3.9, co[:, 1] * 3.9 + co[:, 2] * 2.1, seed + 3) * 0.4
    for v, k in zip(bm.verts, n):
        v.co *= radius * (1 + roughness * k)
        v.co.z *= squash
        v.co.z -= radius * squash * sink
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    uv = mesh.uv_layers.new(name="UV")
    for poly in mesh.polygons:
        for li in poly.loop_indices:
            c = mesh.vertices[mesh.loops[li].vertex_index].co
            uv.data[li].uv = ((c.x + c.y) / 128.0, c.z / 128.0)
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    obj.rotation_euler.z = (seed * 2.399) % (2 * math.pi)
    (collection or bpy.context.scene.collection).objects.link(obj)
    if material is not None:
        mesh.materials.append(material)
    return obj


def ground_height(obj, x, y):
    """The z of mesh `obj`'s surface under (x, y) in world space (the highest), or None off its edge."""
    from mathutils.bvhtree import BVHTree
    depsgraph = bpy.context.evaluated_depsgraph_get()
    tree = BVHTree.FromObject(obj, depsgraph)
    inv = obj.matrix_world.inverted()
    origin = inv @ Vector((x, y, 1e6))
    hit = tree.ray_cast(origin, (inv.to_3x3() @ Vector((0, 0, -1))).normalized())
    return (obj.matrix_world @ hit[0]).z if hit[0] is not None else None


def scatter_points(obj, count, seed=0, min_gap=150.0, max_slope=30.0, area=None, avoid=()):
    """Up to `count` spots [(x, y, z)] on mesh `obj`'s gentle ground (slope at most max_slope), min_gap apart,
    within area ((x0, y0), (x1, y1)) or the whole mesh, and away from `avoid` [(x, y, radius)]: for rocks, pickups,
    props or Studio objects (Blender x, y = the game's x, -z)."""
    import random
    from mathutils.bvhtree import BVHTree
    rnd = random.Random(seed)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    tree = BVHTree.FromObject(obj, depsgraph)
    box = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    (x0, y0), (x1, y1) = area or ((min(v.x for v in box), min(v.y for v in box)),
                                  (max(v.x for v in box), max(v.y for v in box)))
    inv = obj.matrix_world.inverted()
    out = []
    for _ in range(count * 40):
        if len(out) >= count:
            break
        x, y = rnd.uniform(x0, x1), rnd.uniform(y0, y1)
        if any(math.hypot(x - a[0], y - a[1]) < a[2] for a in avoid):
            continue
        if any(math.hypot(x - p[0], y - p[1]) < min_gap for p in out):
            continue
        loc, normal, _, _ = tree.ray_cast(inv @ Vector((x, y, 1e6)), (inv.to_3x3() @ Vector((0, 0, -1))).normalized())
        if loc is None:
            continue
        n = (obj.matrix_world.to_3x3().inverted_safe().transposed() @ normal).normalized()
        if math.degrees(math.acos(max(-1.0, min(1.0, n.z)))) > max_slope:
            continue
        w = obj.matrix_world @ loc
        out.append((round(w.x, 1), round(w.y, 1), round(w.z, 1)))
    return out


def _is_game_image(image):
    return image.get(GAME_TEXTURE) is not None


def budgets(objects):
    """What the game's importer will count: {"triangles", "images" (new images, each a new texture of the game's),
    "game" (game textures used, which the pack names by number and doesn't carry),
    "shrunk" (images the game will shrink: over IMAGE_PIXELS pixels or sides not a power of two), "far" (the farthest
    vertex coordinate from the origin, at most VERTEX_MAX), "surfaces" (kinds in use)}."""
    tris, images, far, shrunk, game = 0, set(), 0.0, set(), set()
    surfaces = set()
    for o in objects:
        if o.type != "MESH":
            continue
        m = o.data
        tris += sum(len(p.vertices) - 2 for p in m.polygons)
        for v in m.vertices:
            w = o.matrix_world @ v.co
            far = max(far, abs(w.x), abs(w.y), abs(w.z))
        for slot in o.material_slots:
            mat = slot.material
            if not mat:
                continue
            surfaces.add(surface_of(mat))
            if mat.use_nodes:
                for node in mat.node_tree.nodes:
                    if node.type == "TEX_IMAGE" and node.image and _is_game_image(node.image):
                        game.add(node.image[GAME_TEXTURE])
                    elif node.type == "TEX_IMAGE" and node.image:
                        images.add(node.image.name)
                        w, h = node.image.size
                        if w * h > IMAGE_PIXELS or w & (w - 1) or h & (h - 1):
                            shrunk.add(node.image.name)
    return {"triangles": tris, "images": len(images), "game": len(game), "shrunk": sorted(shrunk), "far": far,
            "surfaces": sorted(surfaces)}


def add_marker(context, kind, name=None):
    """An empty at the 3D cursor (kind: one of MARKERS) that Studio turns into an object when it makes a level from
    the file; its "jfg_fields" custom property (JSON) holds the object's fields."""
    jfg_type, fields, display, size = MARKERS[kind]
    empty = bpy.data.objects.new(name or kind, None)
    empty.empty_display_type = display
    empty.empty_display_size = size
    empty.location = context.scene.cursor.location
    if kind == "startpoint":
        empty.rotation_euler = (math.radians(90), 0, 0)  # the arrow points along the facing (+Y in Blender)
    empty["jfg_type"] = jfg_type
    empty["jfg_fields"] = json.dumps(fields)
    context.collection.objects.link(empty)
    return empty


class JFG_OT_marker(bpy.types.Operator):
    bl_idname = "jfg64.add_marker"
    bl_label = "Add marker"
    bl_description = "An empty at the 3D cursor that Studio turns into an object when it imports the file"
    bl_options = {"REGISTER", "UNDO"}
    kind: EnumProperty(items=[(k, k.title(), "") for k in MARKERS])

    def execute(self, context):
        empty = add_marker(context, self.kind)
        for o in context.selected_objects:
            o.select_set(False)
        empty.select_set(True)
        context.view_layer.objects.active = empty
        return {"FINISHED"}


class JFG_OT_export(bpy.types.Operator):
    bl_idname = "jfg64.export"
    bl_label = "Export for JFG-Studio"
    bl_description = "Export the visible level as a .glb with the settings JFG-Studio needs"
    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.glb", options={"HIDDEN"})

    def invoke(self, context, event):
        if not self.filepath:
            self.filepath = bpy.path.ensure_ext(bpy.path.abspath("//level") if bpy.data.filepath else "level", ".glb")
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        b = budgets(context.visible_objects)
        if b["far"] > VERTEX_MAX:
            self.report({"ERROR"}, f"a vertex is {b['far']:.0f} from the origin; the game takes {VERTEX_MAX}")
            return {"CANCELLED"}
        export(self.filepath)
        self.report({"INFO"}, f"Exported {self.filepath}: {b['triangles']} triangles, {b['images']} images")
        return {"FINISHED"}


def _scene_of(objects, collection):
    """The scene that holds what is exported: the collection's, else the objects' first scene."""
    for scene in bpy.data.scenes:
        if collection is not None and (collection == scene.collection
                                       or collection in scene.collection.children_recursive):
            return scene
        if collection is None and objects and objects[0].name in scene.objects:
            return scene
    return bpy.context.scene


def export(path, objects=None, collection=None):
    """A .glb for Studio: the visible objects of the window's scene, or only `objects` / the objects of `collection`
    (a Collection or its name), from whichever scene holds them: the window shows that scene for the export and
    goes back after. Only that scene's objects go out. The vertex colours are the "Col" layer bake_light writes.
    Returns {"path", "objects", "triangles"} for what was written."""
    coll = None
    if collection is not None:
        coll = bpy.data.collections[collection] if isinstance(collection, str) else collection
        objects = list(coll.all_objects)
    options = dict(filepath=path, export_format="GLB", export_extras=True, export_yup=True, export_apply=True,
                   export_normals=False, export_vertex_color="NAME", export_vertex_color_name="Col",
                   export_all_vertex_colors=False, export_animations=False, export_skins=False, export_morph=False,
                   export_cameras=False, export_lights=False)
    if objects is None:
        bpy.ops.export_scene.gltf(use_visible=True, **options)
        return _summary(path, bpy.context.visible_objects)
    objects = [o for o in objects if o is not None]
    window = bpy.context.window
    scene = _scene_of(objects, coll)
    old_scene = window.scene if window else None
    if scene != bpy.context.scene:
        if window is None:
            raise RuntimeError(f"the objects are in scene {scene.name!r}, not the current one, and there is no "
                               "window to show it in: export from that scene")
        window.scene = scene  # a plain switch (a context override of the scene can crash Blender)
    try:
        layer = bpy.context.view_layer
        layer.update()  # newly linked objects in, removed ones out
        here = {o.name for o in layer.objects}
        objects = [o for o in objects if o.name in here]  # objects removed or excluded from the view layer: left out
        selected = [o for o in layer.objects if o.select_get()]
        active = layer.objects.active
        # The exporter takes the selected objects of every scene: other scenes' selections are put aside too.
        elsewhere = [(o, vl) for sc in bpy.data.scenes if sc != scene for vl in sc.view_layers
                     for o in vl.objects if o.select_get(view_layer=vl)]
        try:
            for o, vl in elsewhere:
                o.select_set(False, view_layer=vl)
            for o in selected:
                o.select_set(False)
            for o in objects:
                o.select_set(True)
            bpy.ops.export_scene.gltf(use_selection=True, **options)
        finally:
            for o in objects:
                o.select_set(False)
            for o in selected:
                o.select_set(True)
            for o, vl in elsewhere:
                o.select_set(True, view_layer=vl)
            layer.objects.active = active
    finally:
        if old_scene is not None and window.scene != old_scene:
            window.scene = old_scene
    return _summary(path, objects)


def _summary(path, objects):
    meshes = [o for o in objects if o.type == "MESH"]
    return {"path": path, "objects": len(meshes), "triangles": budgets(meshes)["triangles"]}


COLOUR_WORDS = ("any", "green", "brown", "grey", "blue", "cyan", "orange", "yellow", "red", "pink", "purple",
                "white", "black")
PICKER_MAX = 120  # thumbnails the picker shows at once (filter for more)
_previews = None
_picker_items = {}


def _picker(self, context):
    """The picker's thumbnails: the library's textures with the panel's filters, PICKER_MAX at most."""
    global _previews
    s = context.scene.jfg64
    key = (library_folder(context.scene), s.texture_colour, s.texture_level)
    if key in _picker_items:
        return _picker_items[key]
    items = []
    if key[0]:
        if _previews is None:
            _previews = bpy.utils.previews.new()
        words = [] if s.texture_colour == "any" else [s.texture_colour]
        level = s.texture_level if s.texture_level >= 0 else None
        for n, tex in enumerate(game_textures(words, level, limit=PICKER_MAX, scene=context.scene)):
            name = f"{tex['id']:04X}"
            if name not in _previews:
                _previews.load(name, tex["path"], "IMAGE")
            items.append((name, name, f"{tex['width']} x {tex['height']}, {' '.join(tex['words'])}",
                          _previews[name].icon_id, n))
    _picker_items.clear()
    _picker_items[key] = items or [("NONE", "No textures", "", 0, 0)]
    return _picker_items[key]


class JFG_OT_game_texture(bpy.types.Operator):
    bl_idname = "jfg64.game_texture"
    bl_label = "Use on active object"
    bl_description = ("Give the active object's active material slot this game texture (a material that shows it): "
                      "the pack names it by number and doesn't carry it")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.active_object
        pick = context.scene.jfg64.texture_pick
        if obj is None or obj.type != "MESH" or pick in ("", "NONE"):
            self.report({"ERROR"}, "Select a mesh and pick a texture")
            return {"CANCELLED"}
        mat = game_texture_material(int(pick, 16), scene=context.scene)
        if obj.material_slots:
            obj.material_slots[obj.active_material_index].material = mat
        else:
            obj.data.materials.append(mat)
        self.report({"INFO"}, f"{obj.name}: {mat.name}")
        return {"FINISHED"}


class JFG64Settings(bpy.types.PropertyGroup):
    textures_folder: StringProperty(name="Library", subtype="DIR_PATH", default="",
                                    description="JFG-Studio's game-texture library (empty: found in Studio's data "
                                                "folder)")
    texture_colour: EnumProperty(name="Colour", items=[(w, w.title(), "") for w in COLOUR_WORDS])
    texture_level: IntProperty(name="Game level", default=-1, min=-1,
                               description="Only textures this game level uses (-1: any). JFG-Studio's stock_levels "
                                           "lists the levels")
    texture_pick: EnumProperty(name="Texture", items=_picker)
    sun_direction: FloatVectorProperty(name="Sun direction", default=(-0.4, 0.3, -1.0), subtype="DIRECTION")
    sun_colour: FloatVectorProperty(name="Sun", default=(0.75, 0.72, 0.65), subtype="COLOR", min=0, max=1)
    ambient: FloatVectorProperty(name="Ambient", default=(0.35, 0.37, 0.45), subtype="COLOR", min=0, max=1)
    tint: BoolProperty(name="Tint by material", default=False,
                       description="Multiply the light by each face's material colour (the game draws only the "
                                   "vertex colours)")
    start_number: IntProperty(name="Start point", default=0, min=0, max=255)
    yaw_note: FloatProperty(default=0.0)


class JFG_PT_panel(bpy.types.Panel):
    bl_label = "Jet Force Gemini"
    bl_idname = "JFG_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "JFG"

    def draw(self, context):
        s = context.scene.jfg64
        lay = self.layout
        box = lay.box()
        box.label(text="Surface of the active material")
        obj = context.active_object
        if obj and obj.active_material:
            box.label(text=f"{obj.active_material.name}: {surface_of(obj.active_material)}")
        grid = box.grid_flow(columns=3, align=True)
        for k in SURFACES:
            grid.operator("jfg64.surface", text=k.title()).kind = k
        box = lay.box()
        box.label(text="Game textures (from your own ROM)")
        folder = library_folder(context.scene)
        if folder is None:
            box.label(text="No library yet: open a project in JFG-Studio once")
            box.prop(s, "textures_folder")
        else:
            row = box.row(align=True)
            row.prop(s, "texture_colour", text="")
            row.prop(s, "texture_level")
            box.template_icon_view(s, "texture_pick", show_labels=True, scale=5.0, scale_popup=4.0)
            box.operator("jfg64.game_texture")
        box = lay.box()
        box.label(text="Vertex light")
        box.prop(s, "sun_colour")
        box.prop(s, "ambient")
        box.prop(s, "sun_direction")
        box.label(text="(a Sun lamp in the scene sets the direction)")
        box.prop(s, "tint")
        box.operator("jfg64.bake_light")
        box = lay.box()
        box.label(text="Budgets (visible objects)")
        b = budgets(context.visible_objects)
        box.label(text=f"{b['triangles']} triangles")
        row = box.row()
        row.alert = bool(b["shrunk"])
        row.label(text=f"{b['images']} images" + (f", {len(b['shrunk'])} to be shrunk" if b["shrunk"] else
                                                  f" (each at most {IMAGE_PIXELS} pixels in the game)"))
        box.label(text=f"{b['game']} game textures (not carried)")
        row = box.row()
        row.alert = b["far"] > VERTEX_MAX
        row.label(text=f"farthest vertex {b['far']:.0f} (limit {VERTEX_MAX})")
        box.label(text="surfaces: " + (", ".join(b["surfaces"]) or "-"))
        box = lay.box()
        box.label(text="Markers (Studio places these)")
        grid = box.grid_flow(columns=2, align=True)
        for k in MARKERS:
            grid.operator("jfg64.add_marker", text=k.title()).kind = k
        if obj and "jfg_type" in obj:
            box.prop(obj, '["jfg_type"]', text="Type")
            box.prop(obj, '["jfg_fields"]', text="Fields")
        lay.operator("jfg64.export", icon="EXPORT")


CLASSES = (JFG64Settings, JFG_OT_surface, JFG_OT_bake, JFG_OT_marker, JFG_OT_game_texture, JFG_OT_export,
           JFG_PT_panel)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.jfg64 = bpy.props.PointerProperty(type=JFG64Settings)


def unregister():
    global _previews
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None
    _picker_items.clear()
    del bpy.types.Scene.jfg64
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
