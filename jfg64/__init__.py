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
"""

import json
import math

import bpy
from bpy.props import EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, StringProperty
from mathutils import Vector

bl_info = {
    "name": "jfg64: Jet Force Gemini levels",
    "author": "VibinToast",
    "version": (0, 1, 0),
    "blender": (5, 2, 0),
    "location": "View3D > Sidebar > JFG",
    "description": "Surfaces, vertex light, budgets and markers for JFG-Studio levels",
    "category": "Import-Export",
}

# Surface presets: (material-name word, batch flags, render pass, blend). Ground and walls are 0x1008 in stock tracks.
SURFACES = {
    "solid": ("", 0x1008, "opaque", "OPAQUE"),
    "water": ("water", 0x1200D, "translucent", "BLEND"),
    "lava": ("lava", 0x9008, "opaque", "OPAQUE"),
    "kill": ("kill", 0x80001008, "opaque", "OPAQUE"),
    "collider": ("collider", 0x1408, "opaque", "OPAQUE"),
    "nocollide": ("nocollide", 0x1008, "opaque", "OPAQUE"),
}
WORDS = {"water", "lava", "damage", "kill", "collider", "hidden", "nocollide"}
VERTEX_MAX = 32767
NEW_IMAGES_ROOM = 3  # the game's 3D texture table has room for about three new images

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
    for key in ("water", "lava", "kill", "collider", "nocollide"):
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
    if kind == "nocollide":
        mat["triangleFlags"] = 0x80
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


def bake_light(mesh, world_matrix, sun_dir, sun_col, ambient, layer="Col"):
    """Writes sun * max(0, n.l) + ambient into a byte colour attribute on face corners."""
    attr = mesh.color_attributes.get(layer)
    if attr is None or attr.domain != "CORNER" or attr.data_type != "BYTE_COLOR":
        if attr is not None:
            mesh.color_attributes.remove(attr)
        attr = mesh.color_attributes.new(layer, "BYTE_COLOR", "CORNER")
    mesh.color_attributes.active_color = attr
    normal_matrix = world_matrix.to_3x3().inverted_safe().transposed()
    to_light = -Vector(sun_dir).normalized()
    for poly in mesh.polygons:
        n = (normal_matrix @ poly.normal).normalized()
        d = max(0.0, n.dot(to_light))
        col = [min(1.0, ambient[i] + sun_col[i] * d) for i in range(3)]
        for li in poly.loop_indices:
            attr.data[li].color = (*col, 1.0)
    mesh.update()


class JFG_OT_bake(bpy.types.Operator):
    bl_idname = "jfg64.bake_light"
    bl_label = "Bake vertex light"
    bl_description = "Light the selected meshes' vertex colours from a sun and an ambient colour"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.jfg64
        sun = next((o for o in context.scene.objects if o.type == "LIGHT" and o.data.type == "SUN"), None)
        direction = (sun.matrix_world.to_3x3() @ Vector((0, 0, -1))) if sun else Vector(s.sun_direction)
        meshes = [o for o in context.selected_objects if o.type == "MESH"]
        if not meshes:
            self.report({"ERROR"}, "Select the level's meshes")
            return {"CANCELLED"}
        for o in meshes:
            bake_light(o.data, o.matrix_world, direction, s.sun_colour, s.ambient)
        self.report({"INFO"}, f"Lit {len(meshes)} meshes" + (f" from {sun.name}" if sun else ""))
        return {"FINISHED"}


def budgets(objects):
    """What the game's importer will count: triangles, new images, the vertex range."""
    tris, images, far = 0, set(), 0.0
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
    return {"triangles": tris, "images": len(images), "far": far, "surfaces": sorted(surfaces)}


def add_marker(context, kind, name=None):
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


def export(path):
    # The vertex colours are the "Col" layer Vertex light bakes, picked by name.
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_visible=True, export_extras=True,
                              export_yup=True, export_apply=True, export_normals=False,
                              export_vertex_color="NAME", export_vertex_color_name="Col",
                              export_all_vertex_colors=False, export_animations=False, export_skins=False,
                              export_morph=False, export_cameras=False, export_lights=False)


class JFG64Settings(bpy.types.PropertyGroup):
    sun_direction: FloatVectorProperty(name="Sun direction", default=(-0.4, 0.3, -1.0), subtype="DIRECTION")
    sun_colour: FloatVectorProperty(name="Sun", default=(0.75, 0.72, 0.65), subtype="COLOR", min=0, max=1)
    ambient: FloatVectorProperty(name="Ambient", default=(0.35, 0.37, 0.45), subtype="COLOR", min=0, max=1)
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
        box.operator("jfg64.bake_light")
        box = lay.box()
        box.label(text="Budgets (visible objects)")
        b = budgets(context.visible_objects)
        box.label(text=f"{b['triangles']} triangles")
        row = box.row()
        row.alert = b["images"] > NEW_IMAGES_ROOM
        row.label(text=f"{b['images']} images (about {NEW_IMAGES_ROOM} new fit)")
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
