extends SceneTree
## Converts FBX, glTF, GLB and OBJ models to one .glb each, with every mesh's node transform baked
## into its vertices so the root is the identity, then prints a one-line measure of the result.
##
## Run it through tools/convert.sh (Godot changes its working directory to the project, so every
## path must be absolute and the wrapper makes them so):
##
##   tools/convert.sh <file-or-folder>... --out <folder>
##
## A folder is scanned recursively and its files are written under --out at their path relative to
## that folder; a file is written directly under --out. OBJ files go through obj2gltf first.

const OBJ2GLTF := "obj2gltf@3.2.0"
const EXTENSIONS := ["fbx", "gltf", "glb", "obj"]
const MAX_TEXTURE := 1024
const MAX_TEXTURES := 2

var _failed := 0
var _done := 0
var _written := {}  # destination path -> source path, to catch two inputs with one output name


func _init() -> void:
	var inputs := PackedStringArray()
	var out_dir := ""
	var args := OS.get_cmdline_user_args()
	var i := 0
	while i < args.size():
		var a: String = args[i]
		if a == "--out" and i + 1 < args.size():
			out_dir = args[i + 1]
			i += 1
		elif a.begins_with("--out="):
			out_dir = a.substr(6)
		elif a.begins_with("--"):
			printerr("unknown option ", a)
			quit(2)
			return
		else:
			inputs.append(a)
		i += 1
	if inputs.is_empty() or out_dir == "":
		printerr("usage: tools/convert.sh <file-or-folder>... --out <folder>")
		quit(2)
		return
	if not out_dir.is_absolute_path():
		printerr("--out must be an absolute path; run this through tools/convert.sh")
		quit(2)
		return
	for input in inputs:
		_convert_input(input, out_dir)
	print("convert: %d of %d converted" % [_done, _done + _failed])
	quit(1 if _failed > 0 else 0)


func _convert_input(input: String, out_dir: String) -> void:
	if not input.is_absolute_path():
		_fail(input, "not an absolute path; run this through tools/convert.sh")
	elif DirAccess.dir_exists_absolute(input):
		var files := PackedStringArray()
		_scan(input, files)
		if files.is_empty():
			_fail(input, "no fbx, gltf, glb or obj files in this folder")
		for f in files:
			_convert_file(f, out_dir.path_join(f.substr(input.length()).lstrip("/").get_basename() + ".glb"))
	elif FileAccess.file_exists(input):
		_convert_file(input, out_dir.path_join(input.get_file().get_basename() + ".glb"))
	else:
		_fail(input, "no such file or folder")


func _scan(dir: String, out: PackedStringArray) -> void:
	var d := DirAccess.open(dir)
	if d == null:
		return
	var names: Array = []
	names.append_array(d.get_directories())
	names.append_array(d.get_files())
	names.sort()
	for n in names:
		var p: String = dir.path_join(n)
		if d.dir_exists(n):
			_scan(p, out)
		elif n.get_extension().to_lower() in EXTENSIONS:
			out.append(p)


func _fail(path: String, reason: String) -> void:
	_failed += 1
	print("FAIL %s: %s" % [path, reason])


func _convert_file(path: String, dest: String) -> void:
	var ext := path.get_extension().to_lower()
	if not ext in EXTENSIONS:
		_fail(path, "unsupported file type `.%s`" % ext)
		return
	if _written.has(dest):
		_fail(path, "would overwrite %s, already written from %s" % [dest, _written[dest]])
		return
	DirAccess.make_dir_recursive_absolute(dest.get_base_dir())
	var src := path
	var tmp := ""
	if ext == "obj":
		tmp = OS.get_cache_dir().path_join("simhood-obj2gltf-%d.glb" % randi())
		var output := []
		var code := OS.execute("npx", ["--yes", OBJ2GLTF, "-i", path, "-o", tmp, "-b"], output, true)
		if code != 0 or not FileAccess.file_exists(tmp):
			_fail(path, "obj2gltf failed: " + " ".join(output).strip_edges())
			return
		src = tmp
	var err := _bake(src, path.get_file().get_basename(), dest)
	if tmp != "":
		DirAccess.remove_absolute(tmp)
	if err != "":
		_fail(path, err)
		return
	_written[dest] = path
	_done += 1
	print("OK   %s -> %s" % [path, dest])
	print("     ", _measure(dest))


## Loads `src`, bakes the transforms and writes `dest`. Returns "" or the reason it failed.
func _bake(src: String, model_name: String, dest: String) -> String:
	var ext := src.get_extension().to_lower()
	var doc: GLTFDocument
	var state: GLTFState
	if ext == "fbx":
		doc = FBXDocument.new()
		state = FBXState.new()
	else:
		doc = GLTFDocument.new()
		state = GLTFState.new()
	var err := doc.append_from_file(src, state, 0, src.get_base_dir())
	if err != OK:
		return "Godot could not read it (error %d, %s)" % [err, error_string(err)]
	var root := doc.generate_scene(state)
	if root == null:
		return "Godot read it but made no scene"
	var baked := Node3D.new()
	baked.name = model_name
	for mi in _mesh_instances(root):
		var t := _transform_in(root, mi)
		var source: ArrayMesh = mi.mesh if mi.mesh is ArrayMesh else null
		if source == null:
			continue
		var mesh := ArrayMesh.new()
		for s in source.get_surface_count():
			if source.surface_get_primitive_type(s) != Mesh.PRIMITIVE_TRIANGLES:
				continue
			var arrays := source.surface_get_arrays(s)
			_apply(arrays, t)
			mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
			var material := source.surface_get_material(s)
			if material == null:
				material = mi.get_surface_override_material(s)
			mesh.surface_set_material(mesh.get_surface_count() - 1, material)
		if mesh.get_surface_count() == 0:
			continue
		var inst := MeshInstance3D.new()
		inst.mesh = mesh
		inst.name = mi.name
		baked.add_child(inst)
	root.free()
	if baked.get_child_count() == 0:
		baked.free()
		return "it has no triangle meshes"
	var out_doc := GLTFDocument.new()
	var out_state := GLTFState.new()
	err = out_doc.append_from_scene(baked, out_state)
	if err == OK:
		err = out_doc.write_to_filesystem(out_state, dest)
	baked.free()
	if err != OK:
		return "Godot could not write the glb (error %d, %s)" % [err, error_string(err)]
	return ""


## Bakes the transform `t` into one surface's arrays. A transform that mirrors (negative
## determinant) also reverses the winding, so the faces keep facing out.
func _apply(arrays: Array, t: Transform3D) -> void:
	var verts: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
	for i in verts.size():
		verts[i] = t * verts[i]
	arrays[Mesh.ARRAY_VERTEX] = verts
	if arrays[Mesh.ARRAY_NORMAL] != null:
		var normals: PackedVector3Array = arrays[Mesh.ARRAY_NORMAL]
		var nb := t.basis.inverse().transposed()
		for i in normals.size():
			normals[i] = (nb * normals[i]).normalized()
		arrays[Mesh.ARRAY_NORMAL] = normals
	arrays[Mesh.ARRAY_TANGENT] = null
	if t.basis.determinant() < 0.0:
		if arrays[Mesh.ARRAY_INDEX] != null:
			var idx: PackedInt32Array = arrays[Mesh.ARRAY_INDEX]
			for k in range(0, idx.size() - 2, 3):
				var tmp := idx[k + 1]
				idx[k + 1] = idx[k + 2]
				idx[k + 2] = tmp
			arrays[Mesh.ARRAY_INDEX] = idx
		else:
			# No index list: swap the vertex data of every triangle's last two vertices.
			for key in [Mesh.ARRAY_VERTEX, Mesh.ARRAY_NORMAL, Mesh.ARRAY_COLOR, Mesh.ARRAY_TEX_UV, Mesh.ARRAY_TEX_UV2]:
				if arrays[key] != null:
					var data = arrays[key]
					for k in range(0, data.size() - 2, 3):
						var tmp2 = data[k + 1]
						data[k + 1] = data[k + 2]
						data[k + 2] = tmp2
					arrays[key] = data


func _mesh_instances(node: Node, out: Array = []) -> Array:
	if node is MeshInstance3D and node.mesh != null:
		out.append(node)
	for c in node.get_children():
		_mesh_instances(c, out)
	return out


func _transform_in(root: Node3D, node: Node3D) -> Transform3D:
	var t := Transform3D.IDENTITY
	var cur: Node = node
	while cur != null and cur != root:
		if cur is Node3D:
			t = cur.transform * t
		cur = cur.get_parent()
	return t


## One line about the written file, read back from the file itself: triangles, size in model
## units, whether the origin is at the bottom centre, and the textures with their sizes.
func _measure(path: String) -> String:
	var doc := GLTFDocument.new()
	var state := GLTFState.new()
	if doc.append_from_file(path, state) != OK:
		return "measure: the written file could not be read back"
	var root := doc.generate_scene(state)
	var have := false
	var box := AABB()
	var tris := 0
	var textures := {}  # Texture2D -> [width, height]; two slots sharing one image count once
	for mi in _mesh_instances(root):
		var b: AABB = _transform_in(root, mi) * mi.mesh.get_aabb()
		box = box.merge(b) if have else b
		have = true
		tris += mi.mesh.get_faces().size() / 3
		for s in mi.mesh.get_surface_count():
			var m: Material = mi.get_active_material(s)
			if m is BaseMaterial3D:
				for slot in [BaseMaterial3D.TEXTURE_ALBEDO, BaseMaterial3D.TEXTURE_NORMAL, BaseMaterial3D.TEXTURE_METALLIC, BaseMaterial3D.TEXTURE_ROUGHNESS, BaseMaterial3D.TEXTURE_EMISSION]:
					var tex: Texture2D = m.get_texture(slot)
					if tex != null:
						textures[tex] = [tex.get_width(), tex.get_height()]
	root.free()
	var s := box.size
	var cx := box.position.x + s.x * 0.5
	var cz := box.position.z + s.z * 0.5
	var bottom_centre := absf(box.position.y) < 0.02 * maxf(s.y, 0.001) + 0.001 \
		and absf(cx) < 0.1 * maxf(s.x, 0.001) + 0.001 and absf(cz) < 0.1 * maxf(s.z, 0.001) + 0.001
	var tex_text := "none"
	if not textures.is_empty():
		var parts := PackedStringArray()
		var big := false
		for tex in textures:
			var wh: Array = textures[tex]
			parts.append("%dx%d" % [wh[0], wh[1]])
			big = big or wh[0] > MAX_TEXTURE or wh[1] > MAX_TEXTURE
		tex_text = "%d (%s)" % [textures.size(), ", ".join(parts)]
		if big:
			tex_text += " TOO BIG: shrink to at most %d px" % MAX_TEXTURE
		if textures.size() > MAX_TEXTURES:
			tex_text += " TOO MANY: at most %d allowed" % MAX_TEXTURES
	return "triangles=%d size=%.3f x %.3f x %.3f (x, y, z, model units) origin_at_bottom_centre=%s textures=%s" % [
		tris, s.x, s.y, s.z, "yes" if bottom_centre else "no (min y %.3f, centre %.3f, %.3f)" % [box.position.y, cx, cz], tex_text]
