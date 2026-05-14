#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import struct
import zlib
from pathlib import Path


OUT_DIR = Path(__file__).resolve().parent


def png_data_url(width: int, height: int, rgb: list[int]) -> str:
	def chunk(kind: bytes, data: bytes) -> bytes:
		crc = zlib.crc32(kind + data) & 0xFFFFFFFF
		return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", crc)

	scanlines = bytearray()
	for y in range(height):
		scanlines.append(0)
		start = y * width * 3
		scanlines.extend(rgb[start:start + width * 3])
	payload = b"\x89PNG\r\n\x1a\n"
	payload += chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0))
	payload += chunk(b"IDAT", zlib.compress(bytes(scanlines), 9))
	payload += chunk(b"IEND", b"")
	return "data:image/png;base64," + base64.b64encode(payload).decode("ascii")


def make_image(width: int, height: int, mode: str) -> dict:
	rgb: list[int] = []
	for y in range(height):
		for x in range(width):
			if mode == "checker_gradient":
				block = 42 if ((x // 6) + (y // 6)) % 2 else 0
				r = min(255, x * 255 // max(1, width - 1) + block)
				g = min(255, y * 255 // max(1, height - 1) + block)
				b = min(255, 80 + ((x + y) * 80 // max(1, width + height - 2)))
			elif mode == "traffic":
				lane = 230 if abs(x - width // 2) < 2 or abs(x - width // 3) < 1 else 35
				r = min(255, lane + (70 if y > height * 2 // 3 else 0))
				g = min(255, 45 + y * 170 // max(1, height - 1))
				b = min(255, 60 + x * 130 // max(1, width - 1))
				if (x - width * 3 // 4) ** 2 + (y - height // 3) ** 2 < 42:
					r, g, b = 255, 60, 40
			elif mode == "low_light":
				glow = max(0, 120 - ((x - width // 2) ** 2 + (y - height // 2) ** 2) // 10)
				r = 18 + glow
				g = 26 + glow * 2 // 3
				b = 46 + glow // 2
				if x % 11 == 0 or y % 13 == 0:
					r, g, b = r + 20, g + 20, b + 20
			elif mode == "thermal_subject":
				d1 = max(0, 170 - ((x - width // 3) ** 2 + (y - height // 2) ** 2) // 5)
				d2 = max(0, 120 - ((x - width * 2 // 3) ** 2 + (y - height // 3) ** 2) // 7)
				r = 25 + d1 + d2
				g = 40 + d1 // 2 + d2
				b = 55 + x * 80 // max(1, width - 1)
			else:
				r = 30 + x * 190 // max(1, width - 1)
				g = 40 + y * 180 // max(1, height - 1)
				b = 210 - x * 120 // max(1, width - 1)
				if width // 4 < x < width * 3 // 4 and height // 4 < y < height * 3 // 4:
					r, g, b = min(255, r + 55), min(255, g + 45), max(0, b - 45)
			rgb.extend([max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))])
	return {
		"width": width,
		"height": height,
		"encoding": "rgb8",
		"is_bigendian": 0,
		"step": width * 3,
		"data": rgb,
		"dataUrl": png_data_url(width, height, rgb),
	}


IMPORT_FLOAT32 = "from std_msgs.msg import Float32\n"

GRAYSCALE_CODE = """img = msg
if img:
	data = img.get("data") or []
	w = int(img.get("width") or 0)
	h = int(img.get("height") or 0)
	out = []
	total = 0
	count = w * h
	for i in range(0, len(data), 3):
		y = int(data[i] * 0.299 + data[i + 1] * 0.587 + data[i + 2] * 0.114)
		out.extend([y, y, y])
		total += y
	publish("gray", {"width": w, "height": h, "encoding": "rgb8", "is_bigendian": 0, "step": w * 3, "data": out})
	metric = Float32()
	metric.data = float(total) / max(1, count)
	publish("brightness", metric)
"""

EDGE_CODE = """img = msg
if img:
	data = img.get("data") or []
	w = int(img.get("width") or 0)
	h = int(img.get("height") or 0)
	out = []
	total = 0
	for y in range(h):
		for x in range(w):
			left = data[(y * w + max(0, x - 1)) * 3]
			right = data[(y * w + min(w - 1, x + 1)) * 3]
			up = data[(max(0, y - 1) * w + x) * 3]
			down = data[(min(h - 1, y + 1) * w + x) * 3]
			v = min(255, abs(right - left) + abs(down - up))
			out.extend([v, v, v])
			total += v
	publish("edges", {"width": w, "height": h, "encoding": "rgb8", "is_bigendian": 0, "step": w * 3, "data": out})
	metric = Float32()
	metric.data = float(total) / max(1, w * h)
	publish("edge_strength", metric)
"""

MOTION_DIFF_CODE = """img = msg
if img:
	data = img.get("data") or []
	w = int(img.get("width") or 0)
	h = int(img.get("height") or 0)
	prev = state.get("prev")
	if not prev or len(prev) != len(data):
		prev = data
	out = []
	total = 0
	for i in range(0, len(data), 3):
		diff = (abs(data[i] - prev[i]) + abs(data[i + 1] - prev[i + 1]) + abs(data[i + 2] - prev[i + 2])) // 3
		out.extend([diff, diff, diff])
		total += diff
	state["prev"] = list(data)
	publish("motion", {"width": w, "height": h, "encoding": "rgb8", "is_bigendian": 0, "step": w * 3, "data": out})
	metric = Float32()
	metric.data = float(total) / max(1, w * h)
	publish("motion_score", metric)
"""

MOTION_OVERLAY_CODE = """state[input_id] = msg
src = state.get("frame")
motion = state.get("motion")
if src and motion:
	src_data = src.get("data") or []
	motion_data = motion.get("data") or []
	w = int(src.get("width") or 0)
	h = int(src.get("height") or 0)
	out = []
	limit = min(len(src_data), len(motion_data))
	for i in range(0, limit, 3):
		m = motion_data[i]
		out.extend([min(255, src_data[i] + m), max(0, src_data[i + 1] - m // 2), max(0, src_data[i + 2] - m // 2)])
	publish("overlay", {"width": w, "height": h, "encoding": "rgb8", "is_bigendian": 0, "step": w * 3, "data": out})
"""

CONTRAST_CODE = """img = msg
if img:
	data = img.get("data") or []
	w = int(img.get("width") or 0)
	h = int(img.get("height") or 0)
	low = 255
	high = 0
	for v in data:
		low = min(low, v)
		high = max(high, v)
	span = max(1, high - low)
	out = []
	for v in data:
		out.append(max(0, min(255, int((v - low) * 255 / span))))
	publish("contrast", {"width": w, "height": h, "encoding": "rgb8", "is_bigendian": 0, "step": w * 3, "data": out})
"""

RED_BALANCE_CODE = """img = msg
if img:
	data = img.get("data") or []
	w = int(img.get("width") or 0)
	h = int(img.get("height") or 0)
	out = []
	red_total = 0
	other_total = 0
	for i in range(0, len(data), 3):
		r = min(255, int(data[i] * 1.22))
		g = int(data[i + 1] * 0.92)
		b = int(data[i + 2] * 0.88)
		out.extend([r, g, b])
		red_total += r
		other_total += (g + b) // 2
	publish("balanced", {"width": w, "height": h, "encoding": "rgb8", "is_bigendian": 0, "step": w * 3, "data": out})
	metric = Float32()
	metric.data = float(red_total - other_total) / max(1, w * h)
	publish("red_index", metric)
"""

GAMMA_CODE = """img = msg
if img:
	data = img.get("data") or []
	w = int(img.get("width") or 0)
	h = int(img.get("height") or 0)
	out = []
	total = 0
	for v in data:
		nv = min(255, int((float(v) / 255.0) ** 0.55 * 255.0))
		out.append(nv)
		total += nv
	publish("bright", {"width": w, "height": h, "encoding": "rgb8", "is_bigendian": 0, "step": w * 3, "data": out})
	metric = Float32()
	metric.data = float(total) / max(1, len(data))
	publish("luminance", metric)
"""

COLORMAP_CODE = """img = msg
if img:
	data = img.get("data") or []
	w = int(img.get("width") or 0)
	h = int(img.get("height") or 0)
	out = []
	for i in range(0, len(data), 3):
		y = (data[i] + data[i + 1] + data[i + 2]) // 3
		r = min(255, y * 2)
		g = 255 - abs(128 - y) * 2
		b = min(255, (255 - y) * 2)
		out.extend([max(0, r), max(0, g), max(0, b)])
	publish("thermal", {"width": w, "height": h, "encoding": "rgb8", "is_bigendian": 0, "step": w * 3, "data": out})
"""

CROP_CODE = """img = msg
if img:
	data = img.get("data") or []
	w = int(img.get("width") or 0)
	h = int(img.get("height") or 0)
	x0 = w // 4
	y0 = h // 4
	cw = max(1, w // 2)
	ch = max(1, h // 2)
	out = []
	for y in range(y0, y0 + ch):
		start = (y * w + x0) * 3
		out.extend(data[start:start + cw * 3])
	publish("crop", {"width": cw, "height": ch, "encoding": "rgb8", "is_bigendian": 0, "step": cw * 3, "data": out})
"""

MOSAIC_CODE = """img = msg
if img:
	data = img.get("data") or []
	w = int(img.get("width") or 0)
	h = int(img.get("height") or 0)
	block = 4
	out = list(data)
	total = 0
	for by in range(0, h, block):
		for bx in range(0, w, block):
			r = 0
			g = 0
			b = 0
			count = 0
			for y in range(by, min(h, by + block)):
				for x in range(bx, min(w, bx + block)):
					idx = (y * w + x) * 3
					r += data[idx]
					g += data[idx + 1]
					b += data[idx + 2]
					count += 1
			r = r // max(1, count)
			g = g // max(1, count)
			b = b // max(1, count)
			for y in range(by, min(h, by + block)):
				for x in range(bx, min(w, bx + block)):
					idx = (y * w + x) * 3
					out[idx] = r
					out[idx + 1] = g
					out[idx + 2] = b
					total += (r + g + b) // 3
	publish("mosaic", {"width": w, "height": h, "encoding": "rgb8", "is_bigendian": 0, "step": w * 3, "data": out})
	metric = Float32()
	metric.data = float(total) / max(1, w * h)
	publish("mean", metric)
"""


def image_input(node_id: str, name: str, x: int, y: int, image: dict, video: bool = False) -> dict:
	params_key = "frameMessage" if video else "imageMessage"
	return {
		"id": node_id,
		"name": name,
		"x": x,
		"y": y,
		"inputs": [],
		"outputs": [{"id": "out1", "name": "frame" if video else "image", "dataType": "sensor_msgs/msg/Image"}],
		"params": {"fileName": name + ".embedded.mp4", "dataUrl": image["dataUrl"], params_key: image, "baseFrameMessage": image, "embeddedVideo": True, "embeddedFps": 12, "duration": 10, "loop": True} if video else {"fileName": name + ".embedded.png", "dataUrl": image["dataUrl"], params_key: image, "publishMode": "oneshot", "publishHz": 1},
		"loopCode": "",
		"toolType": "video_file_input" if video else "image_file_input",
	}


def image_view(node_id: str, x: int, y: int) -> dict:
	return {
		"id": node_id,
		"name": "image_view",
		"x": x,
		"y": y,
		"inputs": [{"id": "in1", "name": "image", "dataType": "sensor_msgs/msg/Image", "receiveMode": "manual", "callbackCode": ""}],
		"outputs": [],
		"params": {},
		"loopCode": "",
		"toolType": "image_view",
	}


def graph_view(node_id: str, x: int, y: int, sample_limit: int = 120) -> dict:
	return {
		"id": node_id,
		"name": "graph_view",
		"x": x,
		"y": y,
		"inputs": [{"id": "in1", "name": "value", "dataType": "", "receiveMode": "manual", "callbackCode": ""}],
		"outputs": [],
		"params": {"fieldPath": "data", "sampleLimit": sample_limit},
		"loopCode": "",
		"toolType": "graph_view",
	}


def topic_output(node_id: str, x: int, y: int) -> dict:
	return {
		"id": node_id,
		"name": "topic_output",
		"x": x,
		"y": y,
		"inputs": [{"id": "in1", "name": "topic", "dataType": "", "receiveMode": "manual", "callbackCode": ""}],
		"outputs": [],
		"params": {},
		"loopCode": "",
		"toolType": "topic_output",
	}


def custom_node(node_id: str, name: str, x: int, y: int, inputs: list[tuple[str, str, str]], outputs: list[tuple[str, str, str]], code: str, import_code: str = "") -> dict:
	return {
		"id": node_id,
		"name": name,
		"x": x,
		"y": y,
		"inputs": [{"id": pid, "name": pname, "dataType": dtype, "receiveMode": "callback", "callbackCode": code} for pid, pname, dtype in inputs],
		"outputs": [{"id": pid, "name": pname, "dataType": dtype} for pid, pname, dtype in outputs],
		"loopCode": "",
		"timerEnabled": False,
		"timerPeriodSec": 1.0,
		"timerCode": "",
		"importCode": import_code,
		"requirements": "",
		"params": {},
	}


def link(link_id: str, a: str, ap: str, b: str, bp: str, name: str) -> dict:
	return {"id": link_id, "fromNode": a, "fromPort": ap, "toNode": b, "toPort": bp, "name": name}


def project(nodes: list[dict], links: list[dict], next_id: int) -> dict:
	return {
		"format": "lwrclpy-web-node-editor-project",
		"version": 1,
		"nodes": nodes,
		"links": links,
		"view": {"x": 70, "y": 90, "scale": 0.8},
		"nextId": next_id,
	}


def build_projects() -> dict[str, dict]:
	img_a = make_image(48, 32, "checker_gradient")
	img_b = make_image(48, 32, "traffic")
	img_c = make_image(48, 32, "thermal_subject")
	img_d = make_image(48, 32, "low_light")
	img_e = make_image(48, 32, "portrait")

	projects: dict[str, dict] = {}

	nodes = [
		image_input("n1", "embedded_image_input", -520, -120, img_a),
		custom_node("n2", "grayscale_and_brightness", -190, -170, [("image", "image", "sensor_msgs/msg/Image")], [("gray", "gray", "sensor_msgs/msg/Image"), ("brightness", "brightness", "std_msgs/msg/Float32")], GRAYSCALE_CODE, IMPORT_FLOAT32),
		custom_node("n3", "edge_strength_filter", 160, -170, [("gray", "gray", "sensor_msgs/msg/Image")], [("edges", "edges", "sensor_msgs/msg/Image"), ("edge_strength", "edge_strength", "std_msgs/msg/Float32")], EDGE_CODE, IMPORT_FLOAT32),
		image_view("n4", 520, -220),
		graph_view("n5", 520, 25),
		topic_output("n6", 850, 25),
	]
	links = [
		link("l1", "n1", "out1", "n2", "image", "/sample1/input_image"),
		link("l2", "n2", "gray", "n3", "gray", "/sample1/grayscale"),
		link("l3", "n3", "edges", "n4", "in1", "/sample1/edge_image"),
		link("l4", "n3", "edge_strength", "n5", "in1", "/sample1/edge_strength"),
		link("l5", "n3", "edge_strength", "n6", "in1", "/sample1/edge_strength"),
	]
	projects["01_image_edge_topic_graph.json"] = project(nodes, links, 7)

	nodes = [
		image_input("n1", "embedded_video_frame_input", -560, -120, img_b, video=True),
		custom_node("n2", "frame_difference_motion", -210, -165, [("frame", "frame", "sensor_msgs/msg/Image")], [("motion", "motion", "sensor_msgs/msg/Image"), ("motion_score", "motion_score", "std_msgs/msg/Float32")], MOTION_DIFF_CODE, IMPORT_FLOAT32),
		custom_node("n3", "motion_overlay", 150, -165, [("frame", "frame", "sensor_msgs/msg/Image"), ("motion", "motion", "sensor_msgs/msg/Image")], [("overlay", "overlay", "sensor_msgs/msg/Image")], MOTION_OVERLAY_CODE),
		image_view("n4", 510, -230),
		graph_view("n5", 510, 20),
		topic_output("n6", 840, 20),
	]
	links = [
		link("l1", "n1", "out1", "n2", "frame", "/sample2/video_frame"),
		link("l2", "n1", "out1", "n3", "frame", "/sample2/video_frame"),
		link("l3", "n2", "motion", "n3", "motion", "/sample2/motion_mask"),
		link("l4", "n3", "overlay", "n4", "in1", "/sample2/motion_overlay"),
		link("l5", "n2", "motion_score", "n5", "in1", "/sample2/motion_score"),
		link("l6", "n2", "motion_score", "n6", "in1", "/sample2/motion_score"),
	]
	projects["02_video_motion_topic_graph.json"] = project(nodes, links, 7)

	nodes = [
		image_input("n1", "embedded_color_input", -520, -120, img_c),
		custom_node("n2", "contrast_stretch", -180, -160, [("image", "image", "sensor_msgs/msg/Image")], [("contrast", "contrast", "sensor_msgs/msg/Image")], CONTRAST_CODE),
		custom_node("n3", "red_balance_index", 170, -160, [("image", "image", "sensor_msgs/msg/Image")], [("balanced", "balanced", "sensor_msgs/msg/Image"), ("red_index", "red_index", "std_msgs/msg/Float32")], RED_BALANCE_CODE, IMPORT_FLOAT32),
		image_view("n4", 535, -215),
		graph_view("n5", 535, 25),
		topic_output("n6", 865, 25),
	]
	links = [
		link("l1", "n1", "out1", "n2", "image", "/sample3/input_color"),
		link("l2", "n2", "contrast", "n3", "image", "/sample3/contrast_image"),
		link("l3", "n3", "balanced", "n4", "in1", "/sample3/red_balanced_image"),
		link("l4", "n3", "red_index", "n5", "in1", "/sample3/red_index"),
		link("l5", "n3", "red_index", "n6", "in1", "/sample3/red_index"),
	]
	projects["03_image_color_balance_topic_graph.json"] = project(nodes, links, 7)

	nodes = [
		image_input("n1", "embedded_low_light_video", -550, -120, img_d, video=True),
		custom_node("n2", "gamma_brighten", -205, -165, [("frame", "frame", "sensor_msgs/msg/Image")], [("bright", "bright", "sensor_msgs/msg/Image"), ("luminance", "luminance", "std_msgs/msg/Float32")], GAMMA_CODE, IMPORT_FLOAT32),
		custom_node("n3", "pseudo_thermal_colormap", 160, -165, [("image", "image", "sensor_msgs/msg/Image")], [("thermal", "thermal", "sensor_msgs/msg/Image")], COLORMAP_CODE),
		image_view("n4", 520, -225),
		graph_view("n5", 520, 20),
		topic_output("n6", 850, 20),
	]
	links = [
		link("l1", "n1", "out1", "n2", "frame", "/sample4/low_light_frame"),
		link("l2", "n2", "bright", "n3", "image", "/sample4/brightened_frame"),
		link("l3", "n3", "thermal", "n4", "in1", "/sample4/thermal_view"),
		link("l4", "n2", "luminance", "n5", "in1", "/sample4/luminance"),
		link("l5", "n2", "luminance", "n6", "in1", "/sample4/luminance"),
	]
	projects["04_video_low_light_colormap_topic_graph.json"] = project(nodes, links, 7)

	nodes = [
		image_input("n1", "embedded_crop_input", -520, -120, img_e),
		custom_node("n2", "center_crop", -180, -160, [("image", "image", "sensor_msgs/msg/Image")], [("crop", "crop", "sensor_msgs/msg/Image")], CROP_CODE),
		custom_node("n3", "mosaic_privacy_filter", 170, -160, [("image", "image", "sensor_msgs/msg/Image")], [("mosaic", "mosaic", "sensor_msgs/msg/Image"), ("mean", "mean", "std_msgs/msg/Float32")], MOSAIC_CODE, IMPORT_FLOAT32),
		image_view("n4", 535, -215),
		graph_view("n5", 535, 25),
		topic_output("n6", 865, 25),
	]
	links = [
		link("l1", "n1", "out1", "n2", "image", "/sample5/raw_image"),
		link("l2", "n2", "crop", "n3", "image", "/sample5/center_crop"),
		link("l3", "n3", "mosaic", "n4", "in1", "/sample5/mosaic_image"),
		link("l4", "n3", "mean", "n5", "in1", "/sample5/mosaic_mean"),
		link("l5", "n3", "mean", "n6", "in1", "/sample5/mosaic_mean"),
	]
	projects["05_image_crop_mosaic_topic_graph.json"] = project(nodes, links, 7)

	return projects


def main() -> None:
	projects = build_projects()
	for name, payload in projects.items():
		(OUT_DIR / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
	print(f"Wrote {len(projects)} sample projects to {OUT_DIR}")


if __name__ == "__main__":
	main()
