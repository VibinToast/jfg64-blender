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
- add_marker(context, kind, name=None).
Check `jfg64.__version__` against what a guide asks for; after updating the add-on, restart Blender (a running
Blender keeps the version it loaded).
Lava: Juno passes through it (unhurt), so every Lava surface needs a solid floor a little below it (about 30).
Spaces: Blender's own (Z up, 1 unit = 1 game unit); the export turns it into the game's Y up. Colours are RGB 0-1.
The game takes its colour from the vertex colours alone (the "Col" layer): a material's base colour is not used, so
use bake_light's tint to carry it into the bake.
"""

import json
import math

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, StringProperty
from mathutils import Vector

__version__ = "0.3.3"  # bl_info's version too: raise both with every change (scripts check it)

bl_info = {
    "name": "jfg64: Jet Force Gemini levels",
    "author": "VibinToast",
    "version": (0, 3, 3),
    "blender": (5, 2, 0),
    "location": "View3D > Sidebar > JFG",
    "description": "Surfaces, vertex light, budgets and markers for JFG-Studio levels",
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


def budgets(objects):
    """What the game's importer will count: {"triangles", "images" (new images, each a new texture of the game's),
    "shrunk" (images the game will shrink: over IMAGE_PIXELS pixels or sides not a power of two), "far" (the farthest
    vertex coordinate from the origin, at most VERTEX_MAX), "surfaces" (kinds in use)}."""
    tris, images, far, shrunk = 0, set(), 0.0, set()
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
                    if node.type == "TEX_IMAGE" and node.image:
                        images.add(node.image.name)
                        w, h = node.image.size
                        if w * h > IMAGE_PIXELS or w & (w - 1) or h & (h - 1):
                            shrunk.add(node.image.name)
    return {"triangles": tris, "images": len(images), "shrunk": sorted(shrunk), "far": far,
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


class JFG64Settings(bpy.types.PropertyGroup):
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


CLASSES = (JFG64Settings, JFG_OT_surface, JFG_OT_bake, JFG_OT_marker, JFG_OT_export, JFG_PT_panel)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.jfg64 = bpy.props.PointerProperty(type=JFG64Settings)


def unregister():
    del bpy.types.Scene.jfg64
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
