from __future__ import annotations

import json
import re
import shlex
from typing import Any


DEFAULT_CPP_NOOP_LOOP = """rclcpp::spin_some(node);
// No periodic work by default.
loop_rate.sleep();"""


def safe_cpp_identifier(value: object, fallback: str = "node") -> str:
    text = re.sub(r"[^A-Za-z0-9_]", "_", str(value or "").strip())
    if not text:
        text = fallback
    if text[0].isdigit():
        text = f"n_{text}"
    return text


def cpp_type(data_type: object) -> str:
    parts = str(data_type or "std_msgs/msg/String").replace(".", "/").split("/")
    if len(parts) != 3:
        parts = ["std_msgs", "msg", "String"]
    package, kind, name = parts
    namespace = "msg" if kind == "msg" else kind
    return f"{package}::{namespace}::{name}"


def is_cpp_message_type(data_type: object) -> bool:
    parts = str(data_type or "").replace(".", "/").split("/")
    return len(parts) == 3 and parts[1] == "msg" and bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*/msg/[A-Za-z][A-Za-z0-9_]*", "/".join(parts)))


def cpp_include(data_type: object) -> str:
    parts = str(data_type or "std_msgs/msg/String").replace(".", "/").split("/")
    if len(parts) != 3:
        parts = ["std_msgs", "msg", "String"]
    package, kind, name = parts
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    return f"{package}/{kind}/{snake}.hpp"


def cpp_string_literal(value: object) -> str:
    return json.dumps(str(value or ""), ensure_ascii=True)


def cpp_message_packages_for_node(node: dict[str, Any]) -> list[str]:
    packages: set[str] = set()
    for direction in ("inputs", "outputs"):
        for port in node.get(direction, []) if isinstance(node.get(direction), list) else []:
            if not isinstance(port, dict):
                continue
            parts = str(port.get("dataType") or "").replace(".", "/").split("/")
            if len(parts) == 3 and re.fullmatch(r"[a-z][a-z0-9_]*", parts[0]):
                packages.add(parts[0])
    return sorted(packages)


def render_cpp_workspace_cmake(package_names: list[str]) -> str:
    subdirs = "\n".join(f"add_subdirectory({name})" for name in package_names)
    return f"""cmake_minimum_required(VERSION 3.16)
project(lwrcl_cpp_nodes)

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

{subdirs}
"""


def render_cpp_node_cmake(node: dict[str, Any]) -> str:
    package_libraries = "\n".join(
        f"find_library({safe_cpp_identifier(package).upper()}_LIBRARY NAMES {package} PATHS ${{_lwrcl_search_prefixes}} PATH_SUFFIXES lib REQUIRED)"
        for package in node.get("message_packages", [])
    )
    package_link_libraries = " ".join(
        f"${{{safe_cpp_identifier(package).upper()}_LIBRARY}}"
        for package in node.get("message_packages", [])
    )
    extra_link_libraries = " ".join(cmake_link_item(item) for item in cpp_link_items(node.get("linkLibraries") or node.get("requirements") or ""))
    return f"""cmake_minimum_required(VERSION 3.16)
project({node['package_name']})

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(fastrtps REQUIRED)
find_package(fastcdr REQUIRED)
find_package(yaml-cpp REQUIRED)
if(TARGET yaml-cpp::yaml-cpp)
  set(YAML_CPP_LINK_TARGET yaml-cpp::yaml-cpp)
else()
  set(YAML_CPP_LINK_TARGET yaml-cpp)
endif()

set(LWRCL_PREFIX "" CACHE PATH "lwrcl install prefix")
set(_lwrcl_search_prefixes
  ${{LWRCL_PREFIX}}
  $ENV{{LWRCL_PREFIX}}
  $ENV{{LWRCL_FASTDDS_PREFIX}}
  $ENV{{FAST_DDS_PREFIX}}
  ${{CMAKE_PREFIX_PATH}}
  /opt/fast-dds-libs
  /opt/fast-dds
  /opt/cyclonedds-libs
  /opt/vsomeip-libs
)

find_path(LWRCL_INCLUDE_DIR NAMES lwrcl.hpp PATHS ${{_lwrcl_search_prefixes}} PATH_SUFFIXES include REQUIRED)
find_library(LWRCL_LIBRARY NAMES lwrcl PATHS ${{_lwrcl_search_prefixes}} PATH_SUFFIXES lib REQUIRED)
{package_libraries}

add_executable({node['executable_name']} src/{node['executable_name']}.cpp)
target_include_directories({node['executable_name']} PRIVATE ${{LWRCL_INCLUDE_DIR}})
target_link_libraries({node['executable_name']} ${{LWRCL_LIBRARY}} {package_link_libraries} fastrtps fastcdr ${{YAML_CPP_LINK_TARGET}} {extra_link_libraries})
"""


def cpp_link_items(text: object) -> list[str]:
    items: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            parts = shlex.split(line)
        except ValueError:
            parts = line.split()
        items.extend(part for part in parts if part)
    return items


def cmake_link_item(item: str) -> str:
    return json.dumps(item, ensure_ascii=True)


def render_cpp_node_source(node: dict[str, Any], run_hz: float = 60.0) -> str:
    inputs = [port for port in node.get("inputs", []) if isinstance(port, dict) and is_cpp_message_type(port.get("dataType"))]
    outputs = [port for port in node.get("outputs", []) if isinstance(port, dict) and is_cpp_message_type(port.get("dataType"))]
    includes = sorted({"rclcpp/rclcpp.hpp", "chrono", "cstdint", "memory", "string", "vector", *(cpp_include(port.get("dataType")) for port in [*inputs, *outputs])})
    include_text = "\n".join(f"#include <{item}>" for item in includes)
    header_text = str(node.get("importCode") or "").strip()
    header_block = f"\n\n{header_text}" if header_text else ""
    class_name = node.get("class_name") or "GeneratedNode"
    loop_hz = max(1.0, float(run_hz or 60.0))
    output_members = "\n".join(
        f"  std::vector<std::shared_ptr<rclcpp::Publisher<{cpp_type(port.get('dataType'))}>>> pubs_{safe_cpp_identifier(port.get('id'), 'out')}_;"
        for port in outputs
    )
    input_members = "\n".join(
        f"  std::shared_ptr<{cpp_type(port.get('dataType'))}> latest_{safe_cpp_identifier(port.get('id'), 'in')}_;"
        for port in inputs
    )
    publisher_setup = "\n".join(
        f"    pubs_{safe_cpp_identifier(port.get('id'), 'out')}_.push_back(create_publisher<{cpp_type(port.get('dataType'))}>({cpp_string_literal(topic)}, 10));"
        for port in outputs for topic in (port.get("topics") or [])
    )
    subscription_setup = "\n".join(
        f"    subs_{safe_cpp_identifier(port.get('id'), 'in')}_.push_back(create_subscription<{cpp_type(port.get('dataType'))}>({cpp_string_literal(topic)}, 10, [this](const {cpp_type(port.get('dataType'))}& msg) {{ latest_{safe_cpp_identifier(port.get('id'), 'in')}_ = std::make_shared<{cpp_type(port.get('dataType'))}>(msg); {callback_invocation(port)} }}));"
        for port in inputs for topic in (port.get("topics") or [])
    )
    subscription_members = "\n".join(
        f"  std::vector<std::shared_ptr<rclcpp::Subscription<{cpp_type(port.get('dataType'))}>>> subs_{safe_cpp_identifier(port.get('id'), 'in')}_;"
        for port in inputs
    )
    publish_helpers = "\n".join(
        f"""  void publish_{safe_cpp_identifier(port.get('id'), 'out')}(const {cpp_type(port.get('dataType'))}& msg) {{
    for (auto& publisher : pubs_{safe_cpp_identifier(port.get('id'), 'out')}_) {{
      if (publisher) publisher->publish(msg);
    }}
  }}"""
        for port in outputs
    )
    latest_helpers = "\n".join(
        f"""  bool has_{safe_cpp_identifier(port.get('id'), 'in')}() const {{ return static_cast<bool>(latest_{safe_cpp_identifier(port.get('id'), 'in')}_); }}
  const {cpp_type(port.get('dataType'))}* latest_{safe_cpp_identifier(port.get('id'), 'in')}() const {{ return latest_{safe_cpp_identifier(port.get('id'), 'in')}_.get(); }}"""
        for port in inputs
    )
    callback_methods = "\n".join(render_cpp_callback_method(port) for port in inputs if cpp_callback_enabled(port))
    timer_setup = "\n".join(
        f"    timer_{safe_cpp_identifier(timer.get('id'), 'timer')}_ = create_wall_timer(std::chrono::microseconds({max(1, int(round(float(timer.get('periodSec') or 1.0) * 1_000_000.0)))}), [this]() {{ on_timer_{safe_cpp_identifier(timer.get('id'), 'timer')}(); }});"
        for timer in node.get("timers", []) if isinstance(timer, dict) and str(timer.get("callbackCode") or "").strip()
    )
    timer_methods = "\n".join(render_cpp_timer_method(timer) for timer in node.get("timers", []) if isinstance(timer, dict) and str(timer.get("callbackCode") or "").strip())
    timer_members = "\n".join(
        f"  rclcpp::TimerBase::SharedPtr timer_{safe_cpp_identifier(timer.get('id'), 'timer')}_;"
        for timer in node.get("timers", []) if isinstance(timer, dict) and str(timer.get("callbackCode") or "").strip()
    )
    init_code = str(node.get("cppCode") or "").strip()
    init_block = indent_cpp_user_code(init_code, 4) if init_code else "    // No C++ initialize code configured."
    loop_code = str(node.get("loopCode") or "").strip()
    if not loop_code:
        loop_code = DEFAULT_CPP_NOOP_LOOP
    return f"""{include_text}{header_block}

class {class_name} : public rclcpp::Node {{
public:
  {class_name}() : rclcpp::Node({cpp_string_literal(node.get('ros_node_name') or node.get('executable_name') or 'cpp_node')}) {{
{publisher_setup or '    // No publishers are connected.'}
{subscription_setup or '    // No subscriptions are connected.'}
{init_block}
{timer_setup}
  }}

{publish_helpers or '  // No publish helpers were generated.'}
{latest_helpers or '  // No latest helpers were generated.'}
{callback_methods}
{timer_methods}

  void loop_once(const std::shared_ptr<{class_name}>& node, rclcpp::Rate& loop_rate) {{
    auto now = std::chrono::steady_clock::now();
    (void)now;
{indent_cpp_user_code(loop_code, 4)}
  }}

private:
{output_members}
{subscription_members}
{input_members}
{timer_members}
}};

int main(int argc, char** argv) {{
  rclcpp::init(argc, argv);
  auto node = std::make_shared<{class_name}>();
  rclcpp::Rate loop_rate({loop_hz:.6g});
  while (rclcpp::ok()) {{
    node->loop_once(node, loop_rate);
  }}
  rclcpp::shutdown();
  return 0;
}}
"""


def cpp_callback_enabled(port: dict[str, Any]) -> bool:
    return str(port.get("receiveMode") or "callback") != "manual" and bool(str(port.get("callbackCode") or "").strip())


def callback_invocation(port: dict[str, Any]) -> str:
    return f"on_{safe_cpp_identifier(port.get('id'), 'in')}(msg);" if cpp_callback_enabled(port) else ""


def render_cpp_callback_method(port: dict[str, Any]) -> str:
    method = safe_cpp_identifier(port.get("id"), "in")
    code = str(port.get("callbackCode") or "").strip() or "// Add C++ callback logic here."
    return f"""
  void on_{method}(const {cpp_type(port.get('dataType'))}& msg) {{
    auto now = std::chrono::steady_clock::now();
    (void)now;
    (void)msg;
{indent_cpp_user_code(code, 4)}
  }}
"""


def render_cpp_timer_method(timer: dict[str, Any]) -> str:
    timer_id = safe_cpp_identifier(timer.get("id"), "timer")
    code = str(timer.get("callbackCode") or "").strip() or "// Add C++ timer logic here."
    return f"""
  void on_timer_{timer_id}() {{
    auto now = std::chrono::steady_clock::now();
    (void)now;
{indent_cpp_user_code(code, 4)}
  }}
"""


def indent_cpp_user_code(code: str, spaces: int) -> str:
    text = code.strip() or "// Add C++ logic here."
    prefix = " " * spaces
    return "\n".join(prefix + line if line.strip() else "" for line in text.splitlines())
