"""Single anonymous free inference attempt. Never uses billing tokens, never substitutes a mesh."""
import base64, hashlib, json, shutil, struct, sys, os
from pathlib import Path
from gradio_client import Client, handle_file

root = Path(__file__).resolve().parent
out = root / "output"
out.mkdir(exist_ok=True)
source = base64.b64decode((root / "reference.jpg.b64").read_text(), validate=False)
assert hashlib.sha256(source).hexdigest() == "5d0b4b4ff4f7e0b874b6410865bef37f7171cc62e71a16abf45bc31b8883a763", "Reference transfer corrupted"
reference = out / "reference.jpg"
reference.write_bytes(source)
# No HF token; keep inference strictly in the public anonymous pool.
os.environ.pop("HF_TOKEN", None)
os.environ.pop("HUGGING_FACE_HUB_TOKEN", None)
client = Client("trellis-community/TRELLIS", token=False, download_files=str(out / "remote"))
apis = client.view_api(return_format="dict", print_info=False)["named_endpoints"]
print("Public endpoints:", list(apis), flush=True)
if "/start_session" in apis:
    client.predict(api_name="/start_session")
if "/preprocess_image" not in apis:
    raise RuntimeError("Expected preprocessing API unavailable; no substitute generated")
cleaned = client.predict(handle_file(str(reference)), api_name="/preprocess_image")
def paths(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from paths(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from paths(v)
image = next((p for p in paths(cleaned) if Path(p).is_file()), None)
if not image:
    raise RuntimeError("Preprocessing returned no image file")
shutil.copy2(image, out / ("processed" + Path(image).suffix))
endpoint = "/generate_and_extract_glb"
if endpoint not in apis:
    raise RuntimeError("Expected GLB generation endpoint unavailable")
known = {
    "image": handle_file(image), "multiimages": [], "is_multiimage": False,
    "seed": 41, "ss_guidance_strength": 7.5, "ss_sampling_steps": 12,
    "slat_guidance_strength": 3.0, "slat_sampling_steps": 12,
    "multiimage_algo": "stochastic", "mesh_simplify": 0.90, "texture_size": 2048,
}
parameters = apis[endpoint]["parameters"]
kwargs = {}
for param in parameters:
    name = param["parameter_name"]
    if name not in known and not param.get("parameter_has_default"):
        raise RuntimeError("Unknown required public API parameter: " + name)
    if name in known:
        kwargs[name] = known[name]
print("Submitting exactly one anonymous generation request", flush=True)
result = client.predict(api_name=endpoint, **kwargs)
model = next((Path(p) for p in paths(result) if p.lower().endswith(".glb") and Path(p).is_file()), None)
if model is None:
    raise RuntimeError("No actual GLB returned")
raw = model.read_bytes()
if len(raw) < 10000 or raw[:4] != b"glTF":
    raise RuntimeError("Invalid GLB header")
version, total, jsonlen = struct.unpack_from("<III", raw, 4)
assert version == 2 and total == len(raw) and raw[16:20] == b"JSON"
doc = json.loads(raw[20:20+jsonlen])
assert doc.get("meshes") and doc.get("images") and doc.get("materials"), "Expected real textured geometry"
dest = out / "Leshy_realistic_candidate.glb"
dest.write_bytes(raw)
for p in paths(result):
    if p.lower().endswith(".mp4") and Path(p).is_file():
        shutil.copy2(p, out / "turntable.mp4")
        break
meta = {
    "source_sha256": hashlib.sha256(source).hexdigest(),
    "model_sha256": hashlib.sha256(raw).hexdigest(),
    "engine": "trellis-community/TRELLIS",
    "mesh_count": len(doc["meshes"]), "textures": len(doc.get("textures", [])),
    "skins": len(doc.get("skins", [])), "animations": len(doc.get("animations", [])),
    "source": "approved Leshy concept, isolated and resized reference",
    "status": "candidate; visual review and character rigging still required",
}
(out / "manifest.json").write_text(json.dumps(meta, indent=2))
shutil.rmtree(out / "remote", ignore_errors=True)
print(json.dumps(meta, indent=2))
