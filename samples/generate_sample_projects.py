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
	publish("brightness", {"data": float(total) / max(1, count)})
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
	publish("edge_strength", {"data": float(total) / max(1, w * h)})
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
	publish("motion_score", {"data": float(total) / max(1, w * h)})
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
	publish("red_index", {"data": float(red_total - other_total) / max(1, w * h)})
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
	publish("luminance", {"data": float(total) / max(1, len(data))})
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
	publish("mean", {"data": float(total) / max(1, w * h)})
"""

TIMER_FAST_CODE = """state["fast_count"] = int(state.get("fast_count", 0)) + 1
publish("fast", {"data": float(state["fast_count"])})
"""

TIMER_SLOW_CODE = """state["slow_count"] = int(state.get("slow_count", 0)) + 1
publish("slow", {"data": float(state["slow_count"])})
"""

MANUAL_SAMPLER_TIMER_CODE = """value = latest("in")
if value is None:
	return_value = None
else:
	raw = value.get("data", 0.0) if isinstance(value, dict) else getattr(value, "data", 0.0)
	state["last_raw"] = float(raw)
	state["filtered"] = state.get("filtered", float(raw)) * 0.85 + float(raw) * 0.15
	publish("raw", {"data": float(raw)})
	publish("filtered", {"data": float(state["filtered"])})
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
		"params": {"fileName": name + ".embedded.mp4", "dataUrl": image["dataUrl"], params_key: image, "baseFrameMessage": image, "embeddedVideo": True, "embeddedFps": 30, "publishHz": 30, "duration": 10, "loop": True} if video else {"fileName": name + ".embedded.png", "dataUrl": image["dataUrl"], params_key: image, "publishMode": "oneshot", "publishHz": 1},
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


def image_file_save(node_id: str, x: int, y: int) -> dict:
	return {
		"id": node_id,
		"name": "image_file_save",
		"x": x,
		"y": y,
		"inputs": [{"id": "in1", "name": "image", "dataType": "sensor_msgs/msg/Image", "receiveMode": "manual", "callbackCode": ""}],
		"outputs": [],
		"params": {},
		"loopCode": "",
		"toolType": "image_file_save",
	}


def graph_view(node_id: str, x: int, y: int, sample_limit: int = 10000, x_axis_seconds: float = 10, y_axis_mode: str = "auto", y_min: float = -1, y_max: float = 1) -> dict:
	return {
		"id": node_id,
		"name": "graph_view",
		"x": x,
		"y": y,
		"inputs": [{"id": "in1", "name": "value", "dataType": "std_msgs/msg/Float32", "receiveMode": "manual", "callbackCode": ""}],
		"outputs": [],
		"params": {"fieldPath": "data", "sampleLimit": sample_limit, "xAxisSeconds": x_axis_seconds, "yAxisMode": y_axis_mode, "yMin": y_min, "yMax": y_max},
		"loopCode": "",
		"toolType": "graph_view",
	}


def function_generator(node_id: str, x: int, y: int, name: str = "function_generator", params: dict | None = None) -> dict:
	default_params = {
		"signalType": "sine",
		"amplitude": 1,
		"bias": 0,
		"frequency": 1,
		"phase": 0,
		"sampleTime": 0,
		"publishHz": 100,
		"ddsTopic": "",
		"stepTime": 1,
		"initialValue": 0,
		"finalValue": 1,
		"dutyCycle": 50,
		"rampSlope": 1,
		"chirpStartFrequency": 0.1,
		"chirpEndFrequency": 10,
		"chirpDuration": 10,
		"noiseMean": 0,
		"noiseStd": 1,
		"noiseSeed": 1,
	}
	default_params.update(params or {})
	return {
		"id": node_id,
		"name": name,
		"x": x,
		"y": y,
		"inputs": [],
		"outputs": [{"id": "out1", "name": "signal", "dataType": "std_msgs/msg/Float32"}],
		"params": default_params,
		"loopCode": "",
		"toolType": "function_generator",
	}


def topic_input(node_id: str, x: int, y: int, data_type: str = "") -> dict:
	return {
		"id": node_id,
		"name": "topic_input",
		"x": x,
		"y": y,
		"inputs": [],
		"outputs": [{"id": "out1", "name": "topic", "dataType": data_type}],
		"params": {},
		"loopCode": "",
		"toolType": "topic_input",
	}


def topic_output(node_id: str, x: int, y: int, data_type: str = "") -> dict:
	return {
		"id": node_id,
		"name": "topic_output",
		"x": x,
		"y": y,
		"inputs": [{"id": "in1", "name": "topic", "dataType": data_type, "receiveMode": "manual", "callbackCode": ""}],
		"outputs": [],
		"params": {},
		"loopCode": "",
		"toolType": "topic_output",
	}


def custom_node(
	node_id: str,
	name: str,
	x: int,
	y: int,
	inputs: list[tuple[str, str, str]],
	outputs: list[tuple[str, str, str]],
	code: str,
	import_code: str = "",
	timers: list[dict] | None = None,
	input_receive_mode: str = "callback",
	params: dict | None = None,
) -> dict:
	timer_items = timers or []
	return {
		"id": node_id,
		"name": name,
		"x": x,
		"y": y,
		"inputs": [{"id": pid, "name": pname, "dataType": dtype, "receiveMode": input_receive_mode, "callbackCode": code if input_receive_mode == "callback" else ""} for pid, pname, dtype in inputs],
		"outputs": [{"id": pid, "name": pname, "dataType": dtype} for pid, pname, dtype in outputs],
		"loopCode": "",
		"timers": timer_items,
		"timerEnabled": bool(timer_items),
		"timerPeriodSec": float(timer_items[0].get("periodSec", 1.0)) if timer_items else 1.0,
		"timerCode": str(timer_items[0].get("callbackCode", "")) if timer_items else "",
		"importCode": import_code,
		"requirements": "",
		"params": params or {},
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
		topic_output("n6", 850, 25, "std_msgs/msg/Float32"),
	]
	links = [
		link("l1", "n1", "out1", "n2", "image", "/sample1/input_image"),
		link("l2", "n2", "gray", "n3", "gray", "/sample1/grayscale"),
		link("l3", "n3", "edges", "n4", "in1", "/sample1/edge_image"),
		link("l4", "n3", "edge_strength", "n5", "in1", "/sample1/edge_strength"),
		link("l5", "n3", "edge_strength", "n6", "in1", "/sample1/edge_strength"),
	]
	projects["image_video/01_image_edge_topic_graph.json"] = project(nodes, links, 7)

	nodes = [
		image_input("n1", "embedded_video_frame_input", -560, -120, img_b, video=True),
		custom_node("n2", "frame_difference_motion", -210, -165, [("frame", "frame", "sensor_msgs/msg/Image")], [("motion", "motion", "sensor_msgs/msg/Image"), ("motion_score", "motion_score", "std_msgs/msg/Float32")], MOTION_DIFF_CODE, IMPORT_FLOAT32),
		custom_node("n3", "motion_overlay", 150, -165, [("frame", "frame", "sensor_msgs/msg/Image"), ("motion", "motion", "sensor_msgs/msg/Image")], [("overlay", "overlay", "sensor_msgs/msg/Image")], MOTION_OVERLAY_CODE),
		image_view("n4", 510, -230),
		graph_view("n5", 510, 20),
		topic_output("n6", 840, 20, "std_msgs/msg/Float32"),
	]
	links = [
		link("l1", "n1", "out1", "n2", "frame", "/sample2/video_frame"),
		link("l2", "n1", "out1", "n3", "frame", "/sample2/video_frame"),
		link("l3", "n2", "motion", "n3", "motion", "/sample2/motion_mask"),
		link("l4", "n3", "overlay", "n4", "in1", "/sample2/motion_overlay"),
		link("l5", "n2", "motion_score", "n5", "in1", "/sample2/motion_score"),
		link("l6", "n2", "motion_score", "n6", "in1", "/sample2/motion_score"),
	]
	projects["image_video/02_video_motion_topic_graph.json"] = project(nodes, links, 7)

	nodes = [
		image_input("n1", "embedded_color_input", -520, -120, img_c),
		custom_node("n2", "contrast_stretch", -180, -160, [("image", "image", "sensor_msgs/msg/Image")], [("contrast", "contrast", "sensor_msgs/msg/Image")], CONTRAST_CODE),
		custom_node("n3", "red_balance_index", 170, -160, [("image", "image", "sensor_msgs/msg/Image")], [("balanced", "balanced", "sensor_msgs/msg/Image"), ("red_index", "red_index", "std_msgs/msg/Float32")], RED_BALANCE_CODE, IMPORT_FLOAT32),
		image_view("n4", 535, -215),
		graph_view("n5", 535, 25),
		topic_output("n6", 865, 25, "std_msgs/msg/Float32"),
	]
	links = [
		link("l1", "n1", "out1", "n2", "image", "/sample3/input_color"),
		link("l2", "n2", "contrast", "n3", "image", "/sample3/contrast_image"),
		link("l3", "n3", "balanced", "n4", "in1", "/sample3/red_balanced_image"),
		link("l4", "n3", "red_index", "n5", "in1", "/sample3/red_index"),
		link("l5", "n3", "red_index", "n6", "in1", "/sample3/red_index"),
	]
	projects["image_video/03_image_color_balance_topic_graph.json"] = project(nodes, links, 7)

	nodes = [
		image_input("n1", "embedded_low_light_video", -550, -120, img_d, video=True),
		custom_node("n2", "gamma_brighten", -205, -165, [("frame", "frame", "sensor_msgs/msg/Image")], [("bright", "bright", "sensor_msgs/msg/Image"), ("luminance", "luminance", "std_msgs/msg/Float32")], GAMMA_CODE, IMPORT_FLOAT32),
		custom_node("n3", "pseudo_thermal_colormap", 160, -165, [("image", "image", "sensor_msgs/msg/Image")], [("thermal", "thermal", "sensor_msgs/msg/Image")], COLORMAP_CODE),
		image_view("n4", 520, -225),
		graph_view("n5", 520, 20),
		topic_output("n6", 850, 20, "std_msgs/msg/Float32"),
	]
	links = [
		link("l1", "n1", "out1", "n2", "frame", "/sample4/low_light_frame"),
		link("l2", "n2", "bright", "n3", "image", "/sample4/brightened_frame"),
		link("l3", "n3", "thermal", "n4", "in1", "/sample4/thermal_view"),
		link("l4", "n2", "luminance", "n5", "in1", "/sample4/luminance"),
		link("l5", "n2", "luminance", "n6", "in1", "/sample4/luminance"),
	]
	projects["image_video/04_video_low_light_colormap_topic_graph.json"] = project(nodes, links, 7)

	nodes = [
		image_input("n1", "embedded_crop_input", -520, -120, img_e),
		custom_node("n2", "center_crop", -180, -160, [("image", "image", "sensor_msgs/msg/Image")], [("crop", "crop", "sensor_msgs/msg/Image")], CROP_CODE),
		custom_node("n3", "mosaic_privacy_filter", 170, -160, [("image", "image", "sensor_msgs/msg/Image")], [("mosaic", "mosaic", "sensor_msgs/msg/Image"), ("mean", "mean", "std_msgs/msg/Float32")], MOSAIC_CODE, IMPORT_FLOAT32),
		image_view("n4", 535, -215),
		graph_view("n5", 535, 25),
		topic_output("n6", 865, 25, "std_msgs/msg/Float32"),
	]
	links = [
		link("l1", "n1", "out1", "n2", "image", "/sample5/raw_image"),
		link("l2", "n2", "crop", "n3", "image", "/sample5/center_crop"),
		link("l3", "n3", "mosaic", "n4", "in1", "/sample5/mosaic_image"),
		link("l4", "n3", "mean", "n5", "in1", "/sample5/mosaic_mean"),
		link("l5", "n3", "mean", "n6", "in1", "/sample5/mosaic_mean"),
	]
	projects["image_video/05_image_crop_mosaic_topic_graph.json"] = project(nodes, links, 7)

	nodes = [
		function_generator("n1", -260, -90, params={"signalType": "sine", "frequency": 2, "publishHz": 100, "ddsTopic": "/sample6/function_signal"}),
		graph_view("n2", 120, -100, sample_limit=10000, x_axis_seconds=5),
		topic_output("n3", 450, -100, "std_msgs/msg/Float32"),
	]
	links = [
		link("l1", "n1", "out1", "n2", "in1", "/sample6/function_signal"),
		link("l2", "n1", "out1", "n3", "in1", "/sample6/function_signal"),
	]
	projects["signals/06_function_generator_signal_view.json"] = project(nodes, links, 4)

	nodes = [
		function_generator("n1", -520, -250, "sine_2hz", {"signalType": "sine", "frequency": 2, "amplitude": 1, "publishHz": 100}),
		function_generator("n2", -520, -55, "step_after_1s", {"signalType": "step", "stepTime": 1, "initialValue": -1, "finalValue": 1, "publishHz": 100}),
		function_generator("n3", -520, 140, "chirp_0_2_to_8hz", {"signalType": "chirp", "chirpStartFrequency": 0.2, "chirpEndFrequency": 8, "chirpDuration": 6, "amplitude": 1, "publishHz": 100}),
		function_generator("n4", -520, 335, "white_noise", {"signalType": "white_noise", "noiseMean": 0, "noiseStd": 0.25, "noiseSeed": 7, "publishHz": 100}),
		graph_view("n5", -120, -250, x_axis_seconds=5, y_axis_mode="fixed", y_min=-1.5, y_max=1.5),
		graph_view("n6", -120, -55, x_axis_seconds=5, y_axis_mode="fixed", y_min=-1.5, y_max=1.5),
		graph_view("n7", -120, 140, x_axis_seconds=6, y_axis_mode="fixed", y_min=-1.5, y_max=1.5),
		graph_view("n8", -120, 335, x_axis_seconds=5, y_axis_mode="fixed", y_min=-1.0, y_max=1.0),
		topic_output("n9", 220, -250, "std_msgs/msg/Float32"),
		topic_output("n10", 220, -55, "std_msgs/msg/Float32"),
		topic_output("n11", 220, 140, "std_msgs/msg/Float32"),
		topic_output("n12", 220, 335, "std_msgs/msg/Float32"),
	]
	links = [
		link("l1", "n1", "out1", "n5", "in1", "/sample7/sine"),
		link("l2", "n1", "out1", "n9", "in1", "/sample7/sine"),
		link("l3", "n2", "out1", "n6", "in1", "/sample7/step"),
		link("l4", "n2", "out1", "n10", "in1", "/sample7/step"),
		link("l5", "n3", "out1", "n7", "in1", "/sample7/chirp"),
		link("l6", "n3", "out1", "n11", "in1", "/sample7/chirp"),
		link("l7", "n4", "out1", "n8", "in1", "/sample7/white_noise"),
		link("l8", "n4", "out1", "n12", "in1", "/sample7/white_noise"),
	]
	projects["signals/07_function_generator_wave_suite.json"] = project(nodes, links, 13)

	nodes = [
		topic_input("n1", -300, -80, "std_msgs/msg/Float32"),
		graph_view("n2", 80, -90, x_axis_seconds=10),
		topic_output("n3", 410, -90, "std_msgs/msg/Float32"),
	]
	links = [
		link("l1", "n1", "out1", "n2", "in1", "/sample8/external_float"),
		link("l2", "n1", "out1", "n3", "in1", "/sample8/external_float"),
	]
	projects["external_topics/08_external_float_topic_graph.json"] = project(nodes, links, 4)

	nodes = [
		topic_input("n1", -300, -110, "sensor_msgs/msg/Image"),
		image_view("n2", 80, -150),
		image_file_save("n3", 80, 110),
	]
	links = [
		link("l1", "n1", "out1", "n2", "in1", "/sample9/external_image"),
		link("l2", "n1", "out1", "n3", "in1", "/sample9/external_image"),
	]
	projects["external_topics/09_external_image_topic_view_save.json"] = project(nodes, links, 4)

	timer_node = custom_node(
		"n1",
		"multi_timer_counter",
		-360,
		-100,
		[],
		[("fast", "fast_count", "std_msgs/msg/Float32"), ("slow", "slow_count", "std_msgs/msg/Float32")],
		"",
		timers=[
			{"id": "fast_timer", "name": "fast_timer", "periodSec": 0.1, "callbackCode": TIMER_FAST_CODE},
			{"id": "slow_timer", "name": "slow_timer", "periodSec": 0.5, "callbackCode": TIMER_SLOW_CODE},
		],
	)
	nodes = [
		timer_node,
		graph_view("n2", 30, -210, x_axis_seconds=10),
		graph_view("n3", 30, 40, x_axis_seconds=10),
		topic_output("n4", 360, -210, "std_msgs/msg/Float32"),
		topic_output("n5", 360, 40, "std_msgs/msg/Float32"),
	]
	links = [
		link("l1", "n1", "fast", "n2", "in1", "/sample10/fast_count"),
		link("l2", "n1", "fast", "n4", "in1", "/sample10/fast_count"),
		link("l3", "n1", "slow", "n3", "in1", "/sample10/slow_count"),
		link("l4", "n1", "slow", "n5", "in1", "/sample10/slow_count"),
	]
	projects["custom_runtime/10_multi_timer_counter_graph.json"] = project(nodes, links, 6)

	sampler = custom_node(
		"n2",
		"manual_subscriber_sampler",
		-40,
		-100,
		[("in", "signal", "std_msgs/msg/Float32")],
		[("raw", "raw", "std_msgs/msg/Float32"), ("filtered", "filtered", "std_msgs/msg/Float32")],
		"",
		timers=[{"id": "sample_timer", "name": "sample_timer", "periodSec": 0.02, "callbackCode": MANUAL_SAMPLER_TIMER_CODE}],
		input_receive_mode="manual",
	)
	nodes = [
		function_generator("n1", -440, -100, params={"signalType": "square", "frequency": 1, "amplitude": 1, "publishHz": 100}),
		sampler,
		graph_view("n3", 330, -205, x_axis_seconds=8, y_axis_mode="fixed", y_min=-1.2, y_max=1.2),
		graph_view("n4", 330, 45, x_axis_seconds=8, y_axis_mode="fixed", y_min=-1.2, y_max=1.2),
	]
	links = [
		link("l1", "n1", "out1", "n2", "in", "/sample11/source_signal"),
		link("l2", "n2", "raw", "n3", "in1", "/sample11/raw"),
		link("l3", "n2", "filtered", "n4", "in1", "/sample11/filtered"),
	]
	projects["custom_runtime/11_manual_subscriber_timer_sampler.json"] = project(nodes, links, 5)

	nodes = [
		image_input("n1", "embedded_save_input", -520, -105, img_a),
		image_view("n2", -150, -170),
		image_file_save("n3", -150, 95),
		topic_output("n4", 190, -170, "sensor_msgs/msg/Image"),
	]
	links = [
		link("l1", "n1", "out1", "n2", "in1", "/sample12/save_input_image"),
		link("l2", "n1", "out1", "n3", "in1", "/sample12/save_input_image"),
		link("l3", "n1", "out1", "n4", "in1", "/sample12/save_input_image"),
	]
	projects["image_video/12_image_view_save_topic_output.json"] = project(nodes, links, 5)

	return projects


def main() -> None:
	projects = build_projects()
	for name, payload in projects.items():
		path = OUT_DIR / name
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
	print(f"Wrote {len(projects)} sample projects to {OUT_DIR}")


if __name__ == "__main__":
	main()
