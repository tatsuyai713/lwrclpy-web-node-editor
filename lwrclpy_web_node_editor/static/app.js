const GRAPH_RUN_HZ = 60;
const UI_DISPLAY_FPS = GRAPH_RUN_HZ;
const UI_DISPLAY_FRAME_MS = 1000 / UI_DISPLAY_FPS;
const UI_STATUS_FPS = 10;
const UI_STATUS_POLL_MS = 1000 / UI_STATUS_FPS;
const VIDEO_RAW_IMAGE_TYPE = 'sensor_msgs/msg/Image';
const VIDEO_COMPRESSED_IMAGE_TYPE = 'sensor_msgs/msg/CompressedImage';
const FRAME_FETCH_TIMEOUT_MS = 1200;
const FRAME_DECODE_TIMEOUT_MS = 1200;
const FRAME_STREAM_HEADER_BYTES = 36;

const state = {
  messageTypes: {},
  nodes: [],
  links: [],
  selectedNode: null,
  selectedLink: null,
  editingNode: null,
  editingCode: null,
  nextId: 1,
  autoTimer: null,
  videoTimer: null,
  runStopTimer: null,
  view: { x: 0, y: 0, scale: 1 },
  dragLink: null,
  lastRunAt: 0,
  runInFlight: false,
  runStatusInFlight: false,
  runPayloadUpdateInFlight: false,
  videoPayloadDirty: false,
  videoDirtyNodes: new Set(),
  tickCount: 0,
  runState: 'idle',
  nodeViews: {},
  graphBuffers: {},
  videoInputs: {},
  embeddedVideoInputs: {},
  undoStack: [],
  redoStack: [],
  historySnapshot: '',
  suppressHistory: false,
  projectFileHandle: null,
  projectFileName: 'lwrclpy_web_node_project.json',
  projectIsSample: false,
  customNodes: [],
  lwrclpyReleases: [],
  pythonVersions: [],
  hostPythonVersion: '',
  activeView: 'editor',
  ready: false,
  readyInFlight: false,
  readySignature: '',
  collapsedPaletteGroups: {},
};

const $ = (id) => document.getElementById(id);
const workspace = () => $('workspace');
const scene = () => $('scene');

const DEFAULT_CALLBACK_CODE = `# lwrclpy subscription/service callback body.
# Available: node, input_id, msg, request, response, state, publish(output_id, value), log(...)
node.get_logger().info(f"received {input_id}")

# Example for std_msgs/msg/String:
# publish("out1", msg.data)
`;

const DEFAULT_LOOP_CODE = `# Optional lwrclpy-compatible spin tick body.
# Prefer input callback code for data-dependent processing.
# Available: node, state, now, publish(output_id, value), log(...)
`;

const DEFAULT_TIMER_CODE = `# lwrclpy timer callback body.
# Available: node, timer_id, timer_name, state, now, period, publish(output_id, value), log(...)

# Example:
# state["count"] = state.get("count", 0) + 1
# publish("out1", state["count"])
`;

const DEFAULT_IMPORT_CODE = `# Node-level imports run after this node's venv is ready.
# Example:
# import cv2
# import numpy as np
`;
const DEFAULT_NODE_WIDTH = 320;
const DEFAULT_NODE_MIN_HEIGHT = 88;

const INTERFACE_NODE_TEMPLATES = [
  {
    category: 'Boundary',
    label: 'Topic Input',
    toolType: 'topic_input',
    node: {
      name: 'topic_input',
      inputs: [],
      outputs: [{ id: 'out1', name: 'topic', dataType: '' }],
      params: {},
      loopCode: '',
    },
  },
  {
    category: 'Boundary',
    label: 'Topic Output',
    toolType: 'topic_output',
    node: {
      name: 'topic_output',
      inputs: [{ id: 'in1', name: 'topic', dataType: '', receiveMode: 'manual', callbackCode: '' }],
      outputs: [],
      params: {},
      loopCode: '',
    },
  },
  {
    category: 'Sources',
    label: 'Image File Input',
    toolType: 'image_file_input',
    node: {
      name: 'image_file_input',
      inputs: [],
      outputs: [{ id: 'out1', name: 'image', dataType: 'sensor_msgs/msg/Image' }],
      params: { publishMode: 'oneshot', publishHz: 1 },
      loopCode: '',
    },
  },
  {
    category: 'Sources',
    label: 'Video File Input',
    toolType: 'video_file_input',
    node: {
      name: 'video_file_input',
      inputs: [],
      outputs: [{ id: 'out1', name: 'frame', dataType: VIDEO_RAW_IMAGE_TYPE }],
      params: { loop: false, publishHz: 30, detectedFps: 0, frameSkip: 0 },
      loopCode: '',
    },
  },
  {
    category: 'Sources',
    label: 'MCAP File Input',
    toolType: 'mcap_file_input',
    node: {
      name: 'mcap_file_input',
      inputs: [],
      outputs: [],
      params: { loop: false, playbackRate: 1, mcapPath: '', mcapChannels: [], mcapOutputTopics: {} },
      loopCode: '',
    },
  },
  {
    category: 'TF',
    label: 'URDF Static TF',
    toolType: 'urdf_static_tf_publisher',
    node: {
      name: 'urdf_static_tf_publisher',
      inputs: [],
      outputs: [{ id: 'tf_static', name: 'TF', dataType: 'tf2_msgs/msg/TFMessage' }],
      params: { urdfPath: '', fileName: '' },
      loopCode: '',
    },
  },
  {
    category: 'TF',
    label: 'TF Merge',
    toolType: 'tf_merge',
    node: {
      name: 'tf_merge',
      inputs: [
        { id: 'in1', name: 'TF', dataType: 'tf2_msgs/msg/TFMessage', receiveMode: 'manual', callbackCode: '' },
        { id: 'in2', name: 'TF', dataType: 'tf2_msgs/msg/TFMessage', receiveMode: 'manual', callbackCode: '' },
      ],
      outputs: [
        { id: 'tf', name: 'TF', dataType: 'tf2_msgs/msg/TFMessage' },
      ],
      params: { topicCount: 2 },
      loopCode: '',
    },
  },
  {
    category: 'Views',
    label: '3D Viewer',
    toolType: '3d_viewer',
    node: {
      name: '3d_viewer',
      inputs: [{ id: 'tf_in', name: 'TF', dataType: 'tf2_msgs/msg/TFMessage', receiveMode: 'manual', callbackCode: '' }],
      outputs: [],
      params: { rootFrame: '', enableTf: true, pointCloudCount: 0, pointCloudStyle: 'square', pointCloudSize: 0.03, pointCloudColor: '#ffffff', pointCloudOpacity: 1, occupancyGridCount: 0, occupancyGridColorScheme: 'map', occupancyGridAlpha: 0.7, occupancyGridDrawBehind: true, showRobotModel: false, robotModelPath: '', robotModel: null, robotModelColor: '#9aa4b2', robotModelOpacity: 0.45, gridStep: 0.25, gridSize: 4, axisSize: 0.35, showLabels: true },
      loopCode: '',
    },
  },
  {
    category: 'Image Processing',
    label: 'Crop / Resize',
    toolType: 'image_crop_resize',
    node: {
      name: 'image_crop_resize',
      inputs: [{ id: 'in1', name: 'image', dataType: VIDEO_RAW_IMAGE_TYPE, receiveMode: 'callback', callbackCode: '' }],
      outputs: [{ id: 'out1', name: 'image', dataType: VIDEO_RAW_IMAGE_TYPE }],
      params: { cropEnabled: false, cropX: 0, cropY: 0, cropWidth: 0, cropHeight: 0, cropCenter: false, resizeEnabled: false, targetWidth: 0, targetHeight: 0, keepAspect: true },
      loopCode: '',
    },
  },
  {
    category: 'AI',
    label: 'Interactive Text Input',
    toolType: 'interactive_text_input',
    node: {
      name: 'interactive_text_input',
      inputs: [],
      outputs: [{ id: 'out1', name: 'text', dataType: 'std_msgs/msg/String' }],
      params: { draft: '', messages: [], promptHistory: [], historyCursor: -1, nextSeq: 1, maxMessages: 100 },
      loopCode: '',
    },
  },
  {
    category: 'AI',
    label: 'LLM Text',
    toolType: 'llm_text',
    node: {
      name: 'llm_text',
      inputs: [{ id: 'prompt', name: 'prompt', dataType: 'std_msgs/msg/String', receiveMode: 'callback', callbackCode: '' }],
      outputs: [{ id: 'response', name: 'response', dataType: 'std_msgs/msg/String' }],
      params: { provider: 'ollama', model: 'llama3.2', apiBase: '', apiKeyEnv: 'OPENAI_API_KEY', systemPrompt: '', temperature: 0.2, maxTokens: 512, timeoutSec: 60 },
      loopCode: '',
    },
  },
  {
    category: 'Recording',
    label: 'MCAP Record',
    toolType: 'mcap_record',
    node: {
      name: 'mcap_record',
      inputs: [{ id: 'in1', name: 'topic', dataType: '', receiveMode: 'manual', callbackCode: '' }],
      outputs: [],
      params: { mcapPath: '', topicCount: 1, splitSizeMb: 0 },
      loopCode: '',
    },
  },
  {
    category: 'Signal',
    label: 'Function Generator',
    toolType: 'function_generator',
    node: {
      name: 'function_generator',
      inputs: [],
      outputs: [{ id: 'out1', name: 'signal', dataType: 'std_msgs/msg/Float32' }],
      params: {
        signalType: 'sine',
        amplitude: 1,
        bias: 0,
        frequency: 1,
        phase: 0,
        sampleTime: 0,
        publishHz: 10,
        ddsTopic: '',
        stepTime: 1,
        initialValue: 0,
        finalValue: 1,
        dutyCycle: 50,
        rampSlope: 1,
        chirpStartFrequency: 0.1,
        chirpEndFrequency: 10,
        chirpDuration: 10,
        noiseMean: 0,
        noiseStd: 1,
        noiseSeed: 1,
      },
      loopCode: '',
    },
  },
  {
    category: 'Views',
    label: 'Image Viewer',
    toolType: 'image_view',
    node: {
      name: 'image_view',
      inputs: [{ id: 'in1', name: 'image', dataType: 'sensor_msgs/msg/Image', receiveMode: 'manual', callbackCode: '' }],
      outputs: [],
      params: {},
      loopCode: '',
    },
  },
  {
    category: 'Views',
    label: 'String Viewer',
    toolType: 'string_view',
    node: {
      name: 'string_view',
      inputs: [{ id: 'in1', name: 'text', dataType: 'std_msgs/msg/String', receiveMode: 'manual', callbackCode: '' }],
      outputs: [],
      params: { mode: 'replace', maxChars: 20000 },
      loopCode: '',
    },
  },
  {
    category: 'Views',
    label: 'Chat String Viewer',
    toolType: 'chat_string_view',
    node: {
      name: 'chat_string_view',
      inputs: [{ id: 'in1', name: 'text', dataType: 'std_msgs/msg/String', receiveMode: 'manual', callbackCode: '' }],
      outputs: [],
      params: { maxMessages: 100, maxChars: 20000 },
      loopCode: '',
    },
  },
  {
    category: 'Recording',
    label: 'Image File Save',
    toolType: 'image_file_save',
    node: {
      name: 'image_file_save',
      inputs: [{ id: 'in1', name: 'image', dataType: 'sensor_msgs/msg/Image', receiveMode: 'manual', callbackCode: '' }],
      outputs: [],
      params: {},
      loopCode: '',
    },
  },
  {
    category: 'Views',
    label: 'Graph Viewer',
    toolType: 'graph_view',
    node: {
      name: 'graph_view',
      inputs: [{ id: 'in1', name: 'value', dataType: 'std_msgs/msg/Float32', receiveMode: 'manual', callbackCode: '' }],
      outputs: [],
      params: { fieldPath: 'data', sampleLimit: 10000, xAxisSeconds: 10, yAxisMode: 'auto', yMin: -1, yMax: 1 },
      loopCode: '',
    },
  },
  {
    category: 'Views',
    label: 'Topic Hz Monitor',
    toolType: 'topic_hz_monitor',
    node: {
      name: 'topic_hz_monitor',
      inputs: [{ id: 'in1', name: 'topic', dataType: '', receiveMode: 'manual', callbackCode: '' }],
      outputs: [],
      params: { windowSec: 5.0 },
      loopCode: '',
    },
  },
];

async function init() {
  const data = await fetch('/api/message-types').then((res) => res.json());
  state.messageTypes = data.types || {};
  await refreshCustomNodes();
  await refreshLwrclpyReleases();
  bindToolbar();
  bindCanvas();
  renderInterfaceNodeList();
  renderCustomNodePalette();
  renderCustomNodeManager();
  renderAll();
  resetHistory();
  setExecutionStatus('idle', 'Ready');
  refreshRuntimeHealth();
}

function bindToolbar() {
  $('create-node-side').onclick = () => openNodeDialog();
  $('ready-model').onclick = readyRun;
  $('run-model').onclick = startRun;
  $('stop-model').onclick = stopRun;
  $('force-stop-model').onclick = forceStopRun;
  $('run-duration-model').onclick = runForDuration;
  $('save-project').onclick = () => saveProject(false);
  $('load-project').onchange = loadProject;
  $('load-sample-project').onclick = openSampleProjectDialog;
  $('export-ros2-package').onclick = exportRos2Package;
  $('export-cli-package').onclick = exportCliPackage;
  $('view-editor').onclick = () => setActiveView('editor');
  $('view-custom-nodes').onclick = () => setActiveView('custom-nodes');
  $('custom-node-import').onchange = importCustomNodeJson;
  $('export-custom-nodes').onclick = exportCustomNodes;
  $('refresh-custom-nodes').onclick = async () => {
    await refreshCustomNodes();
    renderCustomNodePalette();
    renderCustomNodeManager();
  };
  $('config-input-count').oninput = renderConfigPorts;
  $('config-output-count').oninput = renderConfigPorts;
  $('config-timer-count').oninput = renderConfigPorts;
  $('config-tf-input').onchange = renderConfigPorts;
  $('config-tf-output').onchange = renderConfigPorts;
  $('config-python-version').onchange = updateDraftRuntimeVersions;
  $('config-lwrclpy-version').onchange = updateDraftRuntimeVersions;
  $('node-form').addEventListener('submit', saveNodeDialog);
  $('code-form').addEventListener('submit', saveCodeDialog);
  $('signal-form').addEventListener('submit', saveSignalDialog);
  $('graph-form').addEventListener('submit', saveGraphDialog);
  $('tf-viewer-form').addEventListener('submit', saveTfViewerDialog);
  $('graph-y-mode').onchange = updateGraphAxisFields;
  document.addEventListener('selectstart', (ev) => {
    if (ev.target.closest('#workspace')) ev.preventDefault();
  });
  document.querySelectorAll('[data-close-dialog]').forEach((button) => {
    button.onclick = (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      $(button.dataset.closeDialog).close();
    };
  });
}

function setActiveView(view) {
  state.activeView = view;
  $('editor-view').classList.toggle('hidden', view !== 'editor');
  $('custom-node-view').classList.toggle('hidden', view !== 'custom-nodes');
  $('view-editor').classList.toggle('active', view === 'editor');
  $('view-custom-nodes').classList.toggle('active', view === 'custom-nodes');
}

function bindCanvas() {
  const ws = workspace();
  ws.addEventListener('pointerdown', (ev) => {
    const isCanvas = ev.target === ws || ev.target.id === 'links' || ev.target.id === 'scene' || ev.target.id === 'nodes';
    if (!isCanvas) return;
    ev.preventDefault();
    state.selectedNode = null;
    state.selectedLink = null;
    renderSelection();
    renderInspector();
    startPan(ev);
  });
  ws.addEventListener('wheel', (ev) => {
    ev.preventDefault();
    const before = screenToWorld(ev.clientX, ev.clientY);
    state.view.scale = clamp(state.view.scale * (ev.deltaY < 0 ? 1.08 : 0.92), 0.35, 2.2);
    const after = screenToWorld(ev.clientX, ev.clientY);
    state.view.x += (after.x - before.x) * state.view.scale;
    state.view.y += (after.y - before.y) * state.view.scale;
    applyView();
  }, { passive: false });
  ws.addEventListener('dragover', (ev) => {
    if (ev.dataTransfer.types.includes('application/x-node-template')) {
      ev.preventDefault();
      ev.dataTransfer.dropEffect = 'copy';
    }
  });
  ws.addEventListener('drop', (ev) => {
    const templateRef = ev.dataTransfer.getData('application/x-node-template');
    if (!templateRef) return;
    ev.preventDefault();
    const template = nodeTemplateFromRef(templateRef);
    if (!template) return;
    const pos = screenToWorld(ev.clientX, ev.clientY);
    const node = createNodeFromTemplate(template, pos);
    state.nodes.push(node);
    state.selectedNode = node.id;
    state.selectedLink = null;
    commitHistory();
    renderAll();
    scheduleRun();
  });
}

function nodeTemplateFromRef(ref) {
  if (/^\d+$/.test(ref)) return INTERFACE_NODE_TEMPLATES[parseInt(ref, 10)];
  const [kind, id] = String(ref || '').split(':');
  if (kind === 'builtin') return INTERFACE_NODE_TEMPLATES[Number(id)];
  if (kind === 'custom') {
    const item = state.customNodes.find((node) => node.id === id);
    return item ? customNodeTemplate(item) : null;
  }
  return null;
}

function createDefaultNode(pos = centerWorld()) {
  return {
    id: `n${state.nextId++}`,
    name: `ros_node_${state.nextId - 1}`,
    x: Math.round(pos.x),
    y: Math.round(pos.y),
    inputs: [{ id: 'in1', name: 'in1', dataType: firstDataType(), receiveMode: 'callback', callbackCode: DEFAULT_CALLBACK_CODE }],
    outputs: [{ id: 'out1', name: 'out1', dataType: firstDataType() }],
    loopCode: DEFAULT_LOOP_CODE,
    timers: [],
    timerEnabled: false,
    timerPeriodSec: 1.0,
    timerCode: DEFAULT_TIMER_CODE,
    importCode: DEFAULT_IMPORT_CODE,
    requirements: '',
    pythonVersion: defaultPythonVersion(),
    lwrclpyVersion: defaultLwrclpyVersion(),
  };
}

function createInterfaceNode(template, pos = centerWorld()) {
  return createNodeFromTemplate(template, pos);
}

function createNodeFromTemplate(template, pos = centerWorld()) {
  const node = structuredClone(template.node);
  node.id = `n${state.nextId++}`;
  if (template.toolType) node.toolType = template.toolType;
  else delete node.toolType;
  node.x = Math.round(pos.x);
  node.y = Math.round(pos.y);
  node.params = node.params || {};
  const normalized = normalizeImportedNode(node);
  const initialView = initialNodeView(normalized);
  if (initialView) state.nodeViews[normalized.id] = initialView;
  return normalized;
}

function initialNodeView(node) {
  if (node?.toolType === 'interactive_text_input') {
    return {
      kind: 'text_input',
      draft: String(node.params?.draft || ''),
      messages: Array.isArray(node.params?.messages) ? node.params.messages : [],
      promptHistory: Array.isArray(node.params?.promptHistory) ? node.params.promptHistory : [],
      status: 'Ready to send',
    };
  }
  if (node?.toolType === 'chat_string_view') {
    return { kind: 'chat', messages: [], status: 'No chat messages' };
  }
  return null;
}

function renderInterfaceNodeList() {
  const list = $('interface-node-list');
  list.innerHTML = '';
  const groups = new Map();
  INTERFACE_NODE_TEMPLATES.forEach((template, index) => {
    const category = template.category || 'Other';
    if (!groups.has(category)) groups.set(category, []);
    groups.get(category).push({ template, index });
  });
  groups.forEach((items, category) => {
    const group = document.createElement('section');
    group.className = 'interface-node-group';
    const key = paletteGroupKey('builtin', category);
    const collapsed = isPaletteGroupCollapsed(key);
    group.classList.toggle('collapsed', collapsed);
    group.appendChild(createPaletteGroupHeader(category, items.length, key, renderInterfaceNodeList));
    const body = document.createElement('div');
    body.className = 'interface-node-group-body';
    items.forEach(({ template, index }) => {
      const button = document.createElement('button');
      button.className = 'interface-node-item';
      button.textContent = template.label;
      button.draggable = true;
      button.addEventListener('dragstart', (ev) => {
        ev.dataTransfer.setData('application/x-node-template', `builtin:${index}`);
        ev.dataTransfer.effectAllowed = 'copy';
      });
      button.onclick = () => {
        const node = createNodeFromTemplate(template);
        state.nodes.push(node);
        state.selectedNode = node.id;
        state.selectedLink = null;
        renderAll();
        scheduleRun();
      };
      body.appendChild(button);
    });
    group.appendChild(body);
    list.appendChild(group);
  });
}

function paletteGroupKey(kind, category) {
  return `${kind}:${String(category || 'Other').toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
}

function createPaletteGroupHeader(category, count, key, rerender) {
  const title = document.createElement('button');
  title.type = 'button';
  title.className = 'interface-node-group-title';
  title.setAttribute('aria-expanded', String(!isPaletteGroupCollapsed(key)));
  title.innerHTML = `<span>${escapeHtml(category)}</span><small>${count}</small>`;
  title.onclick = () => {
    state.collapsedPaletteGroups[key] = !isPaletteGroupCollapsed(key);
    rerender();
  };
  return title;
}

function isPaletteGroupCollapsed(key) {
  return state.collapsedPaletteGroups[key] !== false;
}

function customNodeTemplate(item) {
  const node = structuredClone(item.node || {});
  node.customNodeMeta = customNodeMetadata(item);
  return {
    label: item.name || item.node?.name || item.id,
    toolType: '',
    node,
  };
}

function renderCustomNodePalette() {
  const list = $('custom-node-palette');
  if (!list) return;
  list.innerHTML = '';
  if (!state.customNodes.length) {
    const group = document.createElement('section');
    group.className = 'interface-node-group';
    const key = paletteGroupKey('custom', 'Custom Nodes');
    const collapsed = isPaletteGroupCollapsed(key);
    group.classList.toggle('collapsed', collapsed);
    group.appendChild(createPaletteGroupHeader('Custom Nodes', 0, key, renderCustomNodePalette));
    const body = document.createElement('div');
    body.className = 'interface-node-group-body';
    const empty = document.createElement('div');
    empty.className = 'hint';
    empty.textContent = 'No saved custom nodes.';
    body.appendChild(empty);
    group.appendChild(body);
    list.appendChild(group);
    return;
  }
  const group = document.createElement('section');
  group.className = 'interface-node-group';
  const key = paletteGroupKey('custom', 'Custom Nodes');
  const collapsed = isPaletteGroupCollapsed(key);
  group.classList.toggle('collapsed', collapsed);
  group.appendChild(createPaletteGroupHeader('Custom Nodes', state.customNodes.length, key, renderCustomNodePalette));
  const body = document.createElement('div');
  body.className = 'interface-node-group-body';
  state.customNodes.forEach((item) => {
    const button = document.createElement('button');
    button.className = 'interface-node-item';
    button.innerHTML = `<span>${escapeHtml(item.name || item.id)}</span><small>${escapeHtml(customNodeSummary(item.node || {}))}</small>`;
    button.draggable = true;
    button.addEventListener('dragstart', (ev) => {
      ev.dataTransfer.setData('application/x-node-template', `custom:${item.id}`);
      ev.dataTransfer.effectAllowed = 'copy';
    });
    button.onclick = () => {
      const node = createNodeFromTemplate(customNodeTemplate(item));
      state.nodes.push(node);
      state.selectedNode = node.id;
      state.selectedLink = null;
      renderAll();
      scheduleRun();
    };
    body.appendChild(button);
  });
  group.appendChild(body);
  list.appendChild(group);
}

function customNodeSummary(node) {
  const inputs = Array.isArray(node.inputs) ? node.inputs.length : 0;
  const outputs = Array.isArray(node.outputs) ? node.outputs.length : 0;
  const timers = normalizeTimers(node || {}).length;
  const runtime = nodeRuntimeSummary(node);
  return `${inputs} in / ${outputs} out${timers ? ` / ${timers} timer${timers === 1 ? '' : 's'}` : ''}${runtime ? ` / ${runtime}` : ''}`;
}

async function refreshLwrclpyReleases() {
  try {
    const data = await fetch('/api/lwrclpy-releases').then((res) => res.json());
    state.lwrclpyReleases = Array.isArray(data.releases) ? data.releases : [];
    state.hostPythonVersion = data.hostPythonVersion || '';
  } catch (err) {
    console.error(err);
    state.lwrclpyReleases = [];
  }
  const versions = new Set();
  state.lwrclpyReleases.forEach((release) => {
    (release.pythonVersions || []).forEach((version) => versions.add(String(version)));
  });
  if (state.hostPythonVersion) versions.add(state.hostPythonVersion);
  state.pythonVersions = [...versions].sort(versionCompare);
}

function versionCompare(a, b) {
  const left = String(a).split('.').map(Number);
  const right = String(b).split('.').map(Number);
  for (let index = 0; index < Math.max(left.length, right.length); index += 1) {
    const diff = (left[index] || 0) - (right[index] || 0);
    if (diff) return diff;
  }
  return String(a).localeCompare(String(b));
}

function defaultPythonVersion() {
  return state.hostPythonVersion || state.pythonVersions[0] || '';
}

function defaultLwrclpyVersion() {
  return state.lwrclpyReleases[0]?.version || '';
}

function lwrclpyVersionsForPython(pythonVersion) {
  return state.lwrclpyReleases
    .filter((release) => !pythonVersion || (release.pythonVersions || []).map(String).includes(String(pythonVersion)))
    .map((release) => release.version || release.tag || '')
    .filter(Boolean);
}

function nodeRuntimeSummary(node) {
  if (!node || node.toolType) return '';
  const py = node.pythonVersion || defaultPythonVersion();
  return py ? `Python ${py}` : 'Python host';
}

function customNodeMetadata(item) {
  return {
    format: item.format || 'lwrclpy-web-node-editor-custom-node',
    version: Number(item.version || 1),
    id: item.id || safeFileName(item.name || item.node?.name || 'custom_node'),
    name: item.name || item.node?.name || item.id || 'custom_node',
    description: item.description || '',
  };
}

async function refreshCustomNodes() {
  try {
    const data = await fetch('/api/custom-nodes').then((res) => res.json());
    state.customNodes = Array.isArray(data.customNodes) ? data.customNodes : [];
  } catch (err) {
    console.error(err);
    state.customNodes = [];
  }
}

async function postCustomNodeApi(path, payload) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `Request failed: ${response.status}`);
  if (Array.isArray(data.customNodes)) state.customNodes = data.customNodes;
  return data;
}

function customNodeStoragePayload(node, name = '', description = null) {
  const stored = structuredClone(node);
  const meta = stored.customNodeMeta || {};
  delete stored.id;
  delete stored.x;
  delete stored.y;
  delete stored.toolType;
  delete stored.customNodeMeta;
  stored.pythonVersion = stored.pythonVersion || defaultPythonVersion();
  stored.lwrclpyVersion = stored.lwrclpyVersion || defaultLwrclpyVersion();
  const exportName = name || meta.name || stored.name || 'custom_node';
  const exportDescription = description === null ? (meta.description || '') : String(description || '').trim();
  return {
    format: meta.format || 'lwrclpy-web-node-editor-custom-node',
    version: Number(meta.version || 1),
    id: safeFileName(meta.id && (!name || name === meta.name) ? meta.id : exportName),
    name: exportName,
    description: exportDescription,
    node: stored,
  };
}

async function exportCustomNodeFromEditor(node) {
  if (!node || node.toolType) return;
  const meta = node.customNodeMeta || {};
  const name = prompt('Custom node export name', meta.name || node.name || 'custom_node');
  if (!name) return;
  const description = prompt('Custom node description', meta.description || '');
  if (description === null) return;
  try {
    await postCustomNodeApi('/api/custom-nodes/save', customNodeStoragePayload(node, name.trim(), description));
    renderCustomNodePalette();
    renderCustomNodeManager();
    setExecutionStatus('idle', `Exported custom node ${name.trim()}`);
  } catch (err) {
    setExecutionStatus('error', `Custom node export failed: ${err.message}`);
  }
}

async function importCustomNodeJson(event) {
  const file = event.target.files[0];
  event.target.value = '';
  if (!file) return;
  try {
    const payload = JSON.parse(await file.text());
    const items = customNodeImportItems(payload);
    if (!items.length) {
      alert('No custom node definitions were found in this JSON.');
      return;
    }
    await postCustomNodeApi('/api/custom-nodes/import', { items });
    renderCustomNodePalette();
    renderCustomNodeManager();
    setExecutionStatus('idle', `Imported ${items.length} custom node${items.length === 1 ? '' : 's'}`);
  } catch (err) {
    setExecutionStatus('error', `Custom node import failed: ${err.message}`);
  }
}

function customNodeImportItems(payload) {
  if (Array.isArray(payload)) return payload.filter((item) => item && typeof item === 'object');
  if (Array.isArray(payload.customNodes)) return payload.customNodes;
  if (Array.isArray(payload.items)) return payload.items;
  if (payload?.format === 'lwrclpy-web-node-editor-custom-node' && payload.node) return [payload];
  if (payload?.node && !payload.node.toolType) return [payload];
  if (Array.isArray(payload?.nodes)) {
    return payload.nodes
      .filter((node) => node && typeof node === 'object' && !node.toolType)
      .map((node) => customNodeStoragePayload(normalizeImportedNode(node), node.name));
  }
  return [];
}

function exportCustomNodes() {
  if (!state.customNodes.length) {
    alert('No custom nodes to export.');
    return;
  }
  downloadText('lwrclpy_custom_nodes.json', JSON.stringify({
    format: 'lwrclpy-web-node-editor-custom-node-library',
    version: 1,
    customNodes: state.customNodes,
  }, null, 2), 'application/json');
}

function renderCustomNodeManager() {
  const list = $('custom-node-manager-list');
  if (!list) return;
  list.innerHTML = '';
  if (!state.customNodes.length) {
    list.innerHTML = '<div class="hint">No custom nodes saved.</div>';
    return;
  }
  state.customNodes.forEach((item) => {
    const node = item.node || {};
    const card = document.createElement('article');
    card.className = 'custom-node-card';
    const codePreview = [
      node.importCode ? '# Import Code\n' + node.importCode : '',
      node.loopCode ? '# Main Loop\n' + node.loopCode : '',
      ...(node.inputs || []).filter((port) => port.callbackCode).map((port) => `# Callback: ${port.name}\n${port.callbackCode}`),
      ...normalizeTimers(node).filter((timer) => timer.callbackCode).map((timer) => `# Timer: ${timer.name}\n${timer.callbackCode}`),
    ].filter(Boolean).join('\n\n');
    card.innerHTML = `
      <header>
        <div>
          <h3>${escapeHtml(item.name || item.id)}</h3>
          <small>${escapeHtml(customNodeSummary(node))}</small>
          ${item.description ? `<span class="custom-node-meta">${escapeHtml(item.description)}</span>` : ''}
        </div>
      </header>
      <pre>${escapeHtml(codePreview || '# No code configured')}</pre>
      <div class="card-actions">
        <button data-action="add">Add To Editor</button>
        <button data-action="export">Export</button>
        <button data-action="delete">Delete</button>
      </div>`;
    card.querySelector('[data-action="add"]').onclick = () => {
      const created = createNodeFromTemplate(customNodeTemplate(item));
      state.nodes.push(created);
      state.selectedNode = created.id;
      state.selectedLink = null;
      setActiveView('editor');
      renderAll();
      scheduleRun();
    };
    card.querySelector('[data-action="export"]').onclick = () => {
      downloadText(`${safeFileName(item.name || item.id)}.json`, JSON.stringify(item, null, 2), 'application/json');
    };
    card.querySelector('[data-action="delete"]').onclick = async () => {
      if (!confirm(`Delete custom node "${item.name || item.id}"?`)) return;
      try {
        await postCustomNodeApi('/api/custom-nodes/delete', { id: item.id });
        renderCustomNodePalette();
        renderCustomNodeManager();
      } catch (err) {
        setExecutionStatus('error', `Custom node delete failed: ${err.message}`);
      }
    };
    list.appendChild(card);
  });
}

function openNodeDialog(node = null) {
  state.editingNode = node ? node.id : null;
  const draft = node ? structuredClone(node) : createDefaultNode();
  draft.timers = normalizeTimers(draft);
  $('node-dialog').dataset.draft = JSON.stringify(draft);
  $('node-dialog-title').textContent = node ? 'Edit lwrclpy Node' : 'Create lwrclpy Node';
  $('config-node-name').value = draft.name;
  draft.pythonVersion = draft.pythonVersion || defaultPythonVersion();
  draft.lwrclpyVersion = draft.lwrclpyVersion || defaultLwrclpyVersion();
  renderRuntimeVersionSelects(draft);
  $('config-input-count').value = (draft.inputs || []).filter((port) => !isTfMessagePort(port)).length;
  $('config-output-count').value = (draft.outputs || []).filter((port) => !isTfMessagePort(port)).length;
  $('config-tf-input').checked = Boolean(draft.params?.tfInputEnabled);
  $('config-tf-output').checked = Boolean(draft.params?.tfOutputEnabled);
  $('config-timer-count').value = draft.timers.length;
  renderConfigPorts();
  $('node-dialog').showModal();
}

function renderRuntimeVersionSelects(draft) {
  const pythonSelect = $('config-python-version');
  const lwrclpySelect = $('config-lwrclpy-version');
  const pythonVersions = state.pythonVersions.length ? state.pythonVersions : [draft.pythonVersion || defaultPythonVersion()].filter(Boolean);
  const selectedPython = draft.pythonVersion || defaultPythonVersion();
  pythonSelect.innerHTML = pythonVersions.map((version) => `<option value="${escapeAttr(version)}"${version === selectedPython ? ' selected' : ''}>Python ${escapeHtml(version)}</option>`).join('');
  if (!pythonSelect.innerHTML) pythonSelect.innerHTML = '<option value="">Host Python</option>';
  const selected = pythonSelect.value || selectedPython;
  const lwrclpyVersions = lwrclpyVersionsForPython(selected);
  const selectedLwrclpy = lwrclpyVersions.includes(draft.lwrclpyVersion) ? draft.lwrclpyVersion : (lwrclpyVersions[0] || draft.lwrclpyVersion || '');
  lwrclpySelect.innerHTML = lwrclpyVersions.map((version) => `<option value="${escapeAttr(version)}"${version === selectedLwrclpy ? ' selected' : ''}>lwrclpy ${escapeHtml(version)}</option>`).join('');
  if (!lwrclpySelect.innerHTML) lwrclpySelect.innerHTML = `<option value="${escapeAttr(selectedLwrclpy)}">${selectedLwrclpy ? `lwrclpy ${escapeHtml(selectedLwrclpy)}` : 'Latest compatible lwrclpy'}</option>`;
  draft.pythonVersion = pythonSelect.value;
  draft.lwrclpyVersion = lwrclpySelect.value;
  $('node-dialog').dataset.draft = JSON.stringify(draft);
}

function updateDraftRuntimeVersions() {
  const dialog = $('node-dialog');
  const draft = JSON.parse(dialog.dataset.draft || JSON.stringify(createDefaultNode()));
  draft.pythonVersion = $('config-python-version').value || '';
  const compatible = lwrclpyVersionsForPython(draft.pythonVersion);
  draft.lwrclpyVersion = compatible.includes($('config-lwrclpy-version').value)
    ? $('config-lwrclpy-version').value
    : (compatible[0] || $('config-lwrclpy-version').value || '');
  renderRuntimeVersionSelects(draft);
}

function renderConfigPorts() {
  const dialog = $('node-dialog');
  const draft = JSON.parse(dialog.dataset.draft || JSON.stringify(createDefaultNode()));
  draft.name = $('config-node-name').value || draft.name;
  draft.pythonVersion = $('config-python-version').value || draft.pythonVersion || defaultPythonVersion();
  draft.lwrclpyVersion = $('config-lwrclpy-version').value || draft.lwrclpyVersion || defaultLwrclpyVersion();
  draft.params = { ...(draft.params || {}), tfInputEnabled: $('config-tf-input').checked, tfOutputEnabled: $('config-tf-output').checked };
  draft.inputs = resizePorts(draft.inputs || [], Number($('config-input-count').value || 0), 'in');
  draft.outputs = resizePorts(draft.outputs || [], Number($('config-output-count').value || 0), 'out');
  applyCustomTfPorts(draft);
  draft.timers = resizeTimers(normalizeTimers(draft), Number($('config-timer-count').value || 0));
  draft.timerEnabled = draft.timers.length > 0;
  draft.timerPeriodSec = draft.timers[0]?.periodSec || 1.0;
  draft.timerCode = draft.timers[0]?.callbackCode || DEFAULT_TIMER_CODE;
  dialog.dataset.draft = JSON.stringify(draft);
  renderPortConfigList('input-configs', draft.inputs, 'Input');
  renderPortConfigList('output-configs', draft.outputs, 'Output');
  renderTimerConfigList(draft.timers);
}

function normalizeTimers(node) {
  if (Array.isArray(node.timers)) {
    return node.timers.map((timer, index) => ({
      id: timer.id || `timer${index + 1}`,
      name: timer.name || timer.id || `timer${index + 1}`,
      periodSec: Math.max(0.001, Number(timer.periodSec || timer.period || 1.0)),
      callbackCode: timer.callbackCode || timer.timerCode || DEFAULT_TIMER_CODE,
    }));
  }
  if (node.timerEnabled) {
    return [{
      id: 'timer1',
      name: 'timer1',
      periodSec: Math.max(0.001, Number(node.timerPeriodSec || 1.0)),
      callbackCode: node.timerCode || DEFAULT_TIMER_CODE,
    }];
  }
  return [];
}

function resizeTimers(timers, count) {
  const next = timers.slice(0, count);
  while (next.length < count) {
    const index = next.length + 1;
    next.push({ id: `timer${index}`, name: `timer${index}`, periodSec: 1.0, callbackCode: DEFAULT_TIMER_CODE });
  }
  return next.map((timer, index) => ({
    ...timer,
    id: timer.id || `timer${index + 1}`,
    name: timer.name || timer.id || `timer${index + 1}`,
    periodSec: Math.max(0.001, Number(timer.periodSec || 1.0)),
    callbackCode: timer.callbackCode || DEFAULT_TIMER_CODE,
  }));
}

function renderTimerConfigList(timers) {
  const container = $('timer-configs');
  container.innerHTML = '';
  timers.forEach((timer, index) => {
    const row = document.createElement('div');
    row.className = 'port-config-row';
    row.innerHTML = `
      <label><span>Timer Name</span><input data-timer-key="name" data-index="${index}" value="${escapeAttr(timer.name)}"></label>
      <label><span>Period Seconds</span><input data-timer-key="periodSec" data-index="${index}" type="number" min="0.001" step="0.001" value="${escapeAttr(timer.periodSec)}"></label>`;
    container.appendChild(row);
  });
  container.querySelectorAll('input,textarea').forEach((input) => {
    input.oninput = () => updateDraftTimer(input);
  });
}

function updateDraftTimer(input) {
  const draft = JSON.parse($('node-dialog').dataset.draft || JSON.stringify(createDefaultNode()));
  draft.timers = normalizeTimers(draft);
  const timer = draft.timers[Number(input.dataset.index)];
  if (!timer) return;
  if (input.dataset.timerKey === 'periodSec') {
    timer.periodSec = Math.max(0.001, Number(input.value || 1.0));
  } else {
    timer[input.dataset.timerKey] = input.value;
  }
  draft.timerEnabled = draft.timers.length > 0;
  draft.timerPeriodSec = draft.timers[0]?.periodSec || 1.0;
  draft.timerCode = draft.timers[0]?.callbackCode || DEFAULT_TIMER_CODE;
  $('node-dialog').dataset.draft = JSON.stringify(draft);
}

function resizePorts(ports, count, prefix) {
  const next = ports.filter((port) => !isTfMessagePort(port)).slice(0, count);
  while (next.length < count) {
    const index = next.length + 1;
    next.push({
      id: `${prefix}${index}`,
      name: `${prefix}${index}`,
      dataType: 'std_msgs/msg/String',
      receiveMode: prefix === 'in' ? 'callback' : undefined,
      callbackCode: prefix === 'in' ? DEFAULT_CALLBACK_CODE : undefined,
    });
  }
  return next.map((port, index) => ({ ...port, id: port.id || `${prefix}${index + 1}` }));
}

function isTfMessagePort(port) {
  return String(port?.dataType || '') === 'tf2_msgs/msg/TFMessage';
}

function applyCustomTfPorts(node) {
  const params = node.params || {};
  const nonTfInputs = (node.inputs || []).filter((port) => !isTfMessagePort(port));
  const nonTfOutputs = (node.outputs || []).filter((port) => !isTfMessagePort(port));
  node.inputs = nonTfInputs;
  node.outputs = nonTfOutputs;
  if (params.tfInputEnabled) {
    node.inputs = [
      { id: 'tf_in', name: 'TF', dataType: 'tf2_msgs/msg/TFMessage', receiveMode: 'manual', callbackCode: '' },
      ...nonTfInputs,
    ];
  }
  if (params.tfOutputEnabled) {
    node.outputs = [
      { id: 'tf', name: 'TF', dataType: 'tf2_msgs/msg/TFMessage' },
      ...nonTfOutputs,
    ];
  }
}

function renderPortConfigList(containerId, ports, labelPrefix) {
  const container = $(containerId);
  container.innerHTML = '';
  ports.forEach((port, index) => {
    const row = document.createElement('div');
    row.className = 'port-config-row';
    const type = parseDataType(port.dataType);
    const receiveMode = port.receiveMode || 'callback';
    const tfPort = isTfMessagePort(port);
    const disabled = tfPort ? ' disabled' : '';
    const receiveModeField = containerId.startsWith('input')
      ? `<label class="checkbox-field"><input data-key="receiveMode" data-index="${index}" type="checkbox" ${receiveMode !== 'manual' ? 'checked' : ''}${disabled}><span>Use Callback</span></label>`
      : '';
    row.innerHTML = `
      <label><span>${labelPrefix} Name</span><input data-key="name" data-index="${index}" value="${escapeAttr(port.name)}"${disabled}></label>
      <label><span>Package</span><select data-key="typePackage" data-index="${index}"${disabled}>${packageOptions(type.pkg)}</select></label>
      <label><span>Kind</span><select data-key="typeKind" data-index="${index}"${disabled}>${kindOptions(type.pkg, type.kind)}</select></label>
      <label><span>Name</span><select data-key="typeName" data-index="${index}"${disabled}>${nameOptions(type.pkg, type.kind, type.name)}</select></label>
      ${receiveModeField}`;
    container.appendChild(row);
  });
  container.querySelectorAll('input,select').forEach((input) => {
    input.oninput = () => updateDraftPort(containerId.startsWith('input') ? 'inputs' : 'outputs', input);
  });
}

function packageOptions(selected) {
  return Object.keys(state.messageTypes).map((pkg) => `<option value="${escapeAttr(pkg)}"${pkg === selected ? ' selected' : ''}>${escapeHtml(pkg)}</option>`).join('');
}

function firstDataType() {
  if (state.messageTypes.std_msgs?.msg?.includes('String')) {
    return 'std_msgs/msg/String';
  }
  const pkg = Object.keys(state.messageTypes)[0] || 'std_msgs';
  const kind = Object.keys(state.messageTypes[pkg] || {})[0] || 'msg';
  const name = ((state.messageTypes[pkg] || {})[kind] || [])[0] || 'String';
  return `${pkg}/${kind}/${name}`;
}

function parseDataType(dataType) {
  const fallback = firstDataType().split('/');
  const parts = String(dataType || '').split('/');
  const pkg = parts[0] && state.messageTypes[parts[0]] ? parts[0] : fallback[0];
  const validKinds = Object.keys(state.messageTypes[pkg] || {});
  const kind = validKinds.includes(parts[1]) ? parts[1] : (validKinds[0] || fallback[1]);
  const validNames = ((state.messageTypes[pkg] || {})[kind] || []);
  const name = validNames.includes(parts[2]) ? parts[2] : (validNames[0] || fallback[2]);
  return { pkg, kind, name };
}

function kindOptions(pkg, selected) {
  const kinds = Object.keys(state.messageTypes[pkg] || {});
  return kinds.map((kind) => `<option value="${escapeAttr(kind)}"${kind === selected ? ' selected' : ''}>${escapeHtml(kind)}</option>`).join('');
}

function nameOptions(pkg, kind, selected) {
  const names = ((state.messageTypes[pkg] || {})[kind] || []);
  return names.map((name) => `<option value="${escapeAttr(name)}"${name === selected ? ' selected' : ''}>${escapeHtml(name)}</option>`).join('');
}

function updateDraftPort(direction, input) {
  const draft = JSON.parse($('node-dialog').dataset.draft);
  const port = draft[direction][Number(input.dataset.index)];
  if (input.dataset.key.startsWith('type')) {
    const current = parseDataType(port.dataType);
    if (input.dataset.key === 'typePackage') current.pkg = input.value;
    if (input.dataset.key === 'typeKind') current.kind = input.value;
    if (input.dataset.key === 'typeName') current.name = input.value;
    const validKinds = Object.keys(state.messageTypes[current.pkg] || {});
    if (!validKinds.includes(current.kind)) current.kind = validKinds[0] || 'msg';
    const validNames = ((state.messageTypes[current.pkg] || {})[current.kind] || []);
    if (!validNames.includes(current.name)) current.name = validNames[0] || '';
    port.dataType = `${current.pkg}/${current.kind}/${current.name}`;
    $('node-dialog').dataset.draft = JSON.stringify(draft);
    renderConfigPorts();
    return;
  } else {
    if (input.dataset.key === 'receiveMode') {
      port.receiveMode = input.checked ? 'callback' : 'manual';
      if (input.checked && !port.callbackCode) port.callbackCode = DEFAULT_CALLBACK_CODE;
    } else {
      port[input.dataset.key] = input.value;
    }
  }
  $('node-dialog').dataset.draft = JSON.stringify(draft);
}

function saveNodeDialog(ev) {
  ev.preventDefault();
  const draft = JSON.parse($('node-dialog').dataset.draft);
  draft.name = $('config-node-name').value || 'custom_ros_node';
  draft.pythonVersion = $('config-python-version').value || draft.pythonVersion || defaultPythonVersion();
  draft.lwrclpyVersion = $('config-lwrclpy-version').value || draft.lwrclpyVersion || defaultLwrclpyVersion();
  draft.params = { ...(draft.params || {}), tfInputEnabled: $('config-tf-input').checked, tfOutputEnabled: $('config-tf-output').checked };
  applyCustomTfPorts(draft);
  draft.timers = resizeTimers(normalizeTimers(draft), Number($('config-timer-count').value || 0));
  draft.timerEnabled = draft.timers.length > 0;
  draft.timerPeriodSec = draft.timers[0]?.periodSec || 1.0;
  draft.timerCode = draft.timers[0]?.callbackCode || DEFAULT_TIMER_CODE;
  if (state.editingNode) {
    const index = state.nodes.findIndex((node) => node.id === state.editingNode);
    const previous = state.nodes[index];
    state.nodes[index] = { ...previous, ...draft, x: previous.x, y: previous.y };
    pruneInvalidLinks();
  } else {
    const pos = centerWorld();
    draft.x = Math.round(pos.x);
    draft.y = Math.round(pos.y);
    state.nodes.push(draft);
    state.selectedNode = draft.id;
  }
  $('node-dialog').close();
  renderAll();
  scheduleRun();
}

function openCodeDialog(node, kind) {
  state.editingCode = { nodeId: node.id, kind };
  const callbackPort = kind.startsWith('callback:') ? node.inputs.find((port) => port.id === kind.slice('callback:'.length)) : null;
  const timerId = kind.startsWith('timer:') ? kind.slice('timer:'.length) : '';
  const timer = timerId ? normalizeTimers(node).find((item) => item.id === timerId) : null;
  const isTimer = kind === 'timerCode' || Boolean(timer);
  const isImport = kind === 'importCode';
  const isRequirements = kind === 'requirements';
  $('code-dialog-title').textContent = callbackPort
    ? `${node.name}.${callbackPort.name}: Callback Code`
    : (isTimer ? `${node.name}.${timer?.name || 'timer'}: Timer Callback Code` : (isImport ? `${node.name}: Import Code` : (isRequirements ? `${node.name}: requirements.txt` : `${node.name}: Main Loop Code`)));
  $('code-editor').value = callbackPort
    ? (callbackPort.callbackCode || '')
    : (isTimer ? (timer?.callbackCode || node.timerCode || DEFAULT_TIMER_CODE) : (isImport ? (node.importCode || DEFAULT_IMPORT_CODE) : (isRequirements ? (node.requirements || '') : (node.loopCode || ''))));
  $('code-hint').textContent = callbackPort
    ? 'lwrclpy callback scope: node, input_id, msg/request, response, state, publish(output_id, value), log(...). Use publish(...) instead of direct graph outputs.'
    : (isTimer
      ? 'lwrclpy timer scope: node, timer_id, timer_name, state, now, period, publish(output_id, value), log(...).'
      : (isImport
        ? 'Import code runs once after this node venv is ready. Put imports such as import cv2 and import numpy as np here.'
        : (isRequirements
          ? 'One requirement per line. uv creates this node venv and installs these packages before execution.'
          : 'Optional lwrclpy-compatible spin tick scope: node, state, now, publish(output_id, value), log(...). Prefer input callbacks for data-dependent processing.')));
  $('code-dialog').showModal();
}

function saveCodeDialog(ev) {
  ev.preventDefault();
  const { nodeId, kind } = state.editingCode;
  const node = nodeFor(nodeId);
  if (node && kind.startsWith('callback:')) {
    const port = node.inputs.find((item) => item.id === kind.slice('callback:'.length));
    if (port) port.callbackCode = $('code-editor').value;
  } else if (node && kind.startsWith('timer:')) {
    node.timers = normalizeTimers(node);
    const timer = node.timers.find((item) => item.id === kind.slice('timer:'.length));
    if (timer) timer.callbackCode = $('code-editor').value;
    node.timerEnabled = node.timers.length > 0;
    node.timerPeriodSec = node.timers[0]?.periodSec || 1.0;
    node.timerCode = node.timers[0]?.callbackCode || DEFAULT_TIMER_CODE;
  } else if (node && kind === 'timerCode') {
    node.timerCode = $('code-editor').value;
  } else if (node && kind === 'importCode') {
    node.importCode = $('code-editor').value;
  } else if (node && kind === 'requirements') {
    node.requirements = $('code-editor').value;
  } else if (node) {
    node.loopCode = $('code-editor').value;
  }
  $('code-dialog').close();
  renderAll();
  scheduleRun();
}

function openSignalDialog(node) {
  state.editingNode = node.id;
  $('signal-dialog').dataset.nodeId = node.id;
  $('signal-dialog').dataset.params = JSON.stringify(signalDefaults(node.params || {}));
  renderSignalConfigFields();
  $('signal-dialog').showModal();
}

function signalDefaults(params = {}) {
  return {
    signalType: 'sine',
    amplitude: 1,
    bias: 0,
    frequency: 1,
    phase: 0,
    sampleTime: 0,
    publishHz: 10,
    ddsTopic: '',
    stepTime: 1,
    initialValue: 0,
    finalValue: 1,
    dutyCycle: 50,
    rampSlope: 1,
    chirpStartFrequency: 0.1,
    chirpEndFrequency: 10,
    chirpDuration: 10,
    noiseMean: 0,
    noiseStd: 1,
    noiseSeed: 1,
    ...params,
  };
}

function renderSignalConfigFields() {
  const p = signalDefaults(JSON.parse($('signal-dialog').dataset.params || '{}'));
  const type = p.signalType || 'sine';
  const field = (key, label, min = '', step = '0.01') => `
    <label class="field">
      <span>${label}</span>
      <input data-signal-param="${key}" type="number" ${min !== '' ? `min="${escapeAttr(min)}"` : ''} step="${escapeAttr(step)}" value="${escapeAttr(p[key] ?? 0)}">
    </label>`;
  const textField = (key, label, placeholder = '') => `
    <label class="field">
      <span>${label}</span>
      <input data-signal-text-param="${key}" value="${escapeAttr(p[key] || '')}" placeholder="${escapeAttr(placeholder)}">
    </label>`;
  const typeFields = {
    step: [field('stepTime', 'Step Time', '0'), field('initialValue', 'Initial Value'), field('finalValue', 'Final Value')],
    sine: [field('amplitude', 'Amplitude'), field('bias', 'Bias'), field('frequency', 'Frequency Hz', '0'), field('phase', 'Phase rad')],
    square: [field('amplitude', 'Amplitude'), field('bias', 'Bias'), field('frequency', 'Frequency Hz', '0'), field('dutyCycle', 'Duty Cycle %', '0', '1')],
    ramp: [field('rampSlope', 'Slope'), field('bias', 'Bias')],
    chirp: [field('amplitude', 'Amplitude'), field('bias', 'Bias'), field('chirpStartFrequency', 'Start Frequency Hz', '0'), field('chirpEndFrequency', 'End Frequency Hz', '0'), field('chirpDuration', 'Duration sec', '0.001')],
    white_noise: [field('noiseMean', 'Mean'), field('noiseStd', 'Std Dev', '0'), field('noiseSeed', 'Seed', '', '1')],
  };
  $('signal-config-fields').innerHTML = `
    <label class="field">
      <span>Signal Type</span>
      <select id="signal-type">
        <option value="step"${type === 'step' ? ' selected' : ''}>Step</option>
        <option value="sine"${type === 'sine' ? ' selected' : ''}>Sine</option>
        <option value="square"${type === 'square' ? ' selected' : ''}>Square</option>
        <option value="ramp"${type === 'ramp' ? ' selected' : ''}>Ramp</option>
        <option value="chirp"${type === 'chirp' ? ' selected' : ''}>Chirp</option>
        <option value="white_noise"${type === 'white_noise' ? ' selected' : ''}>White Noise</option>
      </select>
    </label>
    ${field('publishHz', 'Publish Hz', '0.01', 'any')}
    ${textField('ddsTopic', 'DDS Topic', '/example/signal')}
    ${field('sampleTime', 'Sample Time sec', '0', '0.001')}
    ${(typeFields[type] || typeFields.sine).join('')}`;
  $('signal-type').onchange = (ev) => {
    const draft = signalDefaults(JSON.parse($('signal-dialog').dataset.params || '{}'));
    $('signal-config-fields').querySelectorAll('[data-signal-param]').forEach((input) => {
      draft[input.dataset.signalParam] = Number(input.value || 0);
    });
    $('signal-config-fields').querySelectorAll('[data-signal-text-param]').forEach((input) => {
      draft[input.dataset.signalTextParam] = input.value.trim();
    });
    draft.signalType = ev.target.value;
    $('signal-dialog').dataset.params = JSON.stringify(draft);
    renderSignalConfigFields();
  };
}

function saveSignalDialog(ev) {
  ev.preventDefault();
  const node = nodeFor($('signal-dialog').dataset.nodeId);
  if (!node) return;
  const params = signalDefaults(JSON.parse($('signal-dialog').dataset.params || '{}'));
  params.signalType = $('signal-type').value;
  $('signal-config-fields').querySelectorAll('[data-signal-param]').forEach((input) => {
    params[input.dataset.signalParam] = Number(input.value || 0);
  });
  $('signal-config-fields').querySelectorAll('[data-signal-text-param]').forEach((input) => {
    params[input.dataset.signalTextParam] = input.value.trim();
  });
  node.params = params;
  $('signal-dialog').close();
  renderAll();
  refreshRunTimer();
}

function graphDefaults(params = {}) {
  return {
    fieldPath: 'data',
    sampleLimit: 10000,
    xAxisSeconds: 10,
    yAxisMode: 'auto',
    yMin: -1,
    yMax: 1,
    ...params,
  };
}

function openGraphDialog(node) {
  $('graph-dialog').dataset.nodeId = node.id;
  $('graph-dialog').dataset.params = JSON.stringify(graphDefaults(node.params || {}));
  renderGraphConfigFields();
  $('graph-dialog').showModal();
}

function renderGraphConfigFields() {
  const p = graphDefaults(JSON.parse($('graph-dialog').dataset.params || '{}'));
  $('graph-field-path').value = p.fieldPath || 'data';
  $('graph-sample-limit').value = Math.max(1, Number(p.sampleLimit || 10000));
  $('graph-window-sec').value = Number(p.xAxisSeconds || 10);
  $('graph-y-mode').value = p.yAxisMode === 'fixed' ? 'fixed' : 'auto';
  $('graph-y-min').value = Number(p.yMin ?? -1);
  $('graph-y-max').value = Number(p.yMax ?? 1);
  updateGraphAxisFields();
}

function updateGraphAxisFields() {
  const fixed = $('graph-y-mode').value === 'fixed';
  $('graph-y-min').disabled = !fixed;
  $('graph-y-max').disabled = !fixed;
  const draft = graphDefaults(JSON.parse($('graph-dialog').dataset.params || '{}'));
  draft.yAxisMode = fixed ? 'fixed' : 'auto';
  $('graph-dialog').dataset.params = JSON.stringify(draft);
}

function saveGraphDialog(ev) {
  ev.preventDefault();
  ev.stopPropagation();
  const node = nodeFor($('graph-dialog').dataset.nodeId);
  if (!node) return;
  const draft = graphDefaults(JSON.parse($('graph-dialog').dataset.params || '{}'));
  const yMin = Number($('graph-y-min').value === '' ? -1 : $('graph-y-min').value);
  const yMax = Number($('graph-y-max').value === '' ? 1 : $('graph-y-max').value);
  node.params = {
    ...draft,
    fieldPath: $('graph-field-path').value.trim() || 'data',
    sampleLimit: Math.max(1, Math.round(Number($('graph-sample-limit').value || 10000))),
    xAxisSeconds: Math.max(0.1, Number($('graph-window-sec').value || 10)),
    yAxisMode: $('graph-y-mode').value === 'fixed' ? 'fixed' : 'auto',
    yMin: Math.min(yMin, yMax),
    yMax: Math.max(yMin, yMax),
  };
  $('graph-dialog').close();
  renderAll();
  scheduleRun();
}

function openTfViewerDialog(node) {
  $('tf-viewer-dialog').dataset.nodeId = node.id;
  $('tf-viewer-dialog').dataset.params = JSON.stringify(tfViewerDefaults(node.params || {}));
  renderTfViewerConfigFields();
  $('tf-viewer-dialog').showModal();
}

function renderTfViewerConfigFields() {
  const p = tfViewerDefaults(JSON.parse($('tf-viewer-dialog').dataset.params || '{}'));
  $('tf-viewer-grid-step').value = p.gridStep;
  $('tf-viewer-grid-size').value = p.gridSize;
  $('tf-viewer-axis-size').value = p.axisSize;
  $('tf-viewer-labels').checked = p.showLabels;
  $('viewer-enable-tf').checked = p.enableTf;
  $('viewer-pointcloud-count').value = p.pointCloudCount;
  $('viewer-pointcloud-style').value = p.pointCloudStyle;
  $('viewer-pointcloud-size').value = p.pointCloudSize;
  $('viewer-pointcloud-color').value = p.pointCloudColor;
  $('viewer-pointcloud-opacity').value = p.pointCloudOpacity;
  $('viewer-occupancy-grid-count').value = p.occupancyGridCount;
  $('viewer-occupancy-grid-color-scheme').value = p.occupancyGridColorScheme;
  $('viewer-occupancy-grid-alpha').value = p.occupancyGridAlpha;
  $('viewer-occupancy-grid-draw-behind').checked = p.occupancyGridDrawBehind;
  $('viewer-robot-model').checked = p.showRobotModel;
  $('viewer-robot-model-path').value = p.robotModelPath;
  $('viewer-robot-model-color').value = p.robotModelColor;
  $('viewer-robot-model-opacity').value = p.robotModelOpacity;
  const select = $('viewer-robot-model-select');
  if (select) {
    select.onclick = async () => {
      select.disabled = true;
      try {
        const selected = await selectUrdfFileFromServer();
        if (selected?.path) {
          $('viewer-robot-model-path').value = selected.path;
          const model = await fetchRobotModel(selected.path);
          const params = tfViewerDefaults(JSON.parse($('tf-viewer-dialog').dataset.params || '{}'));
          params.robotModelPath = selected.path;
          params.robotModel = model;
          params.showRobotModel = true;
          $('tf-viewer-dialog').dataset.params = JSON.stringify(params);
          renderTfViewerConfigFields();
        }
      } catch (err) {
        setExecutionStatus('error', `Robot model load failed: ${err.message}`);
      } finally {
        select.disabled = false;
      }
    };
  }
}

function saveTfViewerDialog(ev) {
  ev.preventDefault();
  ev.stopPropagation();
  const node = nodeFor($('tf-viewer-dialog').dataset.nodeId);
  if (!node) return;
  const current = tfViewerDefaults(node.params || {});
  const gridStep = Number($('tf-viewer-grid-step').value);
  const gridSize = Number($('tf-viewer-grid-size').value);
  const axisSize = Number($('tf-viewer-axis-size').value);
  const pointCloudCount = Number($('viewer-pointcloud-count').value);
  const pointCloudSize = Number($('viewer-pointcloud-size').value);
  const pointCloudOpacity = Number($('viewer-pointcloud-opacity').value);
  const occupancyGridCount = Number($('viewer-occupancy-grid-count').value);
  const occupancyGridAlpha = Number($('viewer-occupancy-grid-alpha').value);
  const robotModelOpacity = Number($('viewer-robot-model-opacity').value);
  const robotModelPath = $('viewer-robot-model-path').value.trim();
  const finish = (robotModel) => {
    node.params = {
      ...(node.params || {}),
      ...current,
      gridStep: Number.isFinite(gridStep) ? Math.max(0.01, gridStep) : current.gridStep,
      gridSize: Number.isFinite(gridSize) ? Math.max(0.1, gridSize) : current.gridSize,
      axisSize: Number.isFinite(axisSize) ? Math.max(0.01, axisSize) : current.axisSize,
      showLabels: $('tf-viewer-labels').checked,
      enableTf: $('viewer-enable-tf').checked,
      pointCloudCount: Number.isFinite(pointCloudCount) ? Math.max(0, Math.min(16, Math.floor(pointCloudCount))) : current.pointCloudCount,
      pointCloudStyle: $('viewer-pointcloud-style').value === 'circle' ? 'circle' : 'square',
      pointCloudSize: Number.isFinite(pointCloudSize) ? Math.max(0.001, pointCloudSize) : current.pointCloudSize,
      pointCloudColor: $('viewer-pointcloud-color').value || current.pointCloudColor,
      pointCloudOpacity: Number.isFinite(pointCloudOpacity) ? Math.max(0, Math.min(1, pointCloudOpacity)) : current.pointCloudOpacity,
      occupancyGridCount: Number.isFinite(occupancyGridCount) ? Math.max(0, Math.min(16, Math.floor(occupancyGridCount))) : current.occupancyGridCount,
      occupancyGridColorScheme: ['map', 'costmap', 'raw'].includes($('viewer-occupancy-grid-color-scheme').value) ? $('viewer-occupancy-grid-color-scheme').value : current.occupancyGridColorScheme,
      occupancyGridAlpha: Number.isFinite(occupancyGridAlpha) ? Math.max(0, Math.min(1, occupancyGridAlpha)) : current.occupancyGridAlpha,
      occupancyGridDrawBehind: $('viewer-occupancy-grid-draw-behind').checked,
      showRobotModel: $('viewer-robot-model').checked,
      robotModelPath,
      robotModel,
      robotModelColor: $('viewer-robot-model-color').value || current.robotModelColor,
      robotModelOpacity: Number.isFinite(robotModelOpacity) ? Math.max(0, Math.min(1, robotModelOpacity)) : current.robotModelOpacity,
    };
    apply3dViewerPorts(node, node.params);
    if (state.nodeViews[node.id]?.kind === 'tf3d') {
      state.nodeViews[node.id] = withTfViewerParams(node.id, state.nodeViews[node.id]);
    }
    $('tf-viewer-dialog').close();
    renderAll();
    scheduleRun();
  };
  if (robotModelPath && robotModelPath !== current.robotModelPath) {
    fetchRobotModel(robotModelPath).then(finish).catch((err) => {
      setExecutionStatus('error', `Robot model load failed: ${err.message}`);
      finish(current.robotModel);
    });
  } else {
    finish(current.robotModel);
  }
}

function renderAll() {
  commitHistory();
  renderNodeList();
  renderNodes();
  renderLinks();
  renderInspector();
  renderSelection();
  applyView();
}

function renderNodeList() {
  const list = $('node-list');
  list.innerHTML = '';
  state.nodes.forEach((node) => {
    const item = document.createElement('button');
    item.className = 'node-list-item';
    item.innerHTML = `<span>${escapeHtml(node.name)}</span><small>${node.inputs.length} in / ${node.outputs.length} out</small>`;
    item.onclick = () => {
      state.selectedNode = node.id;
      state.selectedLink = null;
      renderAll();
    };
    list.appendChild(item);
  });
}

function renderNodes() {
  const root = $('nodes');
  root.innerHTML = '';
  state.nodes.forEach((node) => {
    const el = document.createElement('article');
    el.className = `node ros-node${['3d_viewer', 'tf_viewer'].includes(node.toolType) ? ' node-no-outputs' : ''}`;
    el.dataset.id = node.id;
    el.style.left = `${node.x}px`;
    el.style.top = `${node.y}px`;
    const nodeWidth = nodeWidthValue(node);
    const nodeHeight = nodeHeightValue(node);
    if (nodeWidth > DEFAULT_NODE_WIDTH) {
      el.style.width = `${nodeWidth}px`;
      el.classList.add('resized');
    }
    if (nodeHeight > DEFAULT_NODE_MIN_HEIGHT) {
      el.style.height = `${nodeHeight}px`;
      el.classList.add('resized');
    }
    const runtimeSummary = nodeRuntimeSummary(node);
    el.innerHTML = `
      <div class="node-title">
        <div class="node-title-text"><strong data-node-title-name title="Double-click to rename">${escapeHtml(node.name)}</strong><small>${escapeHtml(nodeKindLabel(node))}${runtimeSummary ? ` / ${escapeHtml(runtimeSummary)}` : ''}</small></div>
        <button class="delete" title="Delete">x</button>
      </div>
      <div class="ports">
        <div class="port-list inputs"></div>
        <div class="port-list outputs"></div>
      </div>
      ${toolActionHtml(node)}
      ${viewNodeHtml(node)}
      ${node.toolType ? '' : `<div class="node-actions">
          <button data-action="config">Configure</button>
          <button data-action="imports">Import Code</button>
          <button data-action="requirements">Requirements</button>
          <button data-action="loop">Main Loop Code</button>
          ${timerActionButtons(node)}
          ${node.inputs.filter((input) => (input.receiveMode || 'callback') === 'callback').map((input) => `<button data-callback-input="${escapeAttr(input.id)}">Callback: ${escapeHtml(input.name)}</button>`).join('')}
        </div>`}
      ${resizeHandlesHtml()}`;
    root.appendChild(el);
    el.onclick = (ev) => selectNode(ev, node.id);
    el.querySelector('.delete').onclick = (ev) => {
      ev.stopPropagation();
      deleteNode(node.id);
    };
    const configButton = el.querySelector('[data-action="config"]');
    if (configButton) {
      configButton.onclick = (ev) => {
        ev.stopPropagation();
        openNodeDialog(node);
      };
    }
    const loopButton = el.querySelector('[data-action="loop"]');
    if (loopButton) {
      loopButton.onclick = (ev) => {
        ev.stopPropagation();
        openCodeDialog(node, 'loopCode');
      };
    }
    const importsButton = el.querySelector('[data-action="imports"]');
    if (importsButton) {
      importsButton.onclick = (ev) => {
        ev.stopPropagation();
        openCodeDialog(node, 'importCode');
      };
    }
    const requirementsButton = el.querySelector('[data-action="requirements"]');
    if (requirementsButton) {
      requirementsButton.onclick = (ev) => {
        ev.stopPropagation();
        openCodeDialog(node, 'requirements');
      };
    }
    el.querySelectorAll('[data-timer-input]').forEach((button) => {
      button.onclick = (ev) => {
        ev.stopPropagation();
        openCodeDialog(node, `timer:${button.dataset.timerInput}`);
      };
    });
    el.querySelectorAll('[data-callback-input]').forEach((button) => {
      button.onclick = (ev) => {
        ev.stopPropagation();
        openCodeDialog(node, `callback:${button.dataset.callbackInput}`);
      };
    });
    bindToolActions(el, node);
    bindNodeTitleEdit(el, node);
    makeNodeDraggable(el, node);
    makeNodeResizable(el, node);
    renderPorts(el.querySelector('.inputs'), node, node.inputs, 'input');
    renderPorts(el.querySelector('.outputs'), node, node.outputs, 'output');
    // Restore canvas views immediately after node element is added to DOM
    const viewEl = el.querySelector('[data-node-view]');
    if (viewEl) patchNodeViewEl(viewEl, state.nodeViews[node.id]);
  });
}

function nodeWidthValue(node) {
  return Math.max(DEFAULT_NODE_WIDTH, Math.round(Number(node.width || DEFAULT_NODE_WIDTH)));
}

function nodeHeightValue(node) {
  return Math.max(DEFAULT_NODE_MIN_HEIGHT, Math.round(Number(node.height || DEFAULT_NODE_MIN_HEIGHT)));
}

function resizeHandlesHtml() {
  return `<div class="resize-handle nw" data-resize-corner="nw"></div>
      <div class="resize-handle ne" data-resize-corner="ne"></div>
      <div class="resize-handle sw" data-resize-corner="sw"></div>
      <div class="resize-handle se" data-resize-corner="se"></div>`;
}

function nodeKindLabel(node) {
  if (!node.toolType) return 'lwrclpy Custom Node';
  if (['topic_input', 'topic_output'].includes(node.toolType)) return 'Boundary Node';
  return 'Tool Node';
}

function inspectorHint(node) {
  if (!node.toolType) {
    const timers = normalizeTimers(node);
    const runtime = nodeRuntimeSummary(node);
    return `${node.inputs.length} subscriptions / ${node.outputs.length} publishers${timers.length ? ` / ${timers.length} timer${timers.length === 1 ? '' : 's'}` : ''}${runtime ? ` / ${runtime}` : ''}`;
  }
  if (['topic_input', 'topic_output'].includes(node.toolType)) {
    return 'Graph boundary only. Sub/Pub is handled by the connected processing node.';
  }
  return 'Built-in processing/view node.';
}

function effectiveVideoHz(node) {
  const p = node.params || {};
  const baseHz = Math.max(0.01, Number(p.detectedFps || p.nativeFps || p.sourceFps || p.publishHz || 30));
  return Math.max(0.01, baseHz / (videoFrameSkip(node) + 1));
}

function videoFrameSkip(node) {
  return Math.max(0, Math.floor(Number(node?.params?.frameSkip || 0)));
}

function videoOutputType(node) {
  const outputType = node?.outputs?.[0]?.dataType || node?.params?.outputType || VIDEO_RAW_IMAGE_TYPE;
  return outputType === VIDEO_COMPRESSED_IMAGE_TYPE ? VIDEO_COMPRESSED_IMAGE_TYPE : VIDEO_RAW_IMAGE_TYPE;
}

function isVideoImageType(dataType) {
  return [VIDEO_RAW_IMAGE_TYPE, VIDEO_COMPRESSED_IMAGE_TYPE].includes(String(dataType || ''));
}

function acceptsVideoImageType(node) {
  return ['image_view', 'image_file_save', 'topic_hz_monitor'].includes(node?.toolType);
}

function applyVideoOutputType(node, outputType) {
  const normalized = outputType === VIDEO_COMPRESSED_IMAGE_TYPE ? VIDEO_COMPRESSED_IMAGE_TYPE : VIDEO_RAW_IMAGE_TYPE;
  if (!node.outputs?.length) return;
  node.outputs[0].dataType = normalized;
  node.params = { ...(node.params || {}), outputType: normalized };
  state.links.forEach((link) => {
    if (link.fromNode !== node.id || link.fromPort !== node.outputs[0].id) return;
    const dst = nodeFor(link.toNode);
    if (!acceptsVideoImageType(dst)) return;
    const input = dst.inputs?.find((port) => port.id === link.toPort);
    if (input && isVideoImageType(input.dataType)) input.dataType = normalized;
  });
}

function mcapRecordTopicCount(node) {
  return Math.max(1, Math.min(64, Math.floor(Number(node?.params?.topicCount || node?.inputs?.length || 1))));
}

function applyMcapRecordTopicCount(node, count) {
  const nextCount = Math.max(1, Math.min(64, Math.floor(Number(count || 1))));
  const nextInputs = [];
  for (let index = 0; index < nextCount; index += 1) {
    const existing = node.inputs?.[index];
    nextInputs.push(existing ? { ...existing } : {
      id: `in${index + 1}`,
      name: `topic${index + 1}`,
      dataType: '',
      receiveMode: 'manual',
      callbackCode: '',
    });
  }
  const keptInputIds = new Set(nextInputs.map((port) => port.id));
  state.links = state.links.filter((link) => link.toNode !== node.id || keptInputIds.has(link.toPort));
  node.inputs = nextInputs;
  node.params = { ...(node.params || {}), topicCount: nextCount };
}

function tfMergeTopicCount(node) {
  return Math.max(1, Math.min(64, Math.floor(Number(node?.params?.topicCount || node?.inputs?.length || 2))));
}

function tfViewerDefaults(params = {}) {
  const numberParam = (key, fallback, min) => {
    const value = Number(params[key]);
    return Number.isFinite(value) ? Math.max(min, value) : fallback;
  };
  return {
    rootFrame: String(params.rootFrame || ''),
    enableTf: params.enableTf !== false,
    pointCloudCount: Math.max(0, Math.min(16, Math.floor(Number(params.pointCloudCount || 0)))),
    pointCloudStyle: ['square', 'circle'].includes(String(params.pointCloudStyle || 'square')) ? String(params.pointCloudStyle || 'square') : 'square',
    pointCloudSize: numberParam('pointCloudSize', 0.03, 0.001),
    pointCloudColor: /^#[0-9a-fA-F]{6}$/.test(String(params.pointCloudColor || '')) ? String(params.pointCloudColor) : '#ffffff',
    pointCloudOpacity: Math.max(0, Math.min(1, numberParam('pointCloudOpacity', 1, 0))),
    occupancyGridCount: Math.max(0, Math.min(16, Math.floor(Number(params.occupancyGridCount || 0)))),
    occupancyGridColorScheme: ['map', 'costmap', 'raw'].includes(String(params.occupancyGridColorScheme || 'map')) ? String(params.occupancyGridColorScheme || 'map') : 'map',
    occupancyGridAlpha: Math.max(0, Math.min(1, numberParam('occupancyGridAlpha', 0.7, 0))),
    occupancyGridDrawBehind: params.occupancyGridDrawBehind !== false,
    showRobotModel: params.showRobotModel === true,
    robotModelPath: String(params.robotModelPath || ''),
    robotModel: params.robotModel || null,
    robotModelColor: /^#[0-9a-fA-F]{6}$/.test(String(params.robotModelColor || '')) ? String(params.robotModelColor) : '#9aa4b2',
    robotModelOpacity: Math.max(0, Math.min(1, numberParam('robotModelOpacity', 0.45, 0))),
    gridStep: numberParam('gridStep', 0.25, 0.01),
    gridSize: numberParam('gridSize', 4, 0.1),
    axisSize: numberParam('axisSize', 0.35, 0.01),
    showLabels: params.showLabels !== false,
  };
}

function apply3dViewerPorts(node, params = node.params || {}, pruneLinks = true) {
  const p = tfViewerDefaults(params);
  const inputs = [];
  if (p.enableTf) inputs.push({ id: 'tf_in', name: 'TF', dataType: 'tf2_msgs/msg/TFMessage', receiveMode: 'manual', callbackCode: '' });
  for (let index = 0; index < p.pointCloudCount; index += 1) {
    const existing = (node.inputs || []).find((port) => port.id === `cloud${index + 1}`);
    inputs.push(existing ? { ...existing, name: existing.name || `cloud${index + 1}`, dataType: 'sensor_msgs/msg/PointCloud2', receiveMode: 'manual', callbackCode: '' } : {
      id: `cloud${index + 1}`,
      name: `cloud${index + 1}`,
      dataType: 'sensor_msgs/msg/PointCloud2',
      receiveMode: 'manual',
      callbackCode: '',
    });
  }
  for (let index = 0; index < p.occupancyGridCount; index += 1) {
    const existing = (node.inputs || []).find((port) => port.id === `grid${index + 1}`);
    inputs.push(existing ? { ...existing, name: existing.name || `grid${index + 1}`, dataType: 'nav_msgs/msg/OccupancyGrid', receiveMode: 'manual', callbackCode: '' } : {
      id: `grid${index + 1}`,
      name: `grid${index + 1}`,
      dataType: 'nav_msgs/msg/OccupancyGrid',
      receiveMode: 'manual',
      callbackCode: '',
    });
  }
  const kept = new Set(inputs.map((port) => port.id));
  if (pruneLinks) state.links = state.links.filter((link) => link.toNode !== node.id || kept.has(link.toPort));
  node.inputs = inputs;
  node.outputs = [];
  node.params = { ...(node.params || {}), ...p };
}

function applyTfMergeTopicCount(node, count) {
  const nextCount = Math.max(1, Math.min(64, Math.floor(Number(count || 2))));
  const nextInputs = [];
  for (let index = 0; index < nextCount; index += 1) {
    const existing = node.inputs?.[index];
    nextInputs.push(existing ? { ...existing, dataType: 'tf2_msgs/msg/TFMessage', receiveMode: 'manual', callbackCode: '' } : {
      id: `in${index + 1}`,
      name: 'TF',
      dataType: 'tf2_msgs/msg/TFMessage',
      receiveMode: 'manual',
      callbackCode: '',
    });
  }
  const keptInputIds = new Set(nextInputs.map((port) => port.id));
  state.links = state.links.filter((link) => link.toNode !== node.id || keptInputIds.has(link.toPort));
  node.inputs = nextInputs;
  node.outputs = [
    { id: 'tf', name: 'TF', dataType: 'tf2_msgs/msg/TFMessage' },
  ];
  node.params = { ...(node.params || {}), topicCount: nextCount };
}

function timerActionButtons(node) {
  const timers = normalizeTimers(node);
  return timers.map((timer) => `<button data-timer-input="${escapeAttr(timer.id)}">Timer: ${escapeHtml(timer.name)}</button>`).join('');
}

function toolActionHtml(node) {
  if (node.toolType === 'image_file_input') {
    const mode = node.params?.publishMode || 'oneshot';
    const hz = Number(node.params?.publishHz || 1);
    return `<div class="node-actions tool-actions">
      <label class="file-button">Load Image<input data-tool-file="image" type="file" accept="image/*"></label>
      <label class="tool-field"><span>Send</span><select data-tool-image-mode><option value="oneshot" ${mode === 'oneshot' ? 'selected' : ''}>One Shot</option><option value="rate" ${mode === 'rate' ? 'selected' : ''}>Rate</option></select></label>
      <label class="tool-field"><span>Hz</span><input data-tool-image-hz type="number" min="0.01" step="0.1" value="${escapeAttr(hz)}"></label>
    </div>`;
  }
  if (node.toolType === 'video_file_input') {
    const loopChecked = node.params?.loop ? 'checked' : '';
    const videoPath = node.params?.videoPath || '';
    const detectedFps = Number(node.params?.detectedFps || 0);
    const frameSkip = videoFrameSkip(node);
    const outputType = videoOutputType(node);
    const fpsLabel = detectedFps > 0 ? detectedFps.toFixed(2) + ' fps' : 'auto (30 fps)';
    return `<div class="node-actions tool-actions">
      <label class="tool-field tool-field-wide"><span>Path</span><input data-tool-video-path type="text" value="${escapeAttr(videoPath)}" placeholder="No video selected" readonly tabindex="-1"></label>
      <button data-action="select-video-file">Select Video</button>
      <label class="tool-field"><span>FPS</span><span class="tool-value-display">${escapeHtml(fpsLabel)}</span></label>
      <label class="tool-field"><span>Output</span><select data-tool-video-output-type><option value="${VIDEO_RAW_IMAGE_TYPE}" ${outputType === VIDEO_RAW_IMAGE_TYPE ? 'selected' : ''}>Raw Image</option><option value="${VIDEO_COMPRESSED_IMAGE_TYPE}" ${outputType === VIDEO_COMPRESSED_IMAGE_TYPE ? 'selected' : ''}>Compressed JPEG</option></select></label>
      <label class="tool-field"><span>Skip</span><input data-tool-video-frame-skip type="number" min="0" step="1" value="${escapeAttr(frameSkip)}"></label>
      <label class="tool-check"><input data-tool-video-loop type="checkbox" ${loopChecked}> Loop</label>
    </div>`;
  }
  if (node.toolType === 'mcap_file_input') {
    const p = node.params || {};
    const mcapPath = p.mcapPath || '';
    const loopChecked = p.loop ? 'checked' : '';
    const playbackRate = Math.max(0.001, Number(p.playbackRate || 1));
    const channels = Array.isArray(p.mcapChannels) ? p.mcapChannels : [];
    const ros2Channels = channels.filter((channel) => isRos2McapChannel(channel));
    const fileCount = Math.max(1, Number(p.fileCount || (Array.isArray(p.mcapFiles) ? p.mcapFiles.length : 1)));
    const fileText = fileCount > 1 ? ` / ${fileCount} files` : '';
    const summary = p.mcapPath
      ? `${channels.length} topics (${ros2Channels.length} ROS 2 topics) / ${formatDuration(Number(p.durationSec || 0))}${fileText}${p.probeError ? ' / probe error' : ''}${p.metadataError ? ' / metadata error' : ''}`
      : 'No MCAP selected';
    return `<div class="node-actions tool-actions">
      <label class="tool-field tool-field-wide"><span>Path</span><input data-tool-mcap-path type="text" value="${escapeAttr(mcapPath)}" placeholder="/path/to/file.mcap or /path/to/rosbag"></label>
      <button data-action="select-mcap-file">Select MCAP/Bag</button>
      <label class="tool-field"><span>Rate</span><input data-tool-mcap-rate type="number" min="0.001" step="0.1" value="${escapeAttr(playbackRate)}"></label>
      <label class="tool-check"><input data-tool-mcap-loop type="checkbox" ${loopChecked}> Loop</label>
      <div class="tool-summary">${escapeHtml(summary)}</div>
    </div>`;
  }
  if (node.toolType === 'urdf_static_tf_publisher') {
    const p = node.params || {};
    const urdfPath = p.urdfPath || '';
    const summary = urdfPath ? `Publishing fixed joints to /tf_static from ${p.fileName || urdfPath}` : 'No URDF/Xacro selected';
    return `<div class="node-actions tool-actions">
      <label class="tool-field tool-field-wide"><span>URDF</span><input data-tool-urdf-path type="text" value="${escapeAttr(urdfPath)}" placeholder="/path/to/robot.urdf or robot.xacro"></label>
      <button data-action="select-urdf-file">Select URDF/Xacro</button>
      <div class="tool-summary">${escapeHtml(summary)}</div>
    </div>`;
  }
  if (node.toolType === 'tf_merge') {
    const topicCount = tfMergeTopicCount(node);
    return `<div class="node-actions tool-actions">
      <label class="tool-field"><span>Inputs</span><input data-tool-tf-merge-count type="number" min="1" max="64" step="1" value="${escapeAttr(topicCount)}"></label>
      <div class="tool-summary">Graph-only TF aggregation edge node</div>
    </div>`;
  }
  if (node.toolType === 'tf_viewer' || node.toolType === '3d_viewer') {
    return `<div class="node-actions tool-actions">
      <button data-action="tf-viewer-settings">Configure</button>
    </div>`;
  }
  if (node.toolType === 'mcap_record') {
    const p = node.params || {};
    const mcapPath = p.mcapPath || '';
    const topicCount = mcapRecordTopicCount(node);
    const splitSizeMb = Math.max(0, Number(p.splitSizeMb || 0));
    const splitText = splitSizeMb > 0 ? ` / split ${splitSizeMb} MB` : '';
    const summary = mcapPath ? `Recording ${topicCount} topic${topicCount === 1 ? '' : 's'} to ROS 2 bag ${mcapPath}${splitText}` : 'No ROS 2 bag output selected';
    return `<div class="node-actions tool-actions">
      <label class="tool-field tool-field-wide"><span>Bag</span><input data-tool-mcap-record-path type="text" value="${escapeAttr(mcapPath)}" placeholder="/path/to/rosbag_name"></label>
      <button data-action="select-mcap-record-file">Save Bag</button>
      <label class="tool-field"><span>Topics</span><input data-tool-mcap-record-count type="number" min="1" max="64" step="1" value="${escapeAttr(topicCount)}"></label>
      <label class="tool-field"><span>Split MB</span><input data-tool-mcap-record-split type="number" min="0" step="100" value="${escapeAttr(splitSizeMb)}"></label>
      <div class="tool-summary">${escapeHtml(summary)}</div>
    </div>`;
  }
  if (node.toolType === 'image_crop_resize') {
    const p = imageCropResizeDefaults(node.params || {});
    const positionDisabled = p.cropCenter ? 'disabled' : '';
    const heightDisabled = p.keepAspect ? 'disabled' : '';
    return `<div class="node-actions tool-actions image-process-actions">
      <label class="tool-check"><input data-tool-crop-enabled type="checkbox" ${p.cropEnabled ? 'checked' : ''}> Crop</label>
      ${p.cropEnabled ? `
        <label class="tool-field"><span>Crop X</span><input data-tool-crop-resize="cropX" type="number" min="0" step="1" value="${escapeAttr(p.cropX)}" ${positionDisabled}></label>
        <label class="tool-field"><span>Crop Y</span><input data-tool-crop-resize="cropY" type="number" min="0" step="1" value="${escapeAttr(p.cropY)}" ${positionDisabled}></label>
        <label class="tool-field"><span>Crop W</span><input data-tool-crop-resize="cropWidth" type="number" min="0" step="1" value="${escapeAttr(p.cropWidth)}"></label>
        <label class="tool-field"><span>Crop H</span><input data-tool-crop-resize="cropHeight" type="number" min="0" step="1" value="${escapeAttr(p.cropHeight)}"></label>
        <label class="tool-check"><input data-tool-crop-resize-center type="checkbox" ${p.cropCenter ? 'checked' : ''}> Center Crop</label>` : ''}
      <label class="tool-check"><input data-tool-resize-enabled type="checkbox" ${p.resizeEnabled ? 'checked' : ''}> Resize</label>
      ${p.resizeEnabled ? `
        <label class="tool-field"><span>Resize W</span><input data-tool-crop-resize="targetWidth" type="number" min="0" step="1" value="${escapeAttr(p.targetWidth)}"></label>
        <label class="tool-field"><span>Resize H</span><input data-tool-crop-resize="targetHeight" type="number" min="0" step="1" value="${escapeAttr(p.targetHeight)}" ${heightDisabled}></label>
        <label class="tool-check"><input data-tool-crop-resize-aspect type="checkbox" ${p.keepAspect ? 'checked' : ''}> Keep Aspect</label>` : ''}
    </div>`;
  }
  if (node.toolType === 'llm_text') {
    const p = llmTextDefaults(node.params || {});
    return `<div class="node-actions tool-actions llm-actions">
      <label class="tool-field"><span>Provider</span><select data-tool-llm="provider">
        <option value="ollama" ${p.provider === 'ollama' ? 'selected' : ''}>Ollama</option>
        <option value="openai" ${p.provider === 'openai' ? 'selected' : ''}>OpenAI</option>
        <option value="openai_compatible" ${p.provider === 'openai_compatible' ? 'selected' : ''}>OpenAI Compatible</option>
        <option value="lmstudio" ${p.provider === 'lmstudio' ? 'selected' : ''}>LM Studio</option>
      </select></label>
      <label class="tool-field"><span>Model</span><input data-tool-llm="model" type="text" value="${escapeAttr(p.model)}" placeholder="llama3.2"></label>
      <label class="tool-field tool-field-wide"><span>Base</span><input data-tool-llm="apiBase" type="text" value="${escapeAttr(p.apiBase)}" placeholder="provider default"></label>
      <label class="tool-field"><span>Key Env</span><input data-tool-llm="apiKeyEnv" type="text" value="${escapeAttr(p.apiKeyEnv)}" placeholder="OPENAI_API_KEY"></label>
      <label class="tool-field"><span>Temp</span><input data-tool-llm="temperature" type="number" min="0" max="2" step="0.1" value="${escapeAttr(p.temperature)}"></label>
      <label class="tool-field"><span>Tokens</span><input data-tool-llm="maxTokens" type="number" min="1" step="1" value="${escapeAttr(p.maxTokens)}"></label>
      <label class="tool-field"><span>Timeout</span><input data-tool-llm="timeoutSec" type="number" min="1" step="1" value="${escapeAttr(p.timeoutSec)}"></label>
      <label class="tool-field tool-field-wide tool-field-textarea"><span>System</span><textarea data-tool-llm="systemPrompt" rows="2" placeholder="optional">${escapeHtml(p.systemPrompt)}</textarea></label>
      <div class="tool-summary">${escapeHtml(llmTextSummary(p))}</div>
    </div>`;
  }
  if (node.toolType === 'string_view') {
    const p = stringViewDefaults(node.params || {});
    return `<div class="node-actions tool-actions string-view-actions">
      <label class="tool-field"><span>Mode</span><select data-tool-string-view="mode">
        <option value="replace" ${p.mode === 'replace' ? 'selected' : ''}>Replace</option>
        <option value="append" ${p.mode === 'append' ? 'selected' : ''}>Append</option>
      </select></label>
      <label class="tool-field"><span>Max</span><input data-tool-string-view="maxChars" type="number" min="1" step="1000" value="${escapeAttr(p.maxChars)}"></label>
      <button data-action="clear-string-view">Clear</button>
      <div class="tool-summary">${escapeHtml(stringViewSummary(p))}</div>
    </div>`;
  }
  if (node.toolType === 'function_generator') {
    return functionGeneratorHtml(node);
  }
  if (node.toolType === 'graph_view') {
    const p = graphDefaults(node.params || {});
    return `<div class="node-actions tool-actions">
      <button data-action="graph-settings">Graph Settings</button>
      <div class="tool-summary">${escapeHtml(graphSummary(p))}</div>
    </div>`;
  }
  return '';
}

function functionGeneratorHtml(node) {
  const p = signalDefaults(node.params || {});
  return `<div class="node-actions tool-actions signal-actions">
    <button data-action="signal-settings">Signal Settings</button>
    <div class="tool-summary">${escapeHtml(signalSummary(p))}</div>
  </div>`;
}

function imageCropResizeDefaults(params) {
  return {
    cropEnabled: Boolean(params.cropEnabled),
    cropX: Math.max(0, Math.floor(Number(params.cropX || 0))),
    cropY: Math.max(0, Math.floor(Number(params.cropY || 0))),
    cropWidth: Math.max(0, Math.floor(Number(params.cropWidth || 0))),
    cropHeight: Math.max(0, Math.floor(Number(params.cropHeight || 0))),
    cropCenter: Boolean(params.cropCenter),
    resizeEnabled: Boolean(params.resizeEnabled),
    targetWidth: Math.max(0, Math.floor(Number(params.targetWidth || 0))),
    targetHeight: Math.max(0, Math.floor(Number(params.targetHeight || 0))),
    keepAspect: params.keepAspect !== false,
  };
}

function imageCropResizeAspectHeight(params) {
  const p = imageCropResizeDefaults(params || {});
  if (!p.keepAspect || p.targetWidth <= 0) return p.targetHeight;
  const sourceW = p.cropWidth > 0 ? p.cropWidth : Number(params.sourceWidth || 0);
  const sourceH = p.cropHeight > 0 ? p.cropHeight : Number(params.sourceHeight || 0);
  if (sourceW > 0 && sourceH > 0) return Math.max(1, Math.round(p.targetWidth * sourceH / sourceW));
  return p.targetHeight;
}

function llmTextDefaults(params) {
  const provider = ['ollama', 'openai', 'openai_compatible', 'lmstudio'].includes(params.provider) ? params.provider : 'ollama';
  return {
    provider,
    model: String(params.model || (provider === 'openai' ? 'gpt-4.1-mini' : 'llama3.2')),
    apiBase: String(params.apiBase || ''),
    apiKeyEnv: String(params.apiKeyEnv || 'OPENAI_API_KEY'),
    systemPrompt: String(params.systemPrompt || ''),
    temperature: Math.max(0, Math.min(2, Number(params.temperature ?? 0.2))),
    maxTokens: Math.max(1, Math.floor(Number(params.maxTokens || 512))),
    timeoutSec: Math.max(1, Math.floor(Number(params.timeoutSec || 60))),
  };
}

function llmTextSummary(p) {
  const base = p.apiBase ? ` / ${p.apiBase}` : '';
  const key = p.provider === 'openai' || p.provider === 'openai_compatible' ? ` / key ${p.apiKeyEnv || 'unset'}` : '';
  return `${p.provider} / ${p.model}${base}${key}`;
}

function stringViewDefaults(params) {
  return {
    mode: params.mode === 'append' ? 'append' : 'replace',
    maxChars: Math.max(1, Math.floor(Number(params.maxChars || 20000))),
  };
}

function stringViewSummary(p) {
  return p.mode === 'append' ? `Append incoming text / keep last ${p.maxChars} chars` : 'Replace with latest message';
}

function signalSummary(p) {
  const type = String(p.signalType || 'sine');
  const pub = `pub ${p.publishHz || 10} Hz`;
  const dds = p.ddsTopic ? `, DDS ${p.ddsTopic}` : '';
  if (type === 'step') return `Step: ${p.initialValue} -> ${p.finalValue} at ${p.stepTime}s, ${pub}${dds}`;
  if (type === 'square') return `Square: ${p.amplitude} amp, ${p.frequency} Hz, ${p.dutyCycle}%, ${pub}${dds}`;
  if (type === 'ramp') return `Ramp: slope ${p.rampSlope}, bias ${p.bias}, ${pub}${dds}`;
  if (type === 'chirp') return `Chirp: ${p.chirpStartFrequency} -> ${p.chirpEndFrequency} Hz, ${pub}${dds}`;
  if (type === 'white_noise') return `White Noise: mean ${p.noiseMean}, std ${p.noiseStd}, ${pub}${dds}`;
  return `Sine: ${p.amplitude} amp, ${p.frequency} Hz, ${pub}${dds}`;
}

function graphSummary(p) {
  const yAxis = p.yAxisMode === 'fixed' ? `Y ${p.yMin}..${p.yMax}` : 'Y auto';
  return `${p.fieldPath || 'data'} / ${Number(p.xAxisSeconds || 10)}s / ${yAxis} / ${Math.max(1, Number(p.sampleLimit || 10000))} samples`;
}

function formatDuration(seconds) {
  const value = Math.max(0, Number(seconds || 0));
  if (value < 60) return `${value.toFixed(2)}s`;
  const minutes = Math.floor(value / 60);
  return `${minutes}m ${(value - minutes * 60).toFixed(1)}s`;
}

function viewNodeHtml(node) {
  if (!['image_file_input', 'video_file_input', 'mcap_file_input', 'urdf_static_tf_publisher', 'tf_merge', 'tf_viewer', '3d_viewer', 'mcap_record', 'function_generator', 'interactive_text_input', 'image_view', 'string_view', 'chat_string_view', 'image_file_save', 'graph_view', 'topic_hz_monitor'].includes(node.toolType)) return '';
  const viewClass = node.toolType === 'video_file_input' ? ' node-view-video' : '';
  const view = state.nodeViews[node.id] || initialNodeView(node);
  if (view && !state.nodeViews[node.id]) state.nodeViews[node.id] = view;
  return `<div class="node-view${viewClass}" data-node-view="${escapeAttr(node.id)}">${renderViewContent(view)}</div>`;
}

function bindToolActions(el, node) {
  const imageInput = el.querySelector('[data-tool-file="image"]');
  if (imageInput) imageInput.onchange = (ev) => loadImageFile(node, ev.target.files[0]);
  const imageMode = el.querySelector('[data-tool-image-mode]');
  if (imageMode) {
    imageMode.onchange = (ev) => {
      node.params = { ...(node.params || {}), publishMode: ev.target.value === 'rate' ? 'rate' : 'oneshot' };
      renderAll();
      scheduleRun();
    };
  }
  const imageHz = el.querySelector('[data-tool-image-hz]');
  if (imageHz) {
    imageHz.onchange = (ev) => {
      node.params = { ...(node.params || {}), publishHz: Math.max(0.01, Number(ev.target.value || 1)) };
      renderAll();
      scheduleRun();
    };
  }
  const videoInput = el.querySelector('[data-tool-file="video"]');
  if (videoInput) videoInput.onchange = (ev) => loadVideoFile(node, ev.target.files[0]);
  const videoPath = el.querySelector('[data-tool-video-path]');
  const selectVideoFile = el.querySelector('[data-action="select-video-file"]');
  const setVideoPath = (path, metadata = {}) => {
    path = String(path || '').trim();
    if (!path) return;
    stopVideoInput(node.id);
    const detectedFps = Number(metadata.fps || metadata.detectedFps || metadata.sourceFps || 0);
    const publishHz = detectedFps > 0 ? Math.round(detectedFps * 100) / 100 : 30;
    node.params = {
      ...(node.params || {}),
      fileName: metadata.fileName || path.split(/[\\/]/).filter(Boolean).pop() || path,
      videoPath: path,
      serverDecode: true,
      publishHz,
      detectedFps: detectedFps > 0 ? publishHz : 0,
      sourceFps: detectedFps > 0 ? detectedFps : undefined,
      sourceWidth: Number(metadata.width || 0) || undefined,
      sourceHeight: Number(metadata.height || 0) || undefined,
      frameSkip: videoFrameSkip(node),
      outputType: videoOutputType(node),
      embeddedVideo: false,
    };
    state.videoPayloadDirty = true;
    state.videoDirtyNodes.add(node.id);
    renderAll();
    pushRunPayloadUpdate();
  };
  if (selectVideoFile) selectVideoFile.onclick = async () => {
    selectVideoFile.disabled = true;
    try {
      const selected = await selectVideoFileFromServer();
      if (selected?.path) setVideoPath(selected.path, selected);
    } catch (err) {
      setExecutionStatus('error', `Video selection failed: ${err.message}`);
    } finally {
      selectVideoFile.disabled = false;
    }
  };
  const videoLoop = el.querySelector('[data-tool-video-loop]');
  if (videoLoop) {
    videoLoop.onchange = (ev) => {
      node.params = { ...(node.params || {}), loop: ev.target.checked };
      const controller = state.videoInputs[node.id];
      if (controller) {
        controller.loop = ev.target.checked;
        controller.video.loop = false;
      }
      if (node.params?.serverDecode && node.params?.videoPath) {
        state.videoPayloadDirty = true;
        state.videoDirtyNodes.add(node.id);
        pushRunPayloadUpdate();
      }
      renderAll();
    };
  }
  const videoOutputTypeInput = el.querySelector('[data-tool-video-output-type]');
  if (videoOutputTypeInput) {
    videoOutputTypeInput.onchange = (ev) => {
      applyVideoOutputType(node, ev.target.value);
      if (node.params?.serverDecode && node.params?.videoPath) {
        state.videoPayloadDirty = true;
        state.videoDirtyNodes.add(node.id);
        pushRunPayloadUpdate();
      }
      renderAll();
      scheduleRun();
    };
  }
  const videoFrameSkipInput = el.querySelector('[data-tool-video-frame-skip]');
  if (videoFrameSkipInput) {
    videoFrameSkipInput.onchange = (ev) => {
      const frameSkip = Math.max(0, Math.floor(Number(ev.target.value || 0)));
      node.params = { ...(node.params || {}), frameSkip };
      const controller = state.videoInputs[node.id];
      if (controller) controller.nextCaptureAt = 0;
      if (node.params?.serverDecode && node.params?.videoPath) {
        state.videoPayloadDirty = true;
        state.videoDirtyNodes.add(node.id);
        pushRunPayloadUpdate();
      }
      renderAll();
    };
  }
  const selectMcapFile = el.querySelector('[data-action="select-mcap-file"]');
  if (selectMcapFile) selectMcapFile.onclick = async () => {
    selectMcapFile.disabled = true;
    try {
      const selected = await selectMcapFileFromServer();
      if (selected?.path) applyMcapSelection(node, selected);
    } catch (err) {
      setExecutionStatus('error', `MCAP selection failed: ${err.message}`);
    } finally {
      selectMcapFile.disabled = false;
    }
  };
  const mcapPath = el.querySelector('[data-tool-mcap-path]');
  if (mcapPath) {
    mcapPath.onchange = async (ev) => {
      const path = String(ev.target.value || '').trim();
      if (!path) return;
      try {
        applyMcapSelection(node, await openMcapFileFromServer(path));
      } catch (err) {
        setExecutionStatus('error', `MCAP open failed: ${err.message}`);
      }
    };
  }
  const selectUrdfFile = el.querySelector('[data-action="select-urdf-file"]');
  if (selectUrdfFile) selectUrdfFile.onclick = async () => {
    selectUrdfFile.disabled = true;
    try {
      const selected = await selectUrdfFileFromServer();
      if (selected?.path) {
        node.params = { ...(node.params || {}), urdfPath: selected.path, fileName: selected.fileName || selected.path.split(/[\\/]/).filter(Boolean).pop() || selected.path };
        renderAll();
        commitHistory();
        scheduleRun();
      }
    } catch (err) {
      setExecutionStatus('error', `URDF selection failed: ${err.message}`);
    } finally {
      selectUrdfFile.disabled = false;
    }
  };
  const urdfPath = el.querySelector('[data-tool-urdf-path]');
  if (urdfPath) {
    urdfPath.onchange = (ev) => {
      const path = String(ev.target.value || '').trim();
      node.params = { ...(node.params || {}), urdfPath: path, fileName: path.split(/[\\/]/).filter(Boolean).pop() || path };
      renderAll();
      commitHistory();
      scheduleRun();
    };
  }
  const tfMergeCount = el.querySelector('[data-tool-tf-merge-count]');
  if (tfMergeCount) {
    tfMergeCount.onchange = (ev) => {
      applyTfMergeTopicCount(node, ev.target.value);
      renderAll();
      commitHistory();
      scheduleRun();
    };
  }
  const mcapRate = el.querySelector('[data-tool-mcap-rate]');
  if (mcapRate) {
    mcapRate.onchange = (ev) => {
      node.params = { ...(node.params || {}), playbackRate: Math.max(0.001, Number(ev.target.value || 1)) };
      renderAll();
      scheduleRun();
    };
  }
  const mcapLoop = el.querySelector('[data-tool-mcap-loop]');
  if (mcapLoop) {
    mcapLoop.onchange = (ev) => {
      node.params = { ...(node.params || {}), loop: ev.target.checked };
      renderAll();
      scheduleRun();
    };
  }
  const selectMcapRecordFile = el.querySelector('[data-action="select-mcap-record-file"]');
  if (selectMcapRecordFile) selectMcapRecordFile.onclick = async () => {
    selectMcapRecordFile.disabled = true;
    try {
      const selected = await selectMcapRecordFileFromServer();
      if (selected?.path) {
        node.params = { ...(node.params || {}), mcapPath: selected.path, fileName: selected.fileName || selected.path.split(/[\\/]/).filter(Boolean).pop() || selected.path };
        renderAll();
        commitHistory();
        scheduleRun();
      }
    } catch (err) {
      setExecutionStatus('error', `MCAP record path selection failed: ${err.message}`);
    } finally {
      selectMcapRecordFile.disabled = false;
    }
  };
  const mcapRecordPath = el.querySelector('[data-tool-mcap-record-path]');
  if (mcapRecordPath) {
    mcapRecordPath.onchange = (ev) => {
      const path = String(ev.target.value || '').trim();
      node.params = { ...(node.params || {}), mcapPath: path, fileName: path.split(/[\\/]/).filter(Boolean).pop() || path };
      renderAll();
      commitHistory();
      scheduleRun();
    };
  }
  const mcapRecordCount = el.querySelector('[data-tool-mcap-record-count]');
  if (mcapRecordCount) {
    mcapRecordCount.onchange = (ev) => {
      applyMcapRecordTopicCount(node, ev.target.value);
      renderAll();
      commitHistory();
      scheduleRun();
    };
  }
  const mcapRecordSplit = el.querySelector('[data-tool-mcap-record-split]');
  if (mcapRecordSplit) {
    mcapRecordSplit.onchange = (ev) => {
      const value = Math.max(0, Number(ev.target.value || 0));
      node.params = { ...(node.params || {}), splitSizeMb: Number.isFinite(value) ? value : 0 };
      renderAll();
      commitHistory();
      scheduleRun();
    };
  }
  const cropResizeInputs = el.querySelectorAll('[data-tool-crop-resize]');
  const cropResizeAspect = el.querySelector('[data-tool-crop-resize-aspect]');
  const cropResizeCenter = el.querySelector('[data-tool-crop-resize-center]');
  const cropEnabled = el.querySelector('[data-tool-crop-enabled]');
  const resizeEnabled = el.querySelector('[data-tool-resize-enabled]');
  if (cropResizeInputs.length || cropResizeAspect || cropResizeCenter || cropEnabled || resizeEnabled) {
    const applyCropResizeParams = () => {
      const next = imageCropResizeDefaults(node.params || {});
      cropResizeInputs.forEach((input) => {
        const key = input.dataset.toolCropResize;
        next[key] = Math.max(0, Math.floor(Number(input.value || 0)));
      });
      next.cropEnabled = cropEnabled ? cropEnabled.checked : next.cropEnabled;
      next.resizeEnabled = resizeEnabled ? resizeEnabled.checked : next.resizeEnabled;
      next.keepAspect = cropResizeAspect ? cropResizeAspect.checked : next.keepAspect;
      next.cropCenter = cropResizeCenter ? cropResizeCenter.checked : next.cropCenter;
      if (next.resizeEnabled && next.keepAspect) next.targetHeight = imageCropResizeAspectHeight(next);
      node.params = { ...(node.params || {}), ...next };
      renderAll();
      commitHistory();
      scheduleRun();
    };
    cropResizeInputs.forEach((input) => {
      input.onchange = applyCropResizeParams;
    });
    if (cropResizeAspect) cropResizeAspect.onchange = applyCropResizeParams;
    if (cropResizeCenter) cropResizeCenter.onchange = applyCropResizeParams;
    if (cropEnabled) cropEnabled.onchange = applyCropResizeParams;
    if (resizeEnabled) resizeEnabled.onchange = applyCropResizeParams;
  }
  const llmInputs = el.querySelectorAll('[data-tool-llm]');
  if (llmInputs.length) {
    const applyLlmParams = () => {
      const next = llmTextDefaults(node.params || {});
      llmInputs.forEach((input) => {
        const key = input.dataset.toolLlm;
        if (key === 'temperature') {
          next[key] = Math.max(0, Math.min(2, Number(input.value || 0)));
        } else if (key === 'maxTokens' || key === 'timeoutSec') {
          next[key] = Math.max(1, Math.floor(Number(input.value || 1)));
        } else {
          next[key] = String(input.value || '').trim();
        }
      });
      node.params = { ...(node.params || {}), ...next };
      renderAll();
      commitHistory();
      scheduleRun();
    };
    llmInputs.forEach((input) => {
      input.onchange = applyLlmParams;
    });
  }
  const stringViewInputs = el.querySelectorAll('[data-tool-string-view]');
  if (stringViewInputs.length) {
    const applyStringViewParams = () => {
      const next = stringViewDefaults(node.params || {});
      stringViewInputs.forEach((input) => {
        const key = input.dataset.toolStringView;
        if (key === 'maxChars') {
          next.maxChars = Math.max(1, Math.floor(Number(input.value || 20000)));
        } else {
          next[key] = String(input.value || '').trim();
        }
      });
      node.params = { ...(node.params || {}), ...next };
      renderAll();
      commitHistory();
      scheduleRun();
    };
    stringViewInputs.forEach((input) => {
      input.onchange = applyStringViewParams;
    });
  }
  const clearStringView = el.querySelector('[data-action="clear-string-view"]');
  if (clearStringView) {
    clearStringView.onclick = (ev) => {
      ev.stopPropagation();
      node.params = { ...(node.params || {}), clearToken: Date.now() };
      state.nodeViews[node.id] = { kind: 'string', text: '', status: 'Cleared' };
      updateNodeViews({ [node.id]: { view: state.nodeViews[node.id] } });
      renderAll();
      commitHistory();
      scheduleRun();
    };
  }
  const signalSettings = el.querySelector('[data-action="signal-settings"]');
  if (signalSettings) {
    signalSettings.onclick = (ev) => {
      ev.stopPropagation();
      openSignalDialog(node);
    };
  }
  const graphSettings = el.querySelector('[data-action="graph-settings"]');
  if (graphSettings) {
    graphSettings.onclick = (ev) => {
      ev.stopPropagation();
      openGraphDialog(node);
    };
  }
  const tfViewerSettings = el.querySelector('[data-action="tf-viewer-settings"]');
  if (tfViewerSettings) {
    tfViewerSettings.onclick = (ev) => {
      ev.stopPropagation();
      openTfViewerDialog(node);
    };
  }
}

function renderPorts(container, node, ports, kind) {
  ports.forEach((port) => {
    const row = document.createElement('div');
    row.className = `port ${kind}`;
    row.dataset.node = node.id;
    row.dataset.port = port.id;
    row.dataset.kind = kind;
    row.dataset.type = port.dataType;
    const dot = document.createElement('span');
    dot.className = 'dot';
    dot.title = port.dataType ? `${port.name}: ${port.dataType}` : `${port.name}: any connected topic type`;
    const label = document.createElement('span');
    label.className = 'port-label';
    label.innerHTML = `<b>${escapeHtml(port.name)}</b>${port.dataType ? `<small>${escapeHtml(port.dataType)}</small>` : '<small>connected topic type</small>'}`;
    if (kind === 'input') {
      row.append(dot, label);
      dot.addEventListener('pointerup', (ev) => finishLinkDrag(ev, row));
    } else {
      row.append(label, dot);
      dot.addEventListener('pointerdown', (ev) => startLinkDrag(ev, row));
    }
    container.appendChild(row);
  });
}

function renderInspector() {
  const box = $('inspector-content');
  const link = state.links.find((item) => item.id === state.selectedLink);
  if (link) {
    const fixedTopic = fixedTfTopicForOutput(link.fromNode, link.fromPort);
    const topicValue = fixedTopic || link.name || defaultLinkTopic(link.fromNode, link.fromPort, link.toNode, link.toPort);
    const tfVisual = isTfLink(link);
    box.innerHTML = `
      <div class="inspector-title">Edge</div>
      <div class="hint">${link.fromNode}.${link.fromPort} -> ${link.toNode}.${link.toPort}</div>
      <label class="field"><span>${tfVisual ? 'TF' : 'Topic Name'}</span><input id="link-name" value="${escapeAttr(tfVisual ? 'TF' : topicValue)}" ${fixedTopic || tfVisual ? 'disabled' : ''}></label>
      <button id="delete-link">Delete Link</button>`;
    $('link-name').oninput = () => {
      if (fixedTopic) return;
      const fallback = defaultLinkTopic(link.fromNode, link.fromPort, link.toNode, link.toPort);
      syncSourceTopicNames(link.fromNode, link.fromPort, $('link-name').value.trim() || fallback);
      renderLinks();
    };
    $('delete-link').onclick = () => deleteLink(link.id);
    return;
  }
  const node = nodeFor(state.selectedNode);
  if (!node) {
    box.innerHTML = '<div class="hint">Create or select a node.</div>';
    return;
  }
  box.innerHTML = `
    <div class="inspector-title">${escapeHtml(node.name)}</div>
    <div class="hint">${escapeHtml(inspectorHint(node))}</div>
    ${node.toolType === 'function_generator' ? `<div class="inspector-actions"><button id="inspect-signal-settings">Signal Settings</button></div>` : ''}
    ${node.toolType === 'graph_view' ? `<div class="inspector-actions"><button id="inspect-graph-settings">Graph Settings</button></div>` : ''}
    ${node.toolType === 'tf_viewer' || node.toolType === '3d_viewer' ? `<div class="inspector-actions"><button id="inspect-tf-viewer-settings">Configure</button></div>` : ''}
    ${node.toolType ? '' : `<div class="inspector-actions">
        <button id="inspect-config">Configure Ports</button>
        <button id="inspect-export-custom-node">Export Custom Node</button>
        <button id="inspect-imports">Import Code</button>
        <button id="inspect-requirements">Requirements</button>
        <button id="inspect-callback">Subscribe Callback Code</button>
        ${timerActionButtons(node)}
        <button id="inspect-loop">Main Loop Code</button>
      </div>`}
    <h3>Inputs</h3>
    ${inputSummary(node)}
    <h3>Outputs</h3>
    ${portSummary(node.outputs)}
    ${node.toolType ? '' : `<h3>Timers</h3>${timerSummary(node)}`}`;
  const inspectConfig = $('inspect-config');
  if (inspectConfig) inspectConfig.onclick = () => openNodeDialog(node);
  const inspectExportCustomNode = $('inspect-export-custom-node');
  if (inspectExportCustomNode) inspectExportCustomNode.onclick = () => exportCustomNodeFromEditor(node);
  const inspectSignalSettings = $('inspect-signal-settings');
  if (inspectSignalSettings) inspectSignalSettings.onclick = () => openSignalDialog(node);
  const inspectGraphSettings = $('inspect-graph-settings');
  if (inspectGraphSettings) inspectGraphSettings.onclick = () => openGraphDialog(node);
  const inspectTfViewerSettings = $('inspect-tf-viewer-settings');
  if (inspectTfViewerSettings) inspectTfViewerSettings.onclick = () => openTfViewerDialog(node);
  const inspectCallback = $('inspect-callback');
  if (inspectCallback) inspectCallback.onclick = () => {
    const firstInput = node.inputs.find((input) => (input.receiveMode || 'callback') === 'callback');
    if (firstInput) openCodeDialog(node, `callback:${firstInput.id}`);
  };
  const inspectLoop = $('inspect-loop');
  if (inspectLoop) inspectLoop.onclick = () => openCodeDialog(node, 'loopCode');
  const inspectImports = $('inspect-imports');
  if (inspectImports) inspectImports.onclick = () => openCodeDialog(node, 'importCode');
  const inspectRequirements = $('inspect-requirements');
  if (inspectRequirements) inspectRequirements.onclick = () => openCodeDialog(node, 'requirements');
  box.querySelectorAll('[data-timer-input]').forEach((button) => {
    button.onclick = () => openCodeDialog(node, `timer:${button.dataset.timerInput}`);
  });
  box.querySelectorAll('[data-callback-port]').forEach((button) => {
    button.onclick = () => openCodeDialog(node, `callback:${button.dataset.callbackPort}`);
  });
}

function portSummary(ports) {
  if (!ports.length) return '<div class="hint">None</div>';
  return ports.map((p) => `<div class="port-summary"><b>${escapeHtml(p.name)}</b><span>${escapeHtml(p.dataType || 'connected topic type')}</span></div>`).join('');
}

function inputSummary(node) {
  if (!node.inputs.length) return '<div class="hint">None</div>';
  return node.inputs.map((p) => {
    const mode = p.receiveMode || 'callback';
    const callbackButton = !node.toolType && mode === 'callback' ? `<button data-callback-port="${escapeAttr(p.id)}">Edit Callback</button>` : '';
    return `<div class="port-summary"><b>${escapeHtml(p.name)}</b><span>${escapeHtml(p.dataType || 'connected topic type')}</span>${node.toolType ? '' : `<small>${escapeHtml(mode)}</small>${callbackButton}`}</div>`;
  }).join('');
}

function timerSummary(node) {
  const timers = normalizeTimers(node);
  if (!timers.length) return '<div class="hint">None</div>';
  return timers.map((timer) => `
    <div class="port-summary">
      <b>${escapeHtml(timer.name)}</b>
      <span>${Number(timer.periodSec || 1).toFixed(3)} sec</span>
      <small>${escapeHtml(timer.id)}</small>
      <button data-timer-input="${escapeAttr(timer.id)}">Edit Callback</button>
    </div>`).join('');
}

function startPan(ev) {
  workspace().setPointerCapture?.(ev.pointerId);
  document.body.classList.add('panning');
  const start = { x: ev.clientX, y: ev.clientY, vx: state.view.x, vy: state.view.y };
  const move = (e) => {
    state.view.x = start.vx + e.clientX - start.x;
    state.view.y = start.vy + e.clientY - start.y;
    applyView();
  };
  const up = () => {
    document.body.classList.remove('panning');
    workspace().releasePointerCapture?.(ev.pointerId);
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', up);
  };
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', up);
}

function bindNodeTitleEdit(el, node) {
  const label = el.querySelector('[data-node-title-name]');
  if (!label) return;
  label.ondblclick = (ev) => {
    ev.stopPropagation();
    ev.preventDefault();
    startNodeTitleEdit(el, node);
  };
}

function startNodeTitleEdit(el, node) {
  const label = el.querySelector('[data-node-title-name]');
  if (!label || el.querySelector('.node-title-input')) return;
  const input = document.createElement('input');
  input.className = 'node-title-input';
  input.value = node.name || '';
  input.setAttribute('aria-label', 'Node title');
  label.replaceWith(input);
  input.focus();
  input.select();
  input.onpointerdown = (ev) => ev.stopPropagation();
  input.onclick = (ev) => ev.stopPropagation();
  let done = false;
  const finish = (commit) => {
    if (done) return;
    done = true;
    const next = input.value.trim();
    if (commit && next && next !== node.name) {
      node.name = next;
      invalidateReady();
      renderAll();
      scheduleRun();
      return;
    }
    renderAll();
  };
  input.onkeydown = (ev) => {
    if (ev.key === 'Enter') finish(true);
    if (ev.key === 'Escape') finish(false);
  };
  input.onblur = () => finish(true);
}

function makeNodeDraggable(el, node) {
  const title = el.querySelector('.node-title');
  title.addEventListener('pointerdown', (ev) => {
    if (ev.target.closest('.node-title-input')) return;
    ev.stopPropagation();
    state.selectedNode = node.id;
    state.selectedLink = null;
    renderSelection();
    const start = { x: ev.clientX, y: ev.clientY, nx: node.x, ny: node.y };
    const move = (e) => {
      node.x = Math.round(start.nx + (e.clientX - start.x) / state.view.scale);
      node.y = Math.round(start.ny + (e.clientY - start.y) / state.view.scale);
      el.style.left = `${node.x}px`;
      el.style.top = `${node.y}px`;
      renderLinks();
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      commitHistory();
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  });
}

function makeNodeResizable(el, node) {
  el.querySelectorAll('[data-resize-corner]').forEach((handle) => {
    handle.addEventListener('pointerdown', (ev) => {
      ev.stopPropagation();
      ev.preventDefault();
      state.selectedNode = node.id;
      state.selectedLink = null;
      renderSelection();
      const corner = handle.dataset.resizeCorner || 'se';
      const start = {
        x: ev.clientX,
        y: ev.clientY,
        nx: Number(node.x || 0),
        ny: Number(node.y || 0),
        width: Math.max(nodeWidthValue(node), el.offsetWidth),
        height: Math.max(nodeHeightValue(node), el.offsetHeight),
      };
      document.body.classList.add('resizing-node');
      handle.setPointerCapture?.(ev.pointerId);
      const move = (e) => {
        const dx = (e.clientX - start.x) / state.view.scale;
        const dy = (e.clientY - start.y) / state.view.scale;
        const left = corner.includes('w');
        const top = corner.includes('n');
        const rawWidth = left ? start.width - dx : start.width + dx;
        const rawHeight = top ? start.height - dy : start.height + dy;
        const nextWidth = Math.max(DEFAULT_NODE_WIDTH, Math.round(rawWidth));
        const nextHeight = Math.max(DEFAULT_NODE_MIN_HEIGHT, Math.round(rawHeight));
        node.width = nextWidth;
        node.height = nextHeight;
        if (left) node.x = Math.round(start.nx + start.width - nextWidth);
        if (top) node.y = Math.round(start.ny + start.height - nextHeight);
        el.style.left = `${node.x}px`;
        el.style.top = `${node.y}px`;
        el.style.width = `${nextWidth}px`;
        el.style.height = `${nextHeight}px`;
        el.classList.add('resized');
        renderLinks();
      };
      const up = (e) => {
        handle.releasePointerCapture?.(e.pointerId);
        document.body.classList.remove('resizing-node');
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', up);
        commitHistory();
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
    });
  });
}

function startLinkDrag(ev, row) {
  ev.stopPropagation();
  ev.preventDefault();
  state.dragLink = {
    fromNode: row.dataset.node,
    fromPort: row.dataset.port,
    type: row.dataset.type,
    pointer: { x: ev.clientX, y: ev.clientY },
  };
  document.body.classList.add('linking');
  renderLinks();
  const move = (e) => {
    if (!state.dragLink) return;
    state.dragLink.pointer = { x: e.clientX, y: e.clientY };
    updateDropTargets();
    renderLinks();
  };
  const up = (e) => {
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', up);
    if (!state.dragLink) {
      clearLinkDrag();
      return;
    }
    const target = document.elementFromPoint(e.clientX, e.clientY)?.closest('.port.input');
    if (target) finishLinkDrag(e, target);
    clearLinkDrag();
  };
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', up);
}

function finishLinkDrag(ev, inputRow) {
  ev.stopPropagation();
  if (!state.dragLink) return;
  if (!canConnect(state.dragLink.fromNode, state.dragLink.fromPort, inputRow.dataset.node, inputRow.dataset.port)) {
    flashPort(inputRow, 'invalid');
    return;
  }
  const fixedTopic = fixedTfTopicForOutput(state.dragLink.fromNode, state.dragLink.fromPort);
  const defaultTopic = fixedTopic || sourceTopicName(state.dragLink.fromNode, state.dragLink.fromPort) || defaultLinkTopic(state.dragLink.fromNode, state.dragLink.fromPort, inputRow.dataset.node, inputRow.dataset.port);
  let topicName = defaultTopic;
  if (!fixedTopic) {
    const topic = prompt('Topic name for this output topic', defaultTopic);
    if (topic === null) return;
    topicName = normalizeTopic(topic.trim() || defaultTopic);
  }
  state.links = state.links.filter((link) => !(link.toNode === inputRow.dataset.node && link.toPort === inputRow.dataset.port));
  state.links.push({
    id: `l${Date.now()}${Math.random().toString(16).slice(2)}`,
    fromNode: state.dragLink.fromNode,
    fromPort: state.dragLink.fromPort,
    toNode: inputRow.dataset.node,
    toPort: inputRow.dataset.port,
    name: topicName,
  });
  syncSourceTopicNames(state.dragLink.fromNode, state.dragLink.fromPort, topicName);
  state.selectedNode = null;
  state.selectedLink = state.links[state.links.length - 1].id;
  clearLinkDrag();
  renderAll();
  scheduleRun();
}

function clearLinkDrag() {
  state.dragLink = null;
  document.body.classList.remove('linking');
  document.querySelectorAll('.port.drop-ok,.port.drop-bad').forEach((el) => el.classList.remove('drop-ok', 'drop-bad'));
  renderLinks();
}

function updateDropTargets() {
  if (!state.dragLink) return;
  document.querySelectorAll('.port.input').forEach((row) => {
    const ok = canConnect(state.dragLink.fromNode, state.dragLink.fromPort, row.dataset.node, row.dataset.port);
    row.classList.toggle('drop-ok', ok);
    row.classList.toggle('drop-bad', !ok);
  });
}

function renderLinks() {
  const svg = $('links');
  svg.innerHTML = '';
  state.links.forEach((link) => {
    const a = dotCenter(link.fromNode, link.fromPort, 'output');
    const b = dotCenter(link.toNode, link.toPort, 'input');
    if (a && b) {
      svg.appendChild(makePath(a, b, link));
      svg.appendChild(makeLinkLabel(a, b, link));
    }
  });
  if (state.dragLink) {
    const a = dotCenter(state.dragLink.fromNode, state.dragLink.fromPort, 'output');
    if (a) {
      const ws = workspace().getBoundingClientRect();
      const b = { x: state.dragLink.pointer.x - ws.left, y: state.dragLink.pointer.y - ws.top };
      const path = makePath(a, b, null);
      path.classList.add('draft');
      svg.appendChild(path);
    }
  }
}

function makePath(a, b, link) {
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  const dx = Math.max(80, Math.abs(b.x - a.x) * 0.5);
  path.setAttribute('d', `M ${a.x} ${a.y} C ${a.x + dx} ${a.y}, ${b.x - dx} ${b.y}, ${b.x} ${b.y}`);
  path.setAttribute('class', 'link');
  if (link) {
    path.dataset.link = link.id;
    if (state.selectedLink === link.id) path.classList.add('selected');
    path.onpointerdown = (ev) => {
      ev.stopPropagation();
      state.selectedLink = link.id;
      state.selectedNode = null;
      renderSelection();
      renderInspector();
    };
    path.ondblclick = () => deleteLink(link.id);
  }
  return path;
}

function makeLinkLabel(a, b, link) {
  const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  text.setAttribute('class', 'link-label');
  if (state.selectedLink === link.id) text.classList.add('selected');
  text.setAttribute('x', String((a.x + b.x) / 2));
  text.setAttribute('y', String((a.y + b.y) / 2 - 8));
  text.setAttribute('text-anchor', 'middle');
  text.setAttribute('dominant-baseline', 'middle');
  text.dataset.link = link.id;
  text.textContent = displayLinkName(link);
  text.onpointerdown = (ev) => {
    ev.stopPropagation();
    state.selectedLink = link.id;
    state.selectedNode = null;
    renderSelection();
    renderInspector();
  };
  text.ondblclick = (ev) => {
    ev.stopPropagation();
    editLinkName(link.id);
  };
  return text;
}

function dotCenter(nodeId, portId, kind) {
  const dot = document.querySelector(`.port.${kind}[data-node="${nodeId}"][data-port="${portId}"] .dot`);
  if (!dot) return null;
  const r = dot.getBoundingClientRect();
  const ws = workspace().getBoundingClientRect();
  return { x: r.left - ws.left + r.width / 2, y: r.top - ws.top + r.height / 2 };
}

function applyView() {
  scene().style.transform = `translate(${state.view.x}px, ${state.view.y}px) scale(${state.view.scale})`;
  $('zoom-label').textContent = `${Math.round(state.view.scale * 100)}%`;
  renderLinks();
}

function screenToWorld(clientX, clientY) {
  const r = workspace().getBoundingClientRect();
  return {
    x: (clientX - r.left - state.view.x) / state.view.scale,
    y: (clientY - r.top - state.view.y) / state.view.scale,
  };
}

function centerWorld() {
  const r = workspace().getBoundingClientRect();
  return screenToWorld(r.left + r.width * 0.48, r.top + r.height * 0.36);
}

function fitView() {
  if (!state.nodes.length) {
    state.view = { x: 0, y: 0, scale: 1 };
    applyView();
    return;
  }
  const minX = Math.min(...state.nodes.map((n) => n.x));
  const minY = Math.min(...state.nodes.map((n) => n.y));
  const maxX = Math.max(...state.nodes.map((n) => n.x + nodeWidthValue(n)));
  const maxY = Math.max(...state.nodes.map((n) => n.y + nodeHeightValue(n)));
  const r = workspace().getBoundingClientRect();
  state.view.scale = clamp(Math.min(1.1, r.width / (maxX - minX + 120), r.height / (maxY - minY + 120)), 0.35, 1.8);
  state.view.x = Math.round((r.width - (maxX + minX) * state.view.scale) / 2);
  state.view.y = Math.round((r.height - (maxY + minY) * state.view.scale) / 2);
  applyView();
}

async function runGraph() {
  if (state.runInFlight || state.runState === 'stopping') return;
  state.runInFlight = true;
  const startedAt = performance.now();
  state.lastRunAt = startedAt;
  if (!state.autoTimer) setExecutionStatus('tick', 'Running one tick');
  const payload = { ...graphRunPayload(), runHz: runLoopHz() };
  try {
    const data = await fetch('/api/run', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
    }).then((res) => res.json());
    if (state.runState === 'stopping' || state.runState === 'stopped') return;
    updateStatus(data);
    updateNodeViews(data.nodes || {});
    updateExecutionStatus(data, performance.now() - startedAt);
  } catch (err) {
    $('runtime-status').textContent = `API error: ${err.message}`;
    setExecutionStatus('error', `API error: ${err.message}`);
  } finally {
    state.runInFlight = false;
  }
}

function graphRunPayload() {
  normalizeSourceTopicNames();
  return {
    nodes: state.nodes.map(nodeForRunPayload),
    links: state.links.map(({ fromNode, fromPort, toNode, toPort, name }) => ({ fromNode, fromPort, toNode, toPort, name })),
  };
}

function nodeForRunPayload(node) {
  if (node.toolType !== 'video_file_input' || node.params?.embeddedVideo) return node;
  const params = { ...(node.params || {}) };
  delete params.dataUrl;
  if (params.serverDecode && params.videoPath) {
    delete params.frameMessage;
    params.frameSkip = videoFrameSkip(node);
    params.outputType = videoOutputType(node);
  }
  return { ...node, params };
}

async function startServerRun(durationSec = null) {
  if (state.runInFlight) return;
  if (!state.ready) {
    setExecutionStatus('error', 'Ready is required before Run');
    return;
  }
  state.runInFlight = true;
  state.tickCount = 0;
  state.lastRunAt = performance.now();
  state.graphBuffers = {};
  prepareVideoInputsForRun();
  startVideoInputs();
  startEmbeddedVideoInputs();
  const payload = { ...graphRunPayload(), runHz: runLoopHz() };
  if (durationSec !== null) payload.durationSec = durationSec;
  try {
    const data = await fetch('/api/start', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
    }).then((res) => res.json());
    if (data.error) {
      setExecutionStatus('error', data.error);
      updateRunStatus(data);
      return;
    }
    $('run-model').classList.add('active');
    setExecutionStatus('starting', durationSec === null
      ? `Starting server run at ${runLoopHz().toFixed(1)} Hz; waiting for worker startup and DDS discovery`
      : `Starting ${durationSec.toFixed(1)} second run at ${runLoopHz().toFixed(1)} Hz; waiting for worker startup and DDS discovery`);
    updateRunStatus(data);
    startRunStatusPolling();
  } catch (err) {
    setExecutionStatus('error', `Start API error: ${err.message}`);
  } finally {
    state.runInFlight = false;
  }
}

async function readyRun() {
  if (state.readyInFlight || state.autoTimer) return;
  state.readyInFlight = true;
  state.ready = false;
  state.readySignature = '';
  $('ready-model').classList.add('active');
  setExecutionStatus('preparing', 'Preparing node environments');
  try {
    const payload = { ...graphRunPayload(), runHz: runLoopHz() };
    const data = await fetch('/api/ready', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
    }).then((res) => res.json());
    updateStatus(data);
    updateNodeViews(data.nodes || {});
    if (data.ready) {
      state.ready = true;
      state.readySignature = data.signature || '';
      setExecutionStatus('idle', 'Ready complete');
    } else {
      state.ready = false;
      setExecutionStatus('error', data?.lwrclpy?.error || data?.error || 'Ready failed');
    }
  } catch (err) {
    state.ready = false;
    setExecutionStatus('error', `Ready API error: ${err.message}`);
  } finally {
    state.readyInFlight = false;
    $('ready-model').classList.toggle('active', state.ready);
  }
}

function startRunStatusPolling() {
  if (state.autoTimer) clearTimeout(state.autoTimer);
  if (state.videoTimer) clearInterval(state.videoTimer);
  state.autoTimer = setTimeout(pollRunStatus, 0);
  resumeFramePullLoops();
  if (hasBrowserVideoRuntime()) {
    state.videoTimer = setInterval(updateVideoFramePayloads, UI_DISPLAY_FRAME_MS);
  } else {
    state.videoTimer = null;
  }
}

async function pollRunStatus() {
  if (state.runStatusInFlight) return;
  state.runStatusInFlight = true;
  try {
    const data = await fetch('/api/run-status').then((res) => res.json());
    updateRunStatus(data);
  } catch (err) {
    setExecutionStatus('error', `Status API error: ${err.message}`);
  } finally {
    state.runStatusInFlight = false;
    // Keep control-plane polling lighter than frame rendering to avoid UI stutter.
    if (state.autoTimer) state.autoTimer = setTimeout(pollRunStatus, UI_STATUS_POLL_MS);
  }
}

function updateVideoPreviewFrames() {
  updateVideoInputsForRun({ markDirty: false });
  updateEmbeddedVideoInputsForRun();
}

function updateVideoFramePayloads() {
  if (!hasBrowserVideoRuntime()) return;
  updateVideoInputsForRun({ markDirty: true });
  pushRunPayloadUpdate();
}

function hasBrowserVideoRuntime() {
  const hasLegacyVideo = Object.values(state.videoInputs).some((controller) => {
    const node = nodeFor(controller.nodeId);
    return node?.toolType === 'video_file_input' && !node.params?.serverDecode;
  });
  if (hasLegacyVideo) return true;
  return state.nodes.some((node) => node.toolType === 'video_file_input' && node.params?.embeddedVideo && (node.params.baseFrameMessage || node.params.frameMessage));
}

async function pushRunPayloadUpdate() {
  if ((!state.videoPayloadDirty && !state.videoDirtyNodes.size) || state.runPayloadUpdateInFlight) return;
  const updates = Array.from(state.videoDirtyNodes)
    .map((nodeId) => nodeFor(nodeId))
    .filter((node) => node?.toolType === 'video_file_input' && (
      (node.params?.serverDecode && node.params?.videoPath) || node.params?.frameMessage
    ))
    .map((node) => ({ nodeId: node.id, params: videoRuntimeParams(node) }));
  state.videoDirtyNodes.clear();
  state.videoPayloadDirty = false;
  if (!updates.length) return;
  state.runPayloadUpdateInFlight = true;
  try {
    await fetch('/api/update-node-params', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ updates }),
    });
  } finally {
    state.runPayloadUpdateInFlight = false;
  }
}

function videoRuntimeParams(node) {
  const p = node.params || {};
  if (p.serverDecode && p.videoPath) {
    return {
      fileName: p.fileName || '',
      videoPath: p.videoPath,
      serverDecode: true,
      loop: Boolean(p.loop),
      publishHz: effectiveVideoHz(node),
      frameSkip: videoFrameSkip(node),
      outputType: videoOutputType(node),
    };
  }
  return {
    fileName: p.fileName || '',
    frameMessage: p.frameMessage,
    loop: Boolean(p.loop),
    publishHz: effectiveVideoHz(node),
    frameSkip: videoFrameSkip(node),
    outputType: videoOutputType(node),
    duration: Number(p.duration || 0),
    currentTime: Number(p.currentTime || 0),
    ended: Boolean(p.ended),
    embeddedVideo: false,
  };
}

function updateRunStatus(data) {
  if (!data || data.error) {
    setExecutionStatus('error', data?.error || 'Run status unavailable');
    return;
  }
  updateStatus(data);
  updateNodeViews(data.nodes || {});
  const run = data.run || {};
  state.tickCount = Number(run.tickCount || 0);
  if (run.error) {
    setExecutionStatus('error', `Server run error: ${run.error}`);
  } else if (run.running) {
    const waiting = run.phase === 'starting' || state.tickCount <= 0 || runHasStartingNodes(data.nodes || {});
    if (waiting) {
      setExecutionStatus('starting', `Waiting for node startup and DDS discovery; server tick ${state.tickCount} at ${Number(run.hz || runLoopHz()).toFixed(1)} Hz`);
    } else {
      setExecutionStatus('running', `Server tick ${state.tickCount} at ${Number(run.hz || runLoopHz()).toFixed(1)} Hz`);
    }
  } else if (state.autoTimer) {
    clearTimeout(state.autoTimer);
    state.autoTimer = null;
    if (state.videoTimer) {
      clearInterval(state.videoTimer);
      state.videoTimer = null;
    }
    pauseVideoInputs();
    stopAllFramePullLoops();
    $('run-model').classList.remove('active');
    setExecutionStatus('stopped', `Server run stopped after ${state.tickCount} ticks`);
  }
}

function runHasStartingNodes(nodes) {
  return Object.values(nodes || {}).some((payload) => {
    const env = String(payload?.meta?.environment || '').toLowerCase();
    const status = String(payload?.view?.status || '').toLowerCase();
    const envRunning = /\brunning\b/.test(env);
    if (isStartupStatusText(env)) return true;
    if (isStartupStatusText(status)) {
      if (envRunning && /source worker starting/.test(status)) return false;
      return true;
    }
    const view = payload?.view;
    if (view?.kind === 'image' && !(view.dataUrl || view.raw || view.frameRef) && /worker/.test(status)) return true;
    if (view?.kind === 'plot') {
      const hasSeries = Array.isArray(view.series) && view.series.length > 0;
      const hasPoints = Array.isArray(view.points) && view.points.length > 0;
      if (!hasSeries && !hasPoints && /worker/.test(status)) return true;
    }
    return false;
  });
}

function isStartupStatusText(text) {
  return /starting|dds discovery|worker startup|waiting for node startup|waiting for worker|waiting for dds/.test(text);
}

async function stopWorkers(force = false) {
  const controller = new AbortController();
  const timeoutMs = force ? 8000 : 2000;
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const endpoint = force ? '/api/force-stop' : '/api/stop';
    const data = await fetch(endpoint, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      signal: controller.signal,
      body: JSON.stringify({ force }),
    }).then((res) => res.json());
    const stoppedCount = Object.keys(data.stopped || {}).length;
    const orphanCount = Array.isArray(data.orphanProcesses?.killed) ? data.orphanProcesses.killed.length : 0;
    const count = stoppedCount + orphanCount;
    if (!force && data.pending) {
      setExecutionStatus('stopping', 'Stop is taking too long, escalating to force stop');
      return await stopWorkers(true);
    }
    setExecutionStatus('stopped', `${force ? 'Force stopped' : 'Stopped'} ${count} worker process${count === 1 ? '' : 'es'}`);
    return data;
  } catch (err) {
    if (err?.name === 'AbortError' && !force) {
      setExecutionStatus('stopping', 'Stop timed out, escalating to force stop');
      return await stopWorkers(true);
    }
    setExecutionStatus('error', `Stop API error: ${err.message}`);
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function updateStatus(data) {
  const runtime = data.lwrclpy || {};
  const setup = data.setup?.complete === false ? ` / setup blocked${runtime.error ? ': ' + runtime.error : ''}` : '';
  const text = runtimeStatusText(runtime) + setup;
  $('runtime-status').textContent = text;
  $('runtime-detail').textContent = text;
  $('node-count').textContent = `${state.nodes.length} nodes / ${state.links.length} links`;
}

function runtimeStatusText(runtime) {
  const version = runtime?.version ? ` ${runtime.version}` : '';
  if (runtime?.available) return `lwrclpy available${version}`;
  return `lwrclpy unavailable${runtime?.error ? ': ' + runtime.error : ''}`;
}

async function refreshRuntimeHealth() {
  try {
    const data = await fetch('/api/health').then((res) => res.json());
    const runtime = data.lwrclpy || {};
    const text = runtimeStatusText(runtime);
    $('runtime-status').textContent = text;
    $('runtime-detail').textContent = text;
  } catch (err) {
    $('runtime-status').textContent = `API error: ${err.message}`;
    $('runtime-detail').textContent = `API error: ${err.message}`;
  }
}

function updateExecutionStatus(data, elapsedMs) {
  state.tickCount += 1;
  const nodeErrors = Object.values(data.nodes || {}).flatMap((node) => {
    const logs = node?.meta?.logs || [];
    return logs.filter((line) => /error|failed/i.test(String(line)));
  });
  if (data.setup?.complete === false) {
    setExecutionStatus('error', `Setup blocked after ${elapsedMs.toFixed(0)} ms`);
    return;
  }
  if (nodeErrors.length) {
    setExecutionStatus('error', `Node error after ${elapsedMs.toFixed(0)} ms: ${nodeErrors[0]}`);
    return;
  }
  const label = state.autoTimer ? 'running' : state.runState === 'stopped' ? 'stopped' : 'tick';
  setExecutionStatus(label, `Tick ${state.tickCount} completed in ${elapsedMs.toFixed(0)} ms`);
}

function setExecutionStatus(kind, detail) {
  state.runState = kind;
  const label = $('execution-state');
  const tick = $('tick-status');
  if (!label || !tick) return;
  label.className = `status-pill ${kind}`;
  label.textContent = {
    idle: 'Idle',
    tick: 'Tick',
    preparing: 'Preparing',
    starting: 'Starting',
    running: 'Running',
    stopping: 'Stopping',
    stopped: 'Stopped',
    error: 'Error',
  }[kind] || kind;
  tick.textContent = detail;
}

function updateNodeViews(nodes) {
  const changedNodeIds = new Set();
  Object.entries(nodes).forEach(([nodeId, payload]) => {
    if (payload?.view) {
      const newView = normalizedNodeView(nodeId, payload.view);
      const existing = state.nodeViews[nodeId];
      const existingSignature = nodeViewSignature(existing);
      // Don't replace a valid image view with an empty one (avoids flicker when frames are sparse)
      const existingHasImage = existing?.kind === 'image' && (existing?.dataUrl || existing?.raw?.data || existing?.frameRef);
      const newHasImage = newView?.kind === 'image' && (newView?.dataUrl || newView?.raw?.data || newView?.frameRef);
      if (existingHasImage && newView?.kind === 'image' && !newHasImage) {
        const nextView = { ...existing, status: newView.status || existing.status };
        if (existingSignature !== nodeViewSignature(nextView)) changedNodeIds.add(nodeId);
        state.nodeViews[nodeId] = nextView;
      } else {
        if (newView?.kind === 'empty') delete state.graphBuffers[nodeId];
        if (newView?.kind === 'plot') mergeGraphView(nodeId, newView);
        if (existingSignature !== nodeViewSignature(newView)) changedNodeIds.add(nodeId);
        state.nodeViews[nodeId] = newView;
      }
    }
  });
  if (!changedNodeIds.size) return;
  document.querySelectorAll('[data-node-view]').forEach((el) => {
    if (!changedNodeIds.has(el.dataset.nodeView)) return;
    patchNodeViewEl(el, state.nodeViews[el.dataset.nodeView]);
  });
}

function mergeGraphView(nodeId, view) {
  const points = Array.isArray(view.points) ? view.points : [];
  const resetKey = String(view.resetKey || '');
  const node = nodeFor(nodeId);
  const limit = Math.max(8, Math.min(Number(node?.params?.sampleLimit || view.sampleLimit || 600), 100000));
  let buffer = state.graphBuffers[nodeId];
  if (!buffer || buffer.resetKey !== resetKey) {
    buffer = { resetKey, lastSeq: 0, points: [] };
    state.graphBuffers[nodeId] = buffer;
  }
  for (const point of points) {
    const seq = Number(point?.seq || 0);
    if (seq && seq <= buffer.lastSeq) continue;
    const y = Number(point?.y);
    if (!Number.isFinite(y)) continue;
    const t = Number.isFinite(Number(point?.t)) ? Number(point.t) : performance.now() / 1000;
    buffer.points.push({ t, y, seq: seq || buffer.lastSeq + 1 });
    if (seq) buffer.lastSeq = seq;
    else buffer.lastSeq += 1;
  }
  if (buffer.points.length > limit) buffer.points.splice(0, buffer.points.length - limit);
  view.series = buffer.points.slice();
  view.clientReceivedAt = performance.now() / 1000;
}

function nodeViewSignature(view) {
  if (!view) return '';
  if (view.kind === 'image') {
    if (view.frameRef) {
      return `image:frame:${view.frameRef.nodeId}:${view.frameRef.seq || 0}:${view.frameRef.width || 0}:${view.frameRef.height || 0}:${view.frameRef.sourceWidth || 0}:${view.frameRef.sourceHeight || 0}:${view.frameRef.encoding || ''}:${view.status || ''}`;
    }
    if (view.raw) return `image:raw:${view.raw.width}:${view.raw.height}:${view.raw.encoding}:${String(view.raw.data || '').length}:${view.status || ''}`;
    if (view.dataUrl) return `image:data:${view.dataUrl.length}:${view.status || ''}`;
    return `image:empty:${view.status || ''}`;
  }
  if (view.kind === 'plot') {
    const series = Array.isArray(view.series) ? view.series : [];
    const last = series.length ? series[series.length - 1] : null;
    return `plot:${series.length}:${last?.t ?? ''}:${last?.y ?? ''}:${view.status || ''}`;
  }
  return JSON.stringify(view);
}

// Draw a dataUrl onto a canvas element without clearing during decode (no blank-frame flicker).
function drawToCanvas(canvas, dataUrl) {
  if (!dataUrl || canvas.dataset.pendingSrc === dataUrl) return;
  canvas.dataset.pendingSrc = dataUrl;
  const img = new Image();
  img.onload = () => {
    if (canvas.width !== img.naturalWidth || canvas.height !== img.naturalHeight) {
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
    }
    canvas.getContext('2d').drawImage(img, 0, 0);
  };
  img.src = dataUrl;
}

function drawRawImageToCanvas(canvas, raw) {
  if (!raw?.data || !raw.width || !raw.height) return;
  const signature = `${raw.width}x${raw.height}:${raw.encoding}:${raw.data.length}:${raw.data.slice(-24)}`;
  if (canvas.dataset.rawSignature === signature) return;
  const width = Math.max(1, Number(raw.width));
  const height = Math.max(1, Number(raw.height));
  const bytes = base64ToBytes(raw.data);
  const rgba = new Uint8ClampedArray(width * height * 4);
  const encoding = String(raw.encoding || 'rgb8').toLowerCase();
  const pixelCount = width * height;
  if (encoding === 'mono8' || encoding === '8uc1') {
    for (let i = 0; i < pixelCount; i += 1) {
      const v = bytes[i] || 0;
      const dst = i * 4;
      rgba[dst] = v;
      rgba[dst + 1] = v;
      rgba[dst + 2] = v;
      rgba[dst + 3] = 255;
    }
  } else {
    const bgr = encoding === 'bgr8';
    for (let i = 0; i < pixelCount; i += 1) {
      const src = i * 3;
      const dst = i * 4;
      rgba[dst] = bytes[src + (bgr ? 2 : 0)] || 0;
      rgba[dst + 1] = bytes[src + 1] || 0;
      rgba[dst + 2] = bytes[src + (bgr ? 0 : 2)] || 0;
      rgba[dst + 3] = 255;
    }
  }
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  canvas.getContext('2d').putImageData(new ImageData(rgba, width, height), 0, 0);
  canvas.dataset.rawSignature = signature;
}

const frameFetchControllers = new WeakMap();

function stopFramePullLoop(canvas) {
  if (!canvas) return;
  canvas._framePullActive = false;
  canvas._frameStreamActive = false;
  if (canvas._framePullTimer) {
    clearTimeout(canvas._framePullTimer);
    canvas._framePullTimer = null;
  }
  if (canvas._frameStreamController) {
    canvas._frameStreamController.abort();
    canvas._frameStreamController = null;
  }
  cancelCanvasFrameLoad(canvas);
}

function stopAllFramePullLoops() {
  document.querySelectorAll('canvas.image-canvas').forEach((canvas) => stopFramePullLoop(canvas));
}

function resumeFramePullLoops() {
  document.querySelectorAll('[data-node-view]').forEach((el) => {
    const view = state.nodeViews[el.dataset.nodeView];
    if (view?.kind !== 'image' || !view.frameRef) return;
    const canvas = el.querySelector('canvas.image-canvas');
    if (canvas) scheduleFrameRefDraw(canvas, view.frameRef);
  });
}

function cancelCanvasFrameLoad(canvas) {
  const controller = frameFetchControllers.get(canvas);
  if (controller) controller.abort();
  if (typeof canvas?._frameImageResolve === 'function') {
    const resolve = canvas._frameImageResolve;
    canvas._frameImageResolve = null;
    resolve();
  }
  if (canvas?._frameImage) {
    canvas._frameImage.onload = null;
    canvas._frameImage.onerror = null;
    canvas._frameImage.removeAttribute('src');
  }
}

function scheduleFrameRefDraw(canvas, frameRef) {
  if (!canvas || !frameRef) return;
  if (frameRef.stream) {
    scheduleFrameStreamDraw(canvas, frameRef);
    return;
  }
  if (canvas._frameStreamActive) {
    stopFramePullLoop(canvas);
  }
  canvas._framePullRef = frameRef;
  canvas.dataset.desiredFrame = `${frameRef.nodeId}:${frameRef.seq || 0}`;
  if (canvas._framePullActive) return;
  canvas._framePullActive = true;
  pullLatestFrameToCanvas(canvas);
}

function drawRawBytesToCanvas(canvas, bytes, width, height, encoding, signature) {
  if (!bytes?.length || !width || !height) return;
  const required = width * height * 4;
  if (!canvas._rgbaBuffer || canvas._rgbaBuffer.length !== required) {
    canvas._rgbaBuffer = new Uint8ClampedArray(required);
    canvas._imageData = new ImageData(canvas._rgbaBuffer, width, height);
  }
  const rgba = canvas._rgbaBuffer;
  const pixelCount = width * height;
  const normalized = String(encoding || 'rgb8').toLowerCase();
  if (normalized === 'mono8' || normalized === '8uc1') {
    for (let i = 0; i < pixelCount; i += 1) {
      const v = bytes[i] || 0;
      const dst = i * 4;
      rgba[dst] = v;
      rgba[dst + 1] = v;
      rgba[dst + 2] = v;
      rgba[dst + 3] = 255;
    }
  } else {
    const bgr = normalized === 'bgr8';
    for (let i = 0; i < pixelCount; i += 1) {
      const src = i * 3;
      const dst = i * 4;
      rgba[dst] = bytes[src + (bgr ? 2 : 0)] || 0;
      rgba[dst + 1] = bytes[src + 1] || 0;
      rgba[dst + 2] = bytes[src + (bgr ? 0 : 2)] || 0;
      rgba[dst + 3] = 255;
    }
  }
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  canvas.getContext('2d').putImageData(canvas._imageData, 0, 0);
  canvas.dataset.rawSignature = signature;
}

function streamEncodingName(code) {
  if (code === 2) return 'bgr8';
  if (code === 3) return 'mono8';
  if (code === 10) return 'jpeg';
  if (code === 11) return 'bmp';
  if (code === 12) return 'png';
  return 'rgb8';
}

function appendBytes(a, b) {
  if (!a?.length) return b;
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

function scheduleFrameStreamDraw(canvas, frameRef) {
  if (!canvas || !frameRef?.nodeId) return;
  canvas._framePullRef = frameRef;
  if (canvas._frameStreamActive && canvas._frameStreamNodeId === frameRef.nodeId) return;
  stopFramePullLoop(canvas);
  canvas._frameStreamActive = true;
  canvas._frameStreamNodeId = frameRef.nodeId;
  const controller = new AbortController();
  canvas._frameStreamController = controller;
  pullFrameStreamToCanvas(canvas, frameRef.nodeId, controller).catch((err) => {
    if (err?.name !== 'AbortError') console.warn('Frame stream failed', err);
  });
}

async function pullFrameStreamToCanvas(canvas, nodeId, controller) {
  const response = await fetch(`/api/node-frame-stream?nodeId=${encodeURIComponent(nodeId)}`, {
    signal: controller.signal,
    cache: 'no-store',
  });
  if (!response.ok || !response.body) return;
  const reader = response.body.getReader();
  let buffer = new Uint8Array(0);
  while (canvas._frameStreamActive && canvas.isConnected) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer = appendBytes(buffer, value);
    while (buffer.length >= FRAME_STREAM_HEADER_BYTES) {
      const view = new DataView(buffer.buffer, buffer.byteOffset, buffer.byteLength);
      const magic = String.fromCharCode(buffer[0], buffer[1], buffer[2], buffer[3]);
      if (magic !== 'IPFS') {
        buffer = new Uint8Array(0);
        break;
      }
      const seq = Number(view.getBigUint64(4, true));
      const width = view.getUint32(12, true);
      const height = view.getUint32(16, true);
      const encodingCode = view.getUint32(20, true);
      const dataLength = view.getUint32(24, true);
      if (buffer.length < FRAME_STREAM_HEADER_BYTES + dataLength) break;
      const payload = buffer.slice(FRAME_STREAM_HEADER_BYTES, FRAME_STREAM_HEADER_BYTES + dataLength);
      buffer = buffer.slice(FRAME_STREAM_HEADER_BYTES + dataLength);
      const encoding = streamEncodingName(encodingCode);
      const signature = frameSignature(nodeId, seq);
      if (['jpeg', 'bmp', 'png', 'webp'].includes(encoding)) {
        const bitmap = await blobToBitmapLike(new Blob([payload], { type: encoding === 'jpeg' ? 'image/jpeg' : `image/${encoding}` }));
        drawBitmapLike(canvas, bitmap);
        canvas.dataset.rawSignature = signature;
      } else {
        drawRawBytesToCanvas(canvas, payload, width, height, encoding, signature);
      }
    }
  }
}

async function pullLatestFrameToCanvas(canvas) {
  if (!canvas || !canvas._framePullActive) return;
  if (!canvas.isConnected) {
    stopFramePullLoop(canvas);
    return;
  }
  const frameRef = canvas._framePullRef;
  if (!frameRef?.nodeId) {
    stopFramePullLoop(canvas);
    return;
  }
  const startedAt = performance.now();
  if (!canvas._frameDrawInProgress) {
    canvas._frameDrawInProgress = true;
    canvas._frameDrawStartedAt = startedAt;
    try {
      await drawFrameRefToCanvas(canvas, frameRef);
    } finally {
      canvas._frameDrawInProgress = false;
      canvas._frameDrawStartedAt = 0;
    }
  } else if (performance.now() - Number(canvas._frameDrawStartedAt || 0) > FRAME_FETCH_TIMEOUT_MS) {
    cancelCanvasFrameLoad(canvas);
    canvas._frameDrawInProgress = false;
    canvas._frameDrawStartedAt = 0;
  }
  if (!canvas._framePullActive || !canvas.isConnected) return;
  const elapsed = performance.now() - startedAt;
  canvas._framePullTimer = setTimeout(() => pullLatestFrameToCanvas(canvas), Math.max(0, UI_DISPLAY_FRAME_MS - elapsed));
}

function drawBitmapLike(canvas, bitmap) {
  const width = bitmap.naturalWidth || bitmap.width;
  const height = bitmap.naturalHeight || bitmap.height;
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  canvas.getContext('2d').drawImage(bitmap, 0, 0);
  if (typeof bitmap.close === 'function') bitmap.close();
}

function frameSignature(nodeId, seq) {
  return `${nodeId}:${seq}`;
}

function signatureSeq(signature) {
  const value = Number(String(signature || '').split(':').pop());
  return Number.isFinite(value) ? value : 0;
}

function shouldDrawResponseFrame(canvas, nodeId, responseSeq) {
  const desired = canvas.dataset.desiredFrame || '';
  const desiredSeq = signatureSeq(desired);
  return desired.startsWith(`${nodeId}:`) && responseSeq >= desiredSeq;
}

async function blobToBitmapLike(blob) {
  if (typeof createImageBitmap === 'function') {
    let timeout = null;
    try {
      const timeoutPromise = new Promise((_, reject) => {
        timeout = setTimeout(() => reject(new Error('Frame image decode timed out')), FRAME_DECODE_TIMEOUT_MS);
      });
      return await Promise.race([createImageBitmap(blob), timeoutPromise]);
    } catch (err) {
      if (!/timed out/.test(String(err?.message || ''))) {
        console.warn('createImageBitmap failed, falling back to Image decode', err);
      }
    } finally {
      if (timeout) clearTimeout(timeout);
    }
  }
  const url = URL.createObjectURL(blob);
  const img = new Image();
  let timeout = null;
  try {
    await new Promise((resolve, reject) => {
      img.onload = resolve;
      img.onerror = () => reject(new Error('Frame image decode failed'));
      timeout = setTimeout(() => {
        img.onload = null;
        img.onerror = null;
        img.removeAttribute('src');
        reject(new Error('Frame image decode timed out'));
      }, FRAME_DECODE_TIMEOUT_MS);
      img.src = url;
    });
    return img;
  } finally {
    if (timeout) clearTimeout(timeout);
    URL.revokeObjectURL(url);
  }
}

async function drawEncodedFrameImage(canvas, frameRef, signature, controller) {
  const response = await fetch(`/api/node-frame?nodeId=${encodeURIComponent(frameRef.nodeId)}&seq=${encodeURIComponent(frameRef.seq)}`, {
    signal: controller.signal,
    cache: 'no-store',
  });
  if (response.status === 204 || !response.ok) return;
  const responseSeq = Number(response.headers.get('x-frame-seq') || frameRef.seq);
  const drawnSeq = Number.isFinite(responseSeq) && responseSeq > 0 ? responseSeq : Number(frameRef.seq);
  if (!shouldDrawResponseFrame(canvas, frameRef.nodeId, drawnSeq)) return;
  const drawnSignature = frameSignature(frameRef.nodeId, drawnSeq);
  if (canvas.dataset.rawSignature === drawnSignature) return;
  const blob = await response.blob();
  if (controller.signal.aborted || !shouldDrawResponseFrame(canvas, frameRef.nodeId, drawnSeq)) return;
  const bitmap = await blobToBitmapLike(blob);
  if (controller.signal.aborted || !shouldDrawResponseFrame(canvas, frameRef.nodeId, drawnSeq)) {
    if (typeof bitmap.close === 'function') bitmap.close();
    return;
  }
  drawBitmapLike(canvas, bitmap);
  canvas.dataset.rawSignature = drawnSignature;
}

async function drawFrameRefToCanvas(canvas, frameRef) {
  if (!frameRef?.nodeId || !frameRef.seq) return;
  const signature = `${frameRef.nodeId}:${frameRef.seq}`;
  canvas.dataset.desiredFrame = signature;
  if (canvas.dataset.rawSignature === signature) return;
  if (canvas.dataset.pendingFrame === signature) return;
  cancelCanvasFrameLoad(canvas);
  const controller = new AbortController();
  frameFetchControllers.set(canvas, controller);
  const timeout = setTimeout(() => controller.abort(), FRAME_FETCH_TIMEOUT_MS);
  if (['jpeg', 'jpg', 'bmp', 'png', 'webp'].includes(String(frameRef.encoding || '').toLowerCase())) {
    canvas.dataset.pendingFrame = signature;
    try {
      await drawEncodedFrameImage(canvas, frameRef, signature, controller);
    } catch (err) {
      if (err?.name !== 'AbortError') console.warn('Frame draw failed', err);
    } finally {
      clearTimeout(timeout);
      if (canvas.dataset.pendingFrame === signature) delete canvas.dataset.pendingFrame;
      if (frameFetchControllers.get(canvas) === controller) frameFetchControllers.delete(canvas);
    }
    return;
  }
  canvas.dataset.pendingFrame = signature;
  try {
    const response = await fetch(`/api/node-frame?nodeId=${encodeURIComponent(frameRef.nodeId)}&seq=${encodeURIComponent(frameRef.seq)}`, {
      signal: controller.signal,
      cache: 'no-store',
    });
    if (response.status === 204) return;
    if (!response.ok) return;
    const responseSeq = Number(response.headers.get('x-frame-seq') || frameRef.seq);
    const drawnSeq = Number.isFinite(responseSeq) && responseSeq > 0 ? responseSeq : Number(frameRef.seq);
    const drawnSignature = frameSignature(frameRef.nodeId, drawnSeq);
    if (!shouldDrawResponseFrame(canvas, frameRef.nodeId, drawnSeq)) return;
    if (canvas.dataset.rawSignature === drawnSignature) return;
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (!shouldDrawResponseFrame(canvas, frameRef.nodeId, drawnSeq)) return;
    const width = Math.max(1, Number(response.headers.get('x-frame-width') || frameRef.width));
    const height = Math.max(1, Number(response.headers.get('x-frame-height') || frameRef.height));
    const encoding = String(response.headers.get('x-frame-encoding') || frameRef.encoding || 'rgb8').toLowerCase();
    if (!width || !height) return;
    const required = width * height * 4;
    if (!canvas._rgbaBuffer || canvas._rgbaBuffer.length !== required) {
      canvas._rgbaBuffer = new Uint8ClampedArray(required);
      canvas._imageData = new ImageData(canvas._rgbaBuffer, width, height);
    }
    const rgba = canvas._rgbaBuffer;
    const pixelCount = width * height;
    if (encoding === 'mono8' || encoding === '8uc1') {
      for (let i = 0; i < pixelCount; i += 1) {
        const v = bytes[i] || 0;
        const dst = i * 4;
        rgba[dst] = v;
        rgba[dst + 1] = v;
        rgba[dst + 2] = v;
        rgba[dst + 3] = 255;
      }
    } else {
      const bgr = encoding === 'bgr8';
      for (let i = 0; i < pixelCount; i += 1) {
        const src = i * 3;
        const dst = i * 4;
        rgba[dst] = bytes[src + (bgr ? 2 : 0)] || 0;
        rgba[dst + 1] = bytes[src + 1] || 0;
        rgba[dst + 2] = bytes[src + (bgr ? 0 : 2)] || 0;
        rgba[dst + 3] = 255;
      }
    }
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    canvas.getContext('2d').putImageData(canvas._imageData, 0, 0);
    canvas.dataset.rawSignature = drawnSignature;
  } catch (err) {
    if (err?.name !== 'AbortError') console.warn('Frame draw failed', err);
  } finally {
    clearTimeout(timeout);
    if (canvas.dataset.pendingFrame === signature) delete canvas.dataset.pendingFrame;
    if (frameFetchControllers.get(canvas) === controller) frameFetchControllers.delete(canvas);
  }
}

// Update a node-view element in-place using canvas to avoid blank-frame flicker.
function patchNodeViewEl(el, view) {
  if (view?.kind === 'plot') {
    patchPlotViewEl(el, view);
    return;
  }
  if (view?.kind === 'tf3d') {
    patchTf3dViewEl(el, view);
    return;
  }
  stopPlotAnimation(el);
  if (view?.kind === 'image' && (view.dataUrl || view.raw || view.frameRef)) {
    const existingFig = el.querySelector('figure.image-view');
    if (existingFig) {
      const canvas = existingFig.querySelector('canvas.image-canvas');
      const cap = existingFig.querySelector('figcaption');
      if (canvas && cap) {
        const newCap = view.status || '';
        if (cap.textContent !== newCap) cap.textContent = newCap;
        if (view.frameRef) scheduleFrameRefDraw(canvas, view.frameRef);
        else if (view.raw) {
          stopFramePullLoop(canvas);
          drawRawImageToCanvas(canvas, view.raw);
        } else {
          stopFramePullLoop(canvas);
          drawToCanvas(canvas, view.dataUrl);
        }
        return;
      }
    }
  }
  const newHtml = renderViewContent(view);
  if (el.innerHTML !== newHtml) {
    el.innerHTML = newHtml;
    if (view?.kind === 'image' && (view.dataUrl || view.raw || view.frameRef)) {
      const canvas = el.querySelector('canvas.image-canvas');
      if (canvas && view.frameRef) scheduleFrameRefDraw(canvas, view.frameRef);
      else if (canvas && view.raw) {
        stopFramePullLoop(canvas);
        drawRawImageToCanvas(canvas, view.raw);
      } else if (canvas) {
        stopFramePullLoop(canvas);
        drawToCanvas(canvas, view.dataUrl);
      }
    }
  }
  bindInteractiveTextView(el);
}

function bindInteractiveTextView(el) {
  const host = el.querySelector('[data-interactive-text-view]');
  if (!host || host.dataset.bound === '1') return;
  host.dataset.bound = '1';
  host.addEventListener('pointerdown', (ev) => ev.stopPropagation());
  host.addEventListener('click', (ev) => ev.stopPropagation());
  host.addEventListener('wheel', (ev) => ev.stopPropagation(), { passive: true });
  const form = host.querySelector('[data-text-input-form]');
  const textarea = host.querySelector('[data-text-input-draft]');
  const nodeId = el.dataset.nodeView;
  if (textarea) {
    textarea.addEventListener('input', () => updateInteractiveTextDraft(nodeId, textarea.value));
    textarea.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) {
        ev.preventDefault();
        form?.requestSubmit();
      }
    });
  }
  const prev = host.querySelector('[data-prompt-history-prev]');
  if (prev) prev.onclick = () => stepInteractiveTextHistory(nodeId, -1);
  const next = host.querySelector('[data-prompt-history-next]');
  if (next) next.onclick = () => stepInteractiveTextHistory(nodeId, 1);
  const clear = host.querySelector('[data-prompt-history-clear]');
  if (clear) clear.onclick = () => clearInteractiveTextHistory(nodeId);
  if (form) {
    form.addEventListener('submit', (ev) => {
      ev.preventDefault();
      sendInteractiveTextMessage(nodeId);
    });
  }
}

function updateInteractiveTextDraft(nodeId, value) {
  const node = nodeFor(nodeId);
  if (!node || node.toolType !== 'interactive_text_input') return;
  node.params = { ...(node.params || {}), draft: String(value || '') };
  const existing = state.nodeViews[node.id];
  state.nodeViews[node.id] = {
    kind: 'text_input',
    ...(existing?.kind === 'text_input' ? existing : {}),
    draft: String(value || ''),
    messages: Array.isArray(node.params.messages) ? node.params.messages : [],
    promptHistory: Array.isArray(node.params.promptHistory) ? node.params.promptHistory : [],
  };
}

function setInteractiveTextDraft(node, value, historyCursor = node.params?.historyCursor ?? -1) {
  node.params = { ...(node.params || {}), draft: String(value || ''), historyCursor };
  const existing = state.nodeViews[node.id];
  state.nodeViews[node.id] = {
    kind: 'text_input',
    ...(existing?.kind === 'text_input' ? existing : {}),
    draft: String(value || ''),
    messages: Array.isArray(node.params.messages) ? node.params.messages : [],
    promptHistory: Array.isArray(node.params.promptHistory) ? node.params.promptHistory : [],
  };
  const viewEl = [...document.querySelectorAll('[data-node-view]')].find((item) => item.dataset.nodeView === node.id);
  const textarea = viewEl?.querySelector('[data-text-input-draft]');
  if (textarea) textarea.value = String(value || '');
}

function stepInteractiveTextHistory(nodeId, direction) {
  const node = nodeFor(nodeId);
  if (!node || node.toolType !== 'interactive_text_input') return;
  const history = Array.isArray(node.params?.promptHistory) ? node.params.promptHistory : [];
  if (!history.length) return;
  const current = Number.isFinite(Number(node.params.historyCursor)) && Number(node.params.historyCursor) >= 0
    ? Number(node.params.historyCursor)
    : history.length;
  const next = Math.max(0, Math.min(history.length - 1, current + direction));
  setInteractiveTextDraft(node, history[next], next);
}

function clearInteractiveTextHistory(nodeId) {
  const node = nodeFor(nodeId);
  if (!node || node.toolType !== 'interactive_text_input') return;
  node.params = { ...(node.params || {}), promptHistory: [], historyCursor: -1 };
  const existing = state.nodeViews[node.id];
  state.nodeViews[node.id] = {
    kind: 'text_input',
    ...(existing?.kind === 'text_input' ? existing : {}),
    promptHistory: [],
    status: 'History cleared',
  };
  const viewEl = [...document.querySelectorAll('[data-node-view]')].find((item) => item.dataset.nodeView === node.id);
  if (viewEl) patchNodeViewEl(viewEl, state.nodeViews[node.id]);
}

async function sendInteractiveTextMessage(nodeId) {
  const node = nodeFor(nodeId);
  if (!node || node.toolType !== 'interactive_text_input') return;
  const params = node.params || {};
  const text = String(params.draft || '').trim();
  if (!text) return;
  const nextSeq = Math.max(1, Math.floor(Number(params.nextSeq || 1)));
  const maxMessages = Math.max(1, Math.min(1000, Math.floor(Number(params.maxMessages || 100))));
  const messages = Array.isArray(params.messages) ? params.messages.slice() : [];
  const promptHistory = Array.isArray(params.promptHistory) ? params.promptHistory.slice() : [];
  if (!promptHistory.length || promptHistory[promptHistory.length - 1] !== text) promptHistory.push(text);
  messages.push({ seq: nextSeq, role: 'user', text, t: Date.now() / 1000 });
  const nextMessages = messages.slice(-maxMessages);
  const nextHistory = promptHistory.slice(-maxMessages);
  node.params = {
    ...params,
    draft: '',
    messages: nextMessages,
    promptHistory: nextHistory,
    historyCursor: -1,
    nextSeq: nextSeq + 1,
    maxMessages,
  };
  state.nodeViews[node.id] = {
    kind: 'text_input',
    draft: '',
    messages: nextMessages,
    promptHistory: nextHistory,
    status: state.autoTimer ? 'Queued' : 'Ready to send',
  };
  const viewEl = [...document.querySelectorAll('[data-node-view]')].find((item) => item.dataset.nodeView === node.id);
  if (viewEl) {
    const textarea = viewEl.querySelector('[data-text-input-draft]');
    if (textarea) textarea.value = '';
    patchNodeViewEl(viewEl, state.nodeViews[node.id]);
  }
  if (state.autoTimer || state.ready) {
    try {
      await fetch('/api/update-node-params', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ updates: [{ nodeId: node.id, params: node.params }] }),
      });
    } catch (err) {
      setExecutionStatus('error', `Text send failed: ${err.message}`);
    }
  }
}

function stopPlotAnimation(el) {
  if (!el) return;
  if (el._plotAnimationFrame) {
    cancelAnimationFrame(el._plotAnimationFrame);
    el._plotAnimationFrame = null;
  }
  if (el._plotRenderTimer) {
    clearTimeout(el._plotRenderTimer);
    el._plotRenderTimer = null;
  }
}

function schedulePlotAnimation(el, view) {
  if (!el || view?.running === false || !Array.isArray(view?.series) || view.series.length < 2) return;
  if (el._plotAnimationFrame) return;
  el._plotAnimationFrame = requestAnimationFrame(() => {
    el._plotAnimationFrame = null;
    const nodeId = el.dataset.nodeView;
    const nextView = state.nodeViews[nodeId];
    if (!document.body.contains(el) || nextView?.kind !== 'plot' || nextView.running === false) return;
    patchPlotViewEl(el, nextView);
  });
}

function patchPlotViewEl(el, view) {
  const now = performance.now();
  const minIntervalMs = UI_DISPLAY_FRAME_MS;
  const last = Number(el.dataset.plotRenderedAt || 0);
  el._pendingPlotView = view;
  if (now - last < minIntervalMs) {
    if (!el._plotRenderTimer) {
      el._plotRenderTimer = setTimeout(() => {
        el._plotRenderTimer = null;
        const pending = el._pendingPlotView;
        if (pending) patchPlotViewEl(el, pending);
      }, Math.max(1, minIntervalMs - (now - last)));
    }
    return;
  }
  const nextView = el._pendingPlotView || view;
  el._pendingPlotView = null;
  el.dataset.plotRenderedAt = String(now);
  const model = plotRenderModel(nextView.series || [], nextView.status || '', nextView);
  let container = el.querySelector('.plot-view');
  if (!container) {
    el.innerHTML = '<div class="plot-view"><svg viewBox="0 0 280 120"><polyline></polyline></svg><span></span></div>';
    container = el.querySelector('.plot-view');
  }
  const svg = container.querySelector('svg');
  const polyline = container.querySelector('polyline');
  const caption = container.querySelector('span');
  if (svg && svg.getAttribute('viewBox') !== '0 0 280 120') svg.setAttribute('viewBox', '0 0 280 120');
  if (polyline && polyline.getAttribute('points') !== model.points) polyline.setAttribute('points', model.points);
  if (caption && caption.textContent !== model.caption) caption.textContent = model.caption;
  schedulePlotAnimation(el, nextView);
}

function normalizedNodeView(nodeId, view) {
  const node = nodeFor(nodeId);
  const controller = state.videoInputs[nodeId];
  if (node?.toolType === 'video_file_input' && controller) {
    // Video files live in the browser. Keep the local canvas frame authoritative;
    // server-side status may contain a stale/raw frame and must not overwrite it.
    const localView = state.nodeViews[nodeId];
    if (localView?.dataUrl || localView?.raw) return { ...localView, status: videoStatus(controller) };
    return { kind: 'image', dataUrl: view?.dataUrl || '', status: videoStatus(controller) };
  }
  if (node?.toolType === 'interactive_text_input' && view?.kind === 'text_input') {
    return {
      ...view,
      draft: String(node.params?.draft || ''),
      messages: Array.isArray(node.params?.messages) ? node.params.messages : [],
      promptHistory: Array.isArray(node.params?.promptHistory) ? node.params.promptHistory : [],
    };
  }
  if ((node?.toolType === 'tf_viewer' || node?.toolType === '3d_viewer') && view?.kind === 'tf3d') return withTfViewerParams(nodeId, view);
  return view;
}

function withTfViewerParams(nodeId, view) {
  const node = nodeFor(nodeId);
  if (!node || view?.kind !== 'tf3d') return view;
  return {
    ...view,
    ...tfViewerDefaults(node.params || {}),
  };
}

function renderViewContent(view) {
  if (!view) return '<div class="view-empty">No data</div>';
  if (view.kind === 'image' && (view.dataUrl || view.raw || view.frameRef)) {
    return `<figure class="image-view"><canvas class="image-canvas"></canvas><figcaption>${escapeHtml(view.status || '')}</figcaption></figure>`;
  }
  if (view.kind === 'string') {
    const text = view.text || '';
    const status = view.status || (text ? `${text.length} chars` : 'No text');
    return `<div class="string-view"><pre>${escapeHtml(text)}</pre><span>${escapeHtml(status)}</span></div>`;
  }
  if (view.kind === 'text_input') {
    return renderTextInputView(view);
  }
  if (view.kind === 'chat') {
    return renderChatView(view);
  }
  if (view.kind === 'plot') {
    return renderPlot(view.series || [], view.status || '', view);
  }
  if (view.kind === 'tf3d') {
    return renderTf3d(view);
  }
  return `<div class="view-empty">${escapeHtml(view.status || 'No data')}</div>`;
}

function renderTextInputView(view) {
  const history = Array.isArray(view.promptHistory) ? view.promptHistory : [];
  return `<div class="interactive-text-view" data-interactive-text-view>
    <form data-text-input-form>
      <input data-text-input-draft type="text" value="${escapeAttr(view.draft || '')}" placeholder="Message">
      <div class="prompt-controls">
        <button type="button" data-prompt-history-prev title="Previous prompt">&lt;</button>
        <button type="button" data-prompt-history-next title="Next prompt">&gt;</button>
        <button type="button" data-prompt-history-clear>Clear History</button>
        <button type="submit">Send</button>
      </div>
    </form>
    <span>${escapeHtml(view.status || 'Ready')}${history.length ? ` / ${history.length} history` : ''}</span>
  </div>`;
}

function renderChatView(view) {
  const messages = Array.isArray(view.messages) ? view.messages : [];
  return `<div class="chat-view">
    <div class="chat-messages">
      ${messages.map((message) => chatMessageHtml(message)).join('') || '<div class="chat-empty">No chat messages</div>'}
    </div>
    <span>${escapeHtml(view.status || 'No chat messages')}</span>
  </div>`;
}

function chatMessageHtml(message) {
  const role = String(message?.role || 'assistant') === 'user' ? 'user' : 'assistant';
  const label = role === 'user' ? 'You' : 'LLM';
  return `<div class="chat-message ${role}">
    <b>${label}</b>
    <p>${escapeHtml(message?.text || '')}</p>
  </div>`;
}

function renderTf3d(view) {
  const frameNames = Array.isArray(view.frameNames) ? view.frameNames : [];
  const rootFrame = String(view.rootFrame || '');
  const options = frameNames.map((name) => `<option value="${escapeAttr(name)}" ${name === rootFrame ? 'selected' : ''}>${escapeHtml(name)}</option>`).join('');
  return `<div class="tf3d-view" data-tf3d-view>
    <div class="tf3d-toolbar">
      <label><span>Root</span><select data-tf-root>${options || '<option value="">No frames</option>'}</select></label>
      <span data-tf-status>${escapeHtml(view.status || 'No TF frames')}</span>
    </div>
    <div class="tf3d-canvas" data-tf-canvas></div>
  </div>`;
}

let threeModulePromise = null;
let colladaLoaderPromise = null;
let stlLoaderPromise = null;
let objLoaderPromise = null;

function loadThreeModule() {
  if (!threeModulePromise) {
    threeModulePromise = import('https://cdn.jsdelivr.net/npm/three@0.165.0/build/three.module.js');
  }
  return threeModulePromise;
}

function loadColladaLoader() {
  if (!colladaLoaderPromise) colladaLoaderPromise = import('https://esm.sh/three@0.165.0/examples/jsm/loaders/ColladaLoader.js');
  return colladaLoaderPromise;
}

function loadStlLoader() {
  if (!stlLoaderPromise) stlLoaderPromise = import('https://esm.sh/three@0.165.0/examples/jsm/loaders/STLLoader.js');
  return stlLoaderPromise;
}

function loadObjLoader() {
  if (!objLoaderPromise) objLoaderPromise = import('https://esm.sh/three@0.165.0/examples/jsm/loaders/OBJLoader.js');
  return objLoaderPromise;
}

function patchTf3dViewEl(el, view) {
  stopPlotAnimation(el);
  let container = el.querySelector('[data-tf3d-view]');
  if (!container) {
    el.innerHTML = renderTf3d(view);
    container = el.querySelector('[data-tf3d-view]');
  }
  const statusEl = container?.querySelector('[data-tf-status]');
  if (statusEl) statusEl.textContent = view?.status || 'No TF frames';
  const select = container?.querySelector('[data-tf-root]');
  const names = Array.isArray(view?.frameNames) ? view.frameNames : [];
  const rootFrame = String(view?.rootFrame || '');
  if (select) {
    const signature = names.join('\n');
    if (select.dataset.optionsSignature !== signature) {
      select.innerHTML = names.length
        ? names.map((name) => `<option value="${escapeAttr(name)}">${escapeHtml(name)}</option>`).join('')
        : '<option value="">No frames</option>';
      select.dataset.optionsSignature = signature;
    }
    if (select.value !== rootFrame) select.value = rootFrame;
    if (!select._tfRootBound) {
      select._tfRootBound = true;
      select.onchange = () => {
        const node = nodeFor(el.dataset.nodeView);
        if (!node) return;
        node.params = { ...(node.params || {}), rootFrame: select.value || '' };
        scheduleRun();
      };
    }
  }
  const canvasHost = container?.querySelector('[data-tf-canvas]');
  if (!canvasHost) return;
  ensureTf3dRenderer(canvasHost).then((viewer) => {
    viewer.view = view || {};
    renderTf3dScene(viewer);
  }).catch((err) => {
    canvasHost.innerHTML = `<div class="view-empty">${escapeHtml(`Three.js load failed: ${err?.message || err}`)}</div>`;
  });
}

async function ensureTf3dRenderer(host) {
  if (host._tf3dViewer) return host._tf3dViewer;
  const THREE = await loadThreeModule();
  host.innerHTML = '';
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x050607);
  const camera = new THREE.PerspectiveCamera(55, 1, 0.01, 1000);
  camera.up.set(0, 0, 1);
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.domElement.className = 'tf3d-renderer';
  host.appendChild(renderer.domElement);
  const gridGroup = new THREE.Group();
  scene.add(gridGroup);
  const group = new THREE.Group();
  scene.add(group);
  const light = new THREE.DirectionalLight(0xffffff, 1.4);
  light.position.set(3, 4, 5);
  scene.add(light);
  scene.add(new THREE.AmbientLight(0xffffff, 0.55));
  const viewer = {
    THREE,
    host,
    scene,
    camera,
    renderer,
    gridGroup,
    group,
    controls: { yaw: -0.8, pitch: 0.6, distance: 5, target: new THREE.Vector3(), dragging: false, lastX: 0, lastY: 0 },
    view: {},
  };
  bindTf3dControls(viewer);
  const resizeObserver = new ResizeObserver(() => drawTf3d(viewer));
  resizeObserver.observe(host);
  viewer.resizeObserver = resizeObserver;
  host._tf3dViewer = viewer;
  drawTf3d(viewer);
  return viewer;
}

function bindTf3dControls(viewer) {
  const canvas = viewer.renderer.domElement;
  canvas.addEventListener('pointerdown', (ev) => {
    viewer.controls.dragging = true;
    viewer.controls.lastX = ev.clientX;
    viewer.controls.lastY = ev.clientY;
    canvas.setPointerCapture(ev.pointerId);
  });
  canvas.addEventListener('pointermove', (ev) => {
    if (!viewer.controls.dragging) return;
    const dx = ev.clientX - viewer.controls.lastX;
    const dy = ev.clientY - viewer.controls.lastY;
    viewer.controls.lastX = ev.clientX;
    viewer.controls.lastY = ev.clientY;
    if (ev.shiftKey || ev.buttons === 2) {
      const scale = viewer.controls.distance * 0.0015;
      const right = new viewer.THREE.Vector3().setFromMatrixColumn(viewer.camera.matrix, 0).multiplyScalar(-dx * scale);
      const up = new viewer.THREE.Vector3().setFromMatrixColumn(viewer.camera.matrix, 1).multiplyScalar(dy * scale);
      viewer.controls.target.add(right).add(up);
    } else {
      viewer.controls.yaw -= dx * 0.006;
      viewer.controls.pitch = clamp(viewer.controls.pitch + dy * 0.006, -1.45, 1.45);
    }
    drawTf3d(viewer);
  });
  canvas.addEventListener('pointerup', (ev) => {
    viewer.controls.dragging = false;
    try { canvas.releasePointerCapture(ev.pointerId); } catch (_) {}
  });
  canvas.addEventListener('contextmenu', (ev) => ev.preventDefault());
  canvas.addEventListener('wheel', (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    viewer.controls.distance = clamp(viewer.controls.distance * (ev.deltaY > 0 ? 1.12 : 0.88), 0.2, 200);
    drawTf3d(viewer);
  }, { passive: false });
}

function renderTf3dScene(viewer) {
  const { THREE, group, gridGroup } = viewer;
  while (group.children.length) {
    const child = group.children.pop();
    child.traverse?.((item) => {
      item.geometry?.dispose?.();
      if (Array.isArray(item.material)) item.material.forEach((mat) => mat.dispose?.());
      else item.material?.dispose?.();
      if (item.material?.map && item.material.map !== tf3dCircleTexture) item.material.map.dispose?.();
    });
  }
  while (gridGroup.children.length) {
    const child = gridGroup.children.pop();
    child.geometry?.dispose?.();
    child.material?.dispose?.();
  }
  const model = tf3dWorldModel(viewer.view || {});
  const settings = tf3dSettings(viewer.view || {});
  const extent = Math.max(1, model.extent || 1);
  const axisLen = settings.axisSize;
  addTf3dRootGrid(THREE, gridGroup, settings.gridSize, settings.gridStep);
  if (settings.occupancyGridDrawBehind) {
    (viewer.view?.occupancyGrids || []).forEach((grid) => addTf3dOccupancyGrid(THREE, group, grid, model, settings));
  }
  if (settings.showRobotModel) addTf3dRobotModel(viewer, group, viewer.view?.robotModel, model, settings);
  model.edges.forEach((edge) => addTf3dLine(THREE, group, edge.from, edge.to, edge.static ? 0x6aa6ff : 0xf6c14b));
  model.frames.forEach((frame) => {
    addTf3dAxes(THREE, group, frame.position, frame.rotation, axisLen);
    if (settings.showLabels) group.add(makeTf3dLabel(THREE, frame.name, frame.position, axisLen));
  });
  (viewer.view?.pointClouds || []).forEach((cloud) => addTf3dPointCloud(THREE, group, cloud, model, settings));
  if (!settings.occupancyGridDrawBehind) {
    (viewer.view?.occupancyGrids || []).forEach((grid) => addTf3dOccupancyGrid(THREE, group, grid, model, settings));
  }
  if (!viewer._fitDone || viewer._fitRoot !== model.root) {
    viewer.controls.target.set(0, 0, 0);
    viewer.controls.distance = Math.max(2.5, extent * 2.2);
    viewer._fitDone = true;
    viewer._fitRoot = model.root;
  }
  drawTf3d(viewer);
}

function drawTf3d(viewer) {
  const { host, camera, renderer, scene, controls } = viewer;
  const width = Math.max(1, host.clientWidth || 320);
  const height = Math.max(1, host.clientHeight || 220);
  renderer.setSize(width, height, false);
  camera.aspect = width / height;
  const cp = Math.cos(controls.pitch);
  camera.position.set(
    controls.target.x + controls.distance * Math.cos(controls.yaw) * cp,
    controls.target.y + controls.distance * Math.sin(controls.yaw) * cp,
    controls.target.z + controls.distance * Math.sin(controls.pitch),
  );
  camera.lookAt(controls.target);
  camera.updateProjectionMatrix();
  renderer.render(scene, camera);
}

function tf3dWorldModel(view) {
  const frames = Array.isArray(view.frames) ? view.frames : [];
  const names = Array.isArray(view.frameNames) ? view.frameNames : [];
  const root = String(view.rootFrame || names[0] || '');
  const links = new Map();
  frames.forEach((item) => {
    const parent = String(item?.parent || '');
    const child = String(item?.child || '');
    if (!parent || !child) return;
    const translation = vector3Array(item.translation);
    const rotation = quatArray(item.rotation);
    if (!links.has(parent)) links.set(parent, []);
    if (!links.has(child)) links.set(child, []);
    links.get(parent).push({ to: child, translation, rotation, static: Boolean(item.static), inverse: false });
    links.get(child).push({ to: parent, translation, rotation, static: Boolean(item.static), inverse: true });
  });
  const world = new Map();
  const queue = [];
  if (root) {
    world.set(root, { position: [0, 0, 0], rotation: [0, 0, 0, 1] });
    queue.push(root);
  }
  while (queue.length) {
    const current = queue.shift();
    const base = world.get(current);
    (links.get(current) || []).forEach((edge) => {
      if (world.has(edge.to)) return;
      const rel = edge.inverse ? invertTransform(edge.translation, edge.rotation) : { translation: edge.translation, rotation: edge.rotation };
      const position = addVec3(base.position, rotateVec3(base.rotation, rel.translation));
      const rotation = normalizeQuat(mulQuat(base.rotation, rel.rotation));
      world.set(edge.to, { position, rotation });
      queue.push(edge.to);
    });
  }
  const modelFrames = [...world.entries()].map(([name, pose]) => ({ name, ...pose }));
  const modelEdges = frames.map((item) => {
    const parent = world.get(String(item?.parent || ''));
    const child = world.get(String(item?.child || ''));
    if (!parent || !child) return null;
    return { from: parent.position, to: child.position, static: Boolean(item.static) };
  }).filter(Boolean);
  const extent = modelFrames.reduce((acc, frame) => Math.max(acc, Math.hypot(frame.position[0], frame.position[1], frame.position[2])), 1);
  return { root, frames: modelFrames, edges: modelEdges, extent, world };
}

function tf3dSettings(view) {
  const numberValue = (key, fallback, min) => {
    const value = Number(view[key]);
    return Number.isFinite(value) ? Math.max(min, value) : fallback;
  };
  return {
    gridStep: numberValue('gridStep', 0.25, 0.01),
    gridSize: numberValue('gridSize', 4, 0.1),
    axisSize: numberValue('axisSize', 0.35, 0.01),
    showLabels: view.showLabels !== false,
    pointCloudStyle: view.pointCloudStyle === 'circle' ? 'circle' : 'square',
    pointCloudSize: numberValue('pointCloudSize', 0.03, 0.001),
    pointCloudColor: /^#[0-9a-fA-F]{6}$/.test(String(view.pointCloudColor || '')) ? String(view.pointCloudColor) : '#ffffff',
    pointCloudOpacity: Math.max(0, Math.min(1, numberValue('pointCloudOpacity', 1, 0))),
    occupancyGridColorScheme: ['map', 'costmap', 'raw'].includes(String(view.occupancyGridColorScheme || 'map')) ? String(view.occupancyGridColorScheme || 'map') : 'map',
    occupancyGridAlpha: Math.max(0, Math.min(1, numberValue('occupancyGridAlpha', 0.7, 0))),
    occupancyGridDrawBehind: view.occupancyGridDrawBehind !== false,
    showRobotModel: view.showRobotModel === true,
    robotModelColor: /^#[0-9a-fA-F]{6}$/.test(String(view.robotModelColor || '')) ? String(view.robotModelColor) : '#9aa4b2',
    robotModelOpacity: Math.max(0, Math.min(1, numberValue('robotModelOpacity', 0.45, 0))),
  };
}

function vector3Array(value) {
  return [0, 1, 2].map((index) => Number(Array.isArray(value) ? value[index] : 0) || 0);
}

function quatArray(value) {
  const q = [0, 1, 2, 3].map((index) => Number(Array.isArray(value) ? value[index] : (index === 3 ? 1 : 0)) || (index === 3 ? 1 : 0));
  return normalizeQuat(q);
}

function normalizeQuat(q) {
  const norm = Math.hypot(q[0], q[1], q[2], q[3]) || 1;
  return [q[0] / norm, q[1] / norm, q[2] / norm, q[3] / norm];
}

function mulQuat(a, b) {
  return [
    a[3] * b[0] + a[0] * b[3] + a[1] * b[2] - a[2] * b[1],
    a[3] * b[1] - a[0] * b[2] + a[1] * b[3] + a[2] * b[0],
    a[3] * b[2] + a[0] * b[1] - a[1] * b[0] + a[2] * b[3],
    a[3] * b[3] - a[0] * b[0] - a[1] * b[1] - a[2] * b[2],
  ];
}

function rotateVec3(q, v) {
  const p = [v[0], v[1], v[2], 0];
  const qi = [-q[0], -q[1], -q[2], q[3]];
  const r = mulQuat(mulQuat(q, p), qi);
  return [r[0], r[1], r[2]];
}

function addVec3(a, b) {
  return [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
}

function invertTransform(translation, rotation) {
  const inverseRotation = [-rotation[0], -rotation[1], -rotation[2], rotation[3]];
  return { translation: rotateVec3(inverseRotation, [-translation[0], -translation[1], -translation[2]]), rotation: inverseRotation };
}

function quatFromRpy(rpy) {
  const roll = Number(rpy[0] || 0);
  const pitch = Number(rpy[1] || 0);
  const yaw = Number(rpy[2] || 0);
  const cr = Math.cos(roll / 2);
  const sr = Math.sin(roll / 2);
  const cp = Math.cos(pitch / 2);
  const sp = Math.sin(pitch / 2);
  const cy = Math.cos(yaw / 2);
  const sy = Math.sin(yaw / 2);
  return normalizeQuat([
    sr * cp * cy - cr * sp * sy,
    cr * sp * cy + sr * cp * sy,
    cr * cp * sy - sr * sp * cy,
    cr * cp * cy + sr * sp * sy,
  ]);
}

function addTf3dLine(THREE, group, from, to, color) {
  const geometry = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(...from), new THREE.Vector3(...to)]);
  group.add(new THREE.Line(geometry, new THREE.LineBasicMaterial({ color })));
}

function addTf3dRootGrid(THREE, group, size, step) {
  size = Math.max(0.1, Number(size || 4));
  step = Math.max(0.01, Number(step || 0.25));
  const points = [];
  for (let value = -size; value <= size + 1e-6; value += step) {
    points.push(new THREE.Vector3(-size, value, 0), new THREE.Vector3(size, value, 0));
    points.push(new THREE.Vector3(value, -size, 0), new THREE.Vector3(value, size, 0));
  }
  const geometry = new THREE.BufferGeometry().setFromPoints(points);
  const material = new THREE.LineBasicMaterial({ color: 0xb46cff, transparent: true, opacity: 0.82, depthWrite: false });
  group.add(new THREE.LineSegments(geometry, material));
}

function addTf3dAxes(THREE, group, position, rotation, length) {
  const thickness = Math.max(0.002, length * 0.1);
  [
    [0xff4d4d, [length / 2, 0, 0], [length, thickness, thickness]],
    [0x4dd36f, [0, length / 2, 0], [thickness, length, thickness]],
    [0x4d8dff, [0, 0, length / 2], [thickness, thickness, length]],
  ].forEach(([color, localCenter, size]) => {
    const center = addVec3(position, rotateVec3(rotation, localCenter));
    const box = new THREE.Mesh(
      new THREE.BoxGeometry(size[0], size[1], size[2]),
      new THREE.MeshStandardMaterial({ color, roughness: 0.45, metalness: 0.0 }),
    );
    box.position.fromArray(center);
    box.quaternion.fromArray(rotation);
    group.add(box);
  });
}

function addTf3dPointCloud(THREE, group, cloud, model, settings) {
  const points = Array.isArray(cloud?.points) ? cloud.points : [];
  if (!points.length) return;
  const pose = model.world?.get?.(String(cloud.frameId || '')) || { position: [0, 0, 0], rotation: [0, 0, 0, 1] };
  const positions = new Float32Array(points.length * 3);
  points.forEach((point, index) => {
    const rotated = rotateVec3(pose.rotation, vector3Array(point));
    const world = addVec3(pose.position, rotated);
    positions[index * 3] = world[0];
    positions[index * 3 + 1] = world[1];
    positions[index * 3 + 2] = world[2];
  });
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  const material = new THREE.PointsMaterial({
    color: new THREE.Color(settings.pointCloudColor),
    size: settings.pointCloudSize,
    sizeAttenuation: true,
    transparent: settings.pointCloudOpacity < 1,
    opacity: settings.pointCloudOpacity,
    depthWrite: settings.pointCloudOpacity >= 1,
  });
  if (settings.pointCloudStyle === 'circle') {
    material.map = circlePointTexture(THREE);
    material.alphaTest = 0.05;
  }
  group.add(new THREE.Points(geometry, material));
}

function addTf3dOccupancyGrid(THREE, group, grid, model, settings) {
  const width = Math.floor(Number(grid?.width || 0));
  const height = Math.floor(Number(grid?.height || 0));
  const resolution = Number(grid?.resolution || 0);
  const data = Array.isArray(grid?.data) ? grid.data : [];
  if (width <= 0 || height <= 0 || resolution <= 0 || !data.length) return;
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  const image = ctx.createImageData(width, height);
  for (let row = 0; row < height; row += 1) {
    const canvasRow = height - 1 - row;
    for (let col = 0; col < width; col += 1) {
      const sourceIndex = row * width + col;
      const targetIndex = (canvasRow * width + col) * 4;
      const [r, g, b, a] = occupancyGridCellColor(Number(data[sourceIndex] ?? -1), settings);
      image.data[targetIndex] = r;
      image.data[targetIndex + 1] = g;
      image.data[targetIndex + 2] = b;
      image.data[targetIndex + 3] = a;
    }
  }
  ctx.putImageData(image, 0, 0);
  const texture = new THREE.CanvasTexture(canvas);
  texture.needsUpdate = true;
  texture.colorSpace = THREE.SRGBColorSpace;
  const material = new THREE.MeshBasicMaterial({
    map: texture,
    transparent: true,
    opacity: 1,
    depthWrite: false,
    side: THREE.DoubleSide,
  });
  const geometry = new THREE.PlaneGeometry(width * resolution, height * resolution);
  const mesh = new THREE.Mesh(geometry, material);
  const framePose = model.world?.get?.(String(grid.frameId || '')) || { position: [0, 0, 0], rotation: [0, 0, 0, 1] };
  const origin = grid.origin || {};
  const originPosition = vector3Array(origin.position || [0, 0, 0]);
  const originRotation = quatArray(origin.orientation || [0, 0, 0, 1]);
  const rotation = normalizeQuat(mulQuat(framePose.rotation, originRotation));
  const localCenter = [width * resolution * 0.5, height * resolution * 0.5, settings.occupancyGridDrawBehind ? -0.001 : 0.001];
  const originWorld = addVec3(framePose.position, rotateVec3(framePose.rotation, originPosition));
  const centerWorld = addVec3(originWorld, rotateVec3(rotation, localCenter));
  mesh.position.fromArray(centerWorld);
  mesh.quaternion.fromArray(rotation);
  mesh.renderOrder = settings.occupancyGridDrawBehind ? -10 : 10;
  group.add(mesh);
}

function occupancyGridCellColor(value, settings) {
  const alpha = Math.round(255 * Math.max(0, Math.min(1, Number(settings.occupancyGridAlpha ?? 0.7))));
  if (settings.occupancyGridColorScheme === 'raw') {
    if (value < 0) return [80, 80, 80, alpha];
    const shade = Math.round(255 * (1 - Math.max(0, Math.min(100, value)) / 100));
    return [shade, shade, shade, alpha];
  }
  if (settings.occupancyGridColorScheme === 'costmap') {
    if (value < 0) return [90, 90, 90, Math.round(alpha * 0.55)];
    if (value <= 0) return [0, 0, 0, 0];
    const t = Math.max(0, Math.min(100, value)) / 100;
    const r = Math.round(255 * Math.min(1, t * 1.8));
    const g = Math.round(210 * Math.max(0, 1 - Math.abs(t - 0.35) * 2.2));
    const b = Math.round(255 * Math.max(0, 1 - t * 1.6));
    return [r, g, b, alpha];
  }
  if (value < 0) return [0, 0, 0, 0];
  if (value <= 0) return [245, 245, 245, Math.round(alpha * 0.45)];
  const shade = Math.round(245 * (1 - Math.max(0, Math.min(100, value)) / 100));
  return [shade, shade, shade, alpha];
}

function addTf3dRobotModel(viewer, group, robotModel, model, settings) {
  const THREE = viewer.THREE;
  const visuals = Array.isArray(robotModel?.visuals) ? robotModel.visuals : [];
  if (!visuals.length) return;
  viewer._robotModelToken = (viewer._robotModelToken || 0) + 1;
  const token = viewer._robotModelToken;
  const material = new THREE.MeshStandardMaterial({
    color: new THREE.Color(settings.robotModelColor),
    transparent: settings.robotModelOpacity < 1,
    opacity: settings.robotModelOpacity,
    roughness: 0.55,
  });
  visuals.forEach((visual) => {
    const linkPose = model.world?.get?.(String(visual.link || ''));
    if (!linkPose) return;
    let geometry = null;
    if (visual.type === 'box') {
      const size = vector3Array(visual.size || [1, 1, 1]);
      geometry = new THREE.BoxGeometry(size[0], size[1], size[2]);
    } else if (visual.type === 'cylinder') {
      geometry = new THREE.CylinderGeometry(Number(visual.radius || 0.5), Number(visual.radius || 0.5), Number(visual.length || 1), 24);
      geometry.rotateX(Math.PI / 2);
    } else if (visual.type === 'sphere') {
      geometry = new THREE.SphereGeometry(Number(visual.radius || 0.5), 24, 16);
    } else if (visual.type === 'mesh') {
      addTf3dRobotMesh(viewer, group, visual, linkPose, material, token);
      return;
    }
    if (!geometry) return;
    const mesh = new THREE.Mesh(geometry, material.clone());
    const localTranslation = vector3Array(visual.xyz || [0, 0, 0]);
    const localRotation = quatFromRpy(vector3Array(visual.rpy || [0, 0, 0]));
    const worldPosition = addVec3(linkPose.position, rotateVec3(linkPose.rotation, localTranslation));
    const worldRotation = normalizeQuat(mulQuat(linkPose.rotation, localRotation));
    mesh.position.fromArray(worldPosition);
    mesh.quaternion.fromArray(worldRotation);
    group.add(mesh);
  });
}

function addTf3dRobotMesh(viewer, group, visual, linkPose, material, token) {
  const url = String(visual.url || '');
  if (!url) return;
  const extension = String(visual.extension || '').toLowerCase();
  const localTranslation = vector3Array(visual.xyz || [0, 0, 0]);
  const localRotation = quatFromRpy(vector3Array(visual.rpy || [0, 0, 0]));
  const worldPosition = addVec3(linkPose.position, rotateVec3(linkPose.rotation, localTranslation));
  const worldRotation = normalizeQuat(mulQuat(linkPose.rotation, localRotation));
  const scale = vector3Array(visual.scale || [1, 1, 1]);
  const applyObject = (object) => {
    if (viewer._robotModelToken !== token) return;
    object.position.fromArray(worldPosition);
    object.quaternion.fromArray(worldRotation);
    object.scale.set(scale[0], scale[1], scale[2]);
    object.traverse?.((child) => {
      if (child.isMesh) child.material = material.clone();
    });
    group.add(object);
    drawTf3d(viewer);
  };
  if (extension === '.dae') {
    loadColladaLoader().then(({ ColladaLoader }) => {
      new ColladaLoader().load(url, (collada) => applyObject(collada.scene));
    }).catch((err) => console.warn('DAE load failed', err));
  } else if (extension === '.stl') {
    loadStlLoader().then(({ STLLoader }) => {
      new STLLoader().load(url, (geometry) => {
        applyObject(new viewer.THREE.Mesh(geometry, material.clone()));
      });
    }).catch((err) => console.warn('STL load failed', err));
  } else if (extension === '.obj') {
    loadObjLoader().then(({ OBJLoader }) => {
      new OBJLoader().load(url, applyObject);
    }).catch((err) => console.warn('OBJ load failed', err));
  }
}

let tf3dCircleTexture = null;

function circlePointTexture(THREE) {
  if (tf3dCircleTexture) return tf3dCircleTexture;
  const canvas = document.createElement('canvas');
  canvas.width = 64;
  canvas.height = 64;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, 64, 64);
  ctx.fillStyle = '#ffffff';
  ctx.beginPath();
  ctx.arc(32, 32, 28, 0, Math.PI * 2);
  ctx.fill();
  tf3dCircleTexture = new THREE.CanvasTexture(canvas);
  return tf3dCircleTexture;
}

function makeTf3dLabel(THREE, text, position, scale) {
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  canvas.width = 256;
  canvas.height = 64;
  ctx.fillStyle = 'rgba(5, 6, 7, 0.72)';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#e7edf3';
  ctx.font = '24px ui-sans-serif, system-ui, sans-serif';
  ctx.textBaseline = 'middle';
  ctx.fillText(String(text).slice(0, 28), 10, 32);
  const texture = new THREE.CanvasTexture(canvas);
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true }));
  sprite.position.set(position[0] + scale * 0.18, position[1] + scale * 0.18, position[2]);
  sprite.scale.set(scale * 1.5, scale * 0.38, 1);
  return sprite;
}

function renderPlot(series, label, view = {}) {
  const model = plotRenderModel(series, label, view);
  return `<div class="plot-view"><svg viewBox="0 0 280 120"><polyline points="${escapeAttr(model.points)}"></polyline></svg><span>${escapeHtml(model.caption)}</span></div>`;
}

function plotRenderModel(series, label, view = {}) {
  const width = 280;
  const height = 120;
  const allPoints = series.map((item, index) => {
    if (item && typeof item === 'object') return { t: Number(item.t), y: Number(item.y) };
    return { t: index, y: Number(item) };
  }).filter((point) => Number.isFinite(point.t) && Number.isFinite(point.y));
  const latestT = allPoints.length ? Math.max(...allPoints.map((point) => point.t)) : 0;
  const windowSec = Math.max(0.1, Number(view.xAxisSeconds || (latestT - (allPoints[0]?.t || 0)) || 1));
  let renderT = latestT;
  const statusTime = Number(view.statusTime || 0);
  const clientReceivedAt = Number(view.clientReceivedAt || 0);
  if (view.running !== false && Number.isFinite(statusTime) && statusTime > 0 && Number.isFinite(clientReceivedAt) && clientReceivedAt > 0) {
    renderT = Math.max(renderT, statusTime + Math.max(0, (performance.now() / 1000) - clientReceivedAt));
  }
  const startT = renderT - windowSec;
  const pointsData = allPoints.filter((point) => point.t >= startT);
  if (pointsData.length < 2) {
    return { points: '', caption: label || 'Waiting for values' };
  }
  const values = pointsData.map((point) => point.y);
  const yAxis = view.yAxis || {};
  const fixed = yAxis.mode === 'fixed';
  let min = fixed ? Number(yAxis.min) : Math.min(...values);
  let max = fixed ? Number(yAxis.max) : Math.max(...values);
  if (!Number.isFinite(min)) min = Math.min(...values);
  if (!Number.isFinite(max)) max = Math.max(...values);
  if (max === min) {
    max += 0.5;
    min -= 0.5;
  }
  const span = max - min || 1;
  const plotPoints = decimatePlotPoints(pointsData, width);
  const points = plotPoints.map((point) => {
    const x = clamp(((point.t - startT) / windowSec) * width, 0, width);
    const y = clamp(height - ((point.y - min) / span) * (height - 12) - 6, 0, height);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return { points, caption: `${label} ${windowSec.toFixed(1)}s / ${min.toFixed(3)} .. ${max.toFixed(3)}` };
}

function decimatePlotPoints(points, width) {
  const maxPoints = Math.max(64, width * 2);
  if (points.length <= maxPoints) return points;
  const step = points.length / maxPoints;
  const sampled = [];
  for (let i = 0; i < maxPoints; i += 1) {
    sampled.push(points[Math.min(points.length - 1, Math.floor(i * step))]);
  }
  const last = points[points.length - 1];
  if (sampled[sampled.length - 1] !== last) sampled.push(last);
  return sampled;
}

function loadImageFile(node, file) {
  if (!file) return;
  const img = new Image();
  img.onload = () => {
    const frame = imageElementToMessage(img);
    node.params = {
      ...(node.params || {}),
      fileName: file.name,
      dataUrl: frame.dataUrl,
      imageMessage: frame.message,
      publishMode: node.params?.publishMode || 'oneshot',
      publishHz: Math.max(0.01, Number(node.params?.publishHz || 1)),
    };
    URL.revokeObjectURL(img.src);
    renderAll();
    scheduleRun();
  };
  img.src = URL.createObjectURL(file);
}

async function loadVideoFile(node, file) {
  if (!file) return;
  stopVideoInput(node.id);
  const video = document.createElement('video');
  video.muted = true;
  video.loop = false;
  video.playsInline = true;
  video.preload = 'auto';
  const controller = {
    nodeId: node.id,
    video,
    url: URL.createObjectURL(file),
    fileName: file.name,
    loop: Boolean(node.params?.loop),
    ended: false,
  };
  state.videoInputs[node.id] = controller;
  video.onloadeddata = () => {
    video.pause();
    captureVideoFrame(node, controller, { updateGraph: false });
    renderAll();
    // Detect native FPS by observing frame timestamps
    _detectVideoNativeFps(video, (fps) => {
      video.pause();
      video.currentTime = 0;
      if (fps && Number.isFinite(fps) && fps > 0) {
        const detectedFps = Math.round(fps * 100) / 100;
        node.params = {
          ...(node.params || {}),
          detectedFps,
          publishHz: detectedFps,
        };
        if (node.params.serverDecode && node.params.videoPath) {
          state.videoPayloadDirty = true;
          state.videoDirtyNodes.add(node.id);
          pushRunPayloadUpdate();
        }
        renderAll();
      }
    });
  };
  video.onended = () => handleVideoEnded(controller);
  video.src = controller.url;
}

async function selectVideoFileFromServer() {
  const response = await fetch('/api/select-video-file', { method: 'POST' });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `HTTP ${response.status}`);
  return data.canceled ? null : data;
}

async function selectUrdfFileFromServer() {
  const response = await fetch('/api/select-urdf-file', { method: 'POST' });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `HTTP ${response.status}`);
  return data.canceled ? null : data;
}

async function fetchRobotModel(path) {
  const response = await fetch('/api/robot-model', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ path }),
  });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

async function selectMcapFileFromServer() {
  const response = await fetch('/api/select-mcap-file', { method: 'POST' });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `HTTP ${response.status}`);
  return data.canceled ? null : data;
}

async function selectMcapRecordFileFromServer() {
  const response = await fetch('/api/select-mcap-record-file', { method: 'POST' });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `HTTP ${response.status}`);
  return data.canceled ? null : data;
}

async function openMcapFileFromServer(path) {
  const response = await fetch('/api/open-mcap-file', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ path }),
  });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function applyMcapSelection(node, selected) {
  const channels = (Array.isArray(selected.channels) ? selected.channels : [])
    .filter((channel) => channel?.topic)
    .map((channel) => ({
      topic: String(channel.topic),
      type: String(channel.type || '').replace(/\./g, '/'),
      messageCount: Math.max(0, Number(channel.messageCount || 0)),
      messageEncoding: String(channel.messageEncoding || ''),
      schemaEncoding: String(channel.schemaEncoding || ''),
      ros2Compatible: Boolean(channel.ros2Compatible),
    }));
  const usedIds = new Set();
  const outputTopics = {};
  const ros2Channels = channels.filter((channel) => isRos2McapChannel(channel));
  const outputs = ros2Channels.map((channel, index) => {
    const base = safeFileName(channel.topic.replace(/^\/+/, '') || `topic_${index + 1}`).replace(/-/g, '_') || `topic_${index + 1}`;
    let id = `out_${base}`;
    while (usedIds.has(id)) id = `${id}_${index + 1}`;
    usedIds.add(id);
    outputTopics[id] = channel.topic;
    return {
      id,
      name: channel.topic,
      dataType: channel.type,
    };
  });
  const outputIds = new Set((node.outputs || []).map((port) => port.id));
  state.links = state.links.filter((link) => link.fromNode !== node.id || !outputIds.has(link.fromPort));
  node.outputs = outputs;
  node.params = {
    ...(node.params || {}),
    mcapPath: selected.path,
    fileName: selected.fileName || selected.path.split(/[\\/]/).filter(Boolean).pop() || selected.path,
    mcapFiles: Array.isArray(selected.mcapFiles) ? selected.mcapFiles : [],
    fileCount: Math.max(1, Number(selected.fileCount || (Array.isArray(selected.mcapFiles) ? selected.mcapFiles.length : 1))),
    mcapChannels: channels,
    mcapOutputTopics: outputTopics,
    startTimeNs: Number(selected.startTimeNs || 0),
    endTimeNs: Number(selected.endTimeNs || 0),
    durationSec: Number(selected.durationSec || 0),
    metadataPath: selected.metadataPath || '',
    metadataError: selected.metadataError || '',
    probeError: selected.probeError || '',
    playbackRate: Math.max(0.001, Number(node.params?.playbackRate || 1)),
    loop: Boolean(node.params?.loop),
  };
  renderAll();
  commitHistory();
  scheduleRun();
}

function isRos2McapChannel(channel) {
  const type = String(channel?.type || '').replace(/\./g, '/');
  const encoding = String(channel?.messageEncoding || '').toLowerCase();
  if (channel?.ros2Compatible === true) return true;
  return encoding === 'cdr' && type.includes('/msg/');
}

function _detectVideoNativeFps(video, callback) {
  if (typeof video.requestVideoFrameCallback !== 'function') {
    callback(null);
    return;
  }
  const times = [];
  let done = false;
  function finish() {
    if (done) return;
    done = true;
    const span = times.length > 1 ? times[times.length - 1] - times[0] : 0;
    callback(span > 0 ? (times.length - 1) / span : null);
  }
  function tick(_now, meta) {
    if (done) return;
    times.push(meta.mediaTime);
    const elapsed = times[times.length - 1] - times[0];
    if (times.length >= 7 || elapsed >= 0.5) {
      finish();
      return;
    }
    video.requestVideoFrameCallback(tick);
  }
  video.requestVideoFrameCallback(tick);
  video.addEventListener('ended', finish, { once: true });
  video.play().catch(() => callback(null));
}

function captureVideoFrame(node, controller, options = {}) {
  const video = controller.video;
  if (!video.videoWidth || !video.videoHeight || video.readyState < 2 || video.seeking) return;
  const frame = imageElementToMessage(video, 0, { includeDataUrl: options.includeDataUrl !== false });
  node.params = {
    ...(node.params || {}),
    fileName: controller.fileName,
    dataUrl: frame.dataUrl,
    frameMessage: frame.message,
    loop: controller.loop,
    publishHz: effectiveVideoHz(node),
    frameSkip: videoFrameSkip(node),
    duration: Number.isFinite(video.duration) ? video.duration : 0,
    currentTime: video.currentTime || 0,
    ended: controller.ended,
  };
  state.nodeViews[node.id] = frame.dataUrl
    ? { kind: 'image', dataUrl: frame.dataUrl, status: videoStatus(controller) }
    : { kind: 'image', raw: frame.message, status: videoStatus(controller) };
  updateNodeViews({ [node.id]: { view: state.nodeViews[node.id] } });
  if (options.markDirty !== false) {
    state.videoPayloadDirty = true;
    state.videoDirtyNodes.add(node.id);
  }
  if (options.renderAll) renderAll();
  if (options.updateGraph) scheduleRun({ invalidateReady: false });
}

function videoStatus(controller) {
  const video = controller.video;
  const node = nodeFor(controller.nodeId);
  const fps = node ? effectiveVideoHz(node) : 30;
  const duration = Number.isFinite(video.duration) && video.duration > 0 ? ` / ${video.duration.toFixed(1)}s` : '';
  const stateText = controller.ended ? 'ended' : video.paused ? 'paused' : 'playing';
  return `${controller.fileName} ${stateText} ${video.currentTime.toFixed(1)}s${duration} @ ${fps.toFixed(1)}fps`;
}

function updateVideoInputsForRun(options = {}) {
  const markDirty = options.markDirty !== false;
  const now = performance.now();
  Object.values(state.videoInputs).forEach((controller) => {
    if (controller.ended && !controller.loop) return;
    const node = nodeFor(controller.nodeId);
    if (!node) return;
    if (node.params?.serverDecode) return;
    controller.video.playbackRate = 1.0;
    const hz = effectiveVideoHz(node);
    const periodMs = 1000 / hz;
    const dueAt = controller.nextCaptureAt || now;
    if (now + 1 < dueAt) return;
    controller.nextCaptureAt = nextPeriodicTimeMs(dueAt, periodMs, now);
    captureVideoFrame(node, controller, {
      updateGraph: false,
      markDirty: markDirty && !node.params?.serverDecode,
      includeDataUrl: false,
    });
  });
}

function prepareVideoInputsForRun() {
  Object.values(state.videoInputs).forEach((controller) => {
    const node = nodeFor(controller.nodeId);
    if (!node) return;
    if (controller.video.readyState >= 2) {
      captureVideoFrame(node, controller, { updateGraph: false, markDirty: false });
    }
  });
}

function updateEmbeddedVideoInputsForRun() {
  const now = performance.now();
  state.nodes.forEach((node) => {
    if (node.toolType !== 'video_file_input' || state.videoInputs[node.id]) return;
    const params = node.params || {};
    const baseFrame = params.baseFrameMessage || params.frameMessage;
    if (!params.embeddedVideo || !baseFrame) return;
    const duration = Math.max(0.1, Number(params.duration || 10));
    const fps = Math.max(1, Number(params.embeddedFps || 12));
    const loop = Boolean(params.loop ?? true);
    let controller = state.embeddedVideoInputs[node.id];
    if (!controller || controller.baseFrame !== baseFrame) {
      controller = {
        nodeId: node.id,
        baseFrame,
        startedAt: now,
        duration,
        fps,
        loop,
        ended: false,
        fileName: params.fileName || `${node.name || node.id}.embedded`,
      };
      state.embeddedVideoInputs[node.id] = controller;
    }
    controller.duration = duration;
    controller.fps = fps;
    controller.loop = loop;
    let elapsed = (now - controller.startedAt) / 1000;
    if (elapsed >= duration) {
      if (loop) {
        controller.startedAt = now - ((elapsed % duration) * 1000);
        elapsed = elapsed % duration;
      } else {
        elapsed = duration;
        controller.ended = true;
      }
    }
    const frameIndex = Math.floor(elapsed * fps) * (videoFrameSkip(node) + 1);
    const frame = syntheticVideoFrame(controller.baseFrame, frameIndex);
    if (!frame) return;
    node.params = {
      ...params,
      fileName: controller.fileName,
      dataUrl: frame.dataUrl,
      frameMessage: frame.message,
      baseFrameMessage: controller.baseFrame,
      embeddedVideo: true,
      embeddedFps: fps,
      publishHz: Math.max(0.01, Number(params.publishHz || fps)),
      frameSkip: videoFrameSkip(node),
      duration,
      currentTime: elapsed,
      ended: controller.ended,
      loop,
    };
    const stateText = controller.ended ? 'ended' : 'playing';
    state.nodeViews[node.id] = { kind: 'image', dataUrl: frame.dataUrl, status: `${controller.fileName} ${stateText} ${elapsed.toFixed(1)}s / ${duration.toFixed(1)}s` };
    updateNodeViews({ [node.id]: { view: state.nodeViews[node.id] } });
  });
}

function startVideoInputs() {
  Object.values(state.videoInputs).forEach((controller) => {
    // Always rewind to the beginning when starting a new run, regardless of loop setting
    if (controller.ended) {
      controller.video.currentTime = 0;
      controller.ended = false;
    }
    const node = nodeFor(controller.nodeId);
    if (node) controller.video.playbackRate = 1.0;
    controller.video.play().catch(() => {});
  });
}

function pauseVideoInputs() {
  Object.values(state.videoInputs).forEach((controller) => controller.video.pause());
}

function stopVideoInput(nodeId) {
  const controller = state.videoInputs[nodeId];
  if (!controller) return;
  controller.video.pause();
  controller.video.removeAttribute('src');
  controller.video.load();
  if (controller.url) URL.revokeObjectURL(controller.url);
  delete state.videoInputs[nodeId];
}

function stopAllVideoInputs() {
  Object.keys(state.videoInputs).forEach(stopVideoInput);
  state.embeddedVideoInputs = {};
}

function startEmbeddedVideoInputs() {
  const now = performance.now();
  state.nodes.forEach((node) => {
    if (node.toolType !== 'video_file_input' || state.videoInputs[node.id]) return;
    if (!node.params?.embeddedVideo || !(node.params.baseFrameMessage || node.params.frameMessage)) return;
    state.embeddedVideoInputs[node.id] = {
      nodeId: node.id,
      baseFrame: node.params.baseFrameMessage || node.params.frameMessage,
      startedAt: now,
      duration: Math.max(0.1, Number(node.params.duration || 10)),
      fps: Math.max(1, Number(node.params.embeddedFps || 12)),
      loop: Boolean(node.params.loop ?? true),
      ended: false,
      fileName: node.params.fileName || `${node.name || node.id}.embedded`,
    };
  });
}

function handleVideoEnded(controller) {
  const node = nodeFor(controller.nodeId);
  if (controller.loop) {
    controller.ended = false;
    controller.video.currentTime = 0;
    if (state.autoTimer) controller.video.play().catch(() => {});
    return;
  }
  controller.ended = true;
  if (node) captureVideoFrame(node, controller, { updateGraph: false });
  // Do not stop the run when a video ends; other nodes may still be running.
}

function imageElementToMessage(source, sizeLimit = 0, options = {}) {
  const naturalWidth = source.videoWidth || source.naturalWidth || source.width;
  const naturalHeight = source.videoHeight || source.naturalHeight || source.height;
  const scale = sizeLimit > 0 ? Math.min(1, sizeLimit / Math.max(naturalWidth, naturalHeight)) : 1;
  const width = Math.max(1, Math.round(naturalWidth * scale));
  const height = Math.max(1, Math.round(naturalHeight * scale));
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(source, 0, 0, width, height);
  const image = ctx.getImageData(0, 0, width, height);
  const rgb = new Uint8Array(width * height * 3);
  for (let src = 0, dst = 0; src < image.data.length; src += 4) {
    rgb[dst++] = image.data[src];
    rgb[dst++] = image.data[src + 1];
    rgb[dst++] = image.data[src + 2];
  }
  const dataUrl = options.includeDataUrl === false ? '' : canvas.toDataURL('image/png');
  return {
    dataUrl,
    message: {
      width,
      height,
      encoding: 'rgb8',
      is_bigendian: 0,
      step: width * 3,
      data: bytesToBase64(rgb),
      dataEncoding: 'base64',
    },
  };
}

function bytesToBase64(bytes) {
  const chunkSize = 0x8000;
  let binary = '';
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return btoa(binary);
}

function base64ToBytes(text) {
  const binary = atob(text || '');
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

function messageRgbBytes(message) {
  if (!message) return null;
  if (message.dataEncoding === 'base64' && typeof message.data === 'string') return base64ToBytes(message.data);
  if (Array.isArray(message.data)) return Uint8Array.from(message.data.map((value) => clamp(Number(value) || 0, 0, 255)));
  return null;
}

function rgbBytesToDataUrl(bytes, width, height) {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  const image = ctx.createImageData(width, height);
  for (let src = 0, dst = 0; src < bytes.length && dst < image.data.length; src += 3, dst += 4) {
    image.data[dst] = bytes[src];
    image.data[dst + 1] = bytes[src + 1] || 0;
    image.data[dst + 2] = bytes[src + 2] || 0;
    image.data[dst + 3] = 255;
  }
  ctx.putImageData(image, 0, 0);
  return canvas.toDataURL('image/png');
}

function syntheticVideoFrame(baseFrame, frameIndex) {
  const width = Number(baseFrame?.width || 0);
  const height = Number(baseFrame?.height || 0);
  const source = messageRgbBytes(baseFrame);
  if (!width || !height || !source) return null;
  const output = new Uint8Array(width * height * 3);
  const shift = frameIndex % Math.max(1, width);
  const band = (frameIndex * 3) % Math.max(1, width + height);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const srcX = (x + shift) % width;
      const src = (y * width + srcX) * 3;
      const dst = (y * width + x) * 3;
      const highlight = Math.abs((x + y) - band) < 4 ? 34 : 0;
      output[dst] = clamp((source[src] || 0) + highlight, 0, 255);
      output[dst + 1] = clamp((source[src + 1] || 0) + Math.floor(highlight / 2), 0, 255);
      output[dst + 2] = clamp(source[src + 2] || 0, 0, 255);
    }
  }
  return {
    dataUrl: rgbBytesToDataUrl(output, width, height),
    message: {
      width,
      height,
      encoding: 'rgb8',
      is_bigendian: 0,
      step: width * 3,
      data: bytesToBase64(output),
      dataEncoding: 'base64',
    },
  };
}

function scheduleRun(options = {}) {
  if (options.invalidateReady !== false) invalidateReady();
  // Editing the graph must not start execution. Continuous Run is server-driven
  // and is started only by the explicit Run / Run For controls.
  return;
}

function invalidateReady() {
  if (!state.ready && !state.readySignature) return;
  state.ready = false;
  state.readySignature = '';
  const readyButton = $('ready-model');
  if (readyButton) readyButton.classList.remove('active');
}

function startRun() {
  if (state.autoTimer) return;
  startServerRun();
}

function refreshRunTimer() {
  if (!state.autoTimer) return;
  startServerRun();
}

function runLoopHz() {
  return GRAPH_RUN_HZ;
}

function runIntervalMs() {
  return Math.max(1, Math.round(1000 / runLoopHz()));
}

function stopRun(message = null) {
  if (state.autoTimer) {
    clearTimeout(state.autoTimer);
    state.autoTimer = null;
  }
  if (state.videoTimer) {
    clearInterval(state.videoTimer);
    state.videoTimer = null;
  }
  if (state.runStopTimer) {
    clearTimeout(state.runStopTimer);
    state.runStopTimer = null;
  }
  pauseVideoInputs();
  stopAllFramePullLoops();
  $('run-model').classList.remove('active');
  setExecutionStatus('stopping', message || `Stopping after ${state.tickCount} ticks`);
  stopWorkers(false);
}

function forceStopRun() {
  if (state.autoTimer) {
    clearTimeout(state.autoTimer);
    state.autoTimer = null;
  }
  if (state.videoTimer) {
    clearInterval(state.videoTimer);
    state.videoTimer = null;
  }
  if (state.runStopTimer) {
    clearTimeout(state.runStopTimer);
    state.runStopTimer = null;
  }
  pauseVideoInputs();
  stopAllFramePullLoops();
  $('run-model').classList.remove('active');
  setExecutionStatus('stopping', `Force stopping after ${state.tickCount} ticks`);
  stopWorkers(true);
}

async function resetGraphRuntimeState({ stopServer = false } = {}) {
  if (state.autoTimer) {
    clearTimeout(state.autoTimer);
    state.autoTimer = null;
  }
  if (state.videoTimer) {
    clearInterval(state.videoTimer);
    state.videoTimer = null;
  }
  if (state.runStopTimer) {
    clearTimeout(state.runStopTimer);
    state.runStopTimer = null;
  }
  stopAllFramePullLoops();
  stopAllVideoInputs();
  state.nodeViews = {};
  state.graphBuffers = {};
  state.videoInputs = {};
  state.embeddedVideoInputs = {};
  state.videoDirtyNodes.clear();
  state.videoPayloadDirty = false;
  state.runPayloadUpdateInFlight = false;
  state.tickCount = 0;
  state.selectedNode = null;
  state.selectedLink = null;
  state.editingNode = null;
  state.editingCode = null;
  state.ready = false;
  state.readySignature = '';
  state.readyInFlight = false;
  $('run-model')?.classList.remove('active');
  $('ready-model')?.classList.remove('active');
  if (stopServer) await stopWorkers(true);
}

function runForDuration() {
  const seconds = Math.max(0.1, Number($('run-duration').value || 5));
  if (state.runStopTimer) {
    clearTimeout(state.runStopTimer);
    state.runStopTimer = null;
  }
  startServerRun(seconds);
}

async function clearGraph() {
  invalidateReady();
  await resetGraphRuntimeState({ stopServer: true });
  state.nodes = [];
  state.links = [];
  state.selectedNode = null;
  state.selectedLink = null;
  state.projectFileHandle = null;
  state.projectFileName = 'lwrclpy_web_node_project.json';
  state.projectIsSample = false;
  renderAll();
  setExecutionStatus('idle', 'Graph cleared');
}

function selectNode(ev, id) {
  ev.stopPropagation();
  state.selectedNode = id;
  state.selectedLink = null;
  renderSelection();
  renderInspector();
}

function deleteNode(id) {
  stopVideoInput(id);
  delete state.embeddedVideoInputs[id];
  state.nodes = state.nodes.filter((node) => node.id !== id);
  state.links = state.links.filter((link) => link.fromNode !== id && link.toNode !== id);
  if (state.selectedNode === id) state.selectedNode = null;
  renderAll();
  scheduleRun();
}

function deleteLink(id) {
  state.links = state.links.filter((link) => link.id !== id);
  if (state.selectedLink === id) state.selectedLink = null;
  renderAll();
  scheduleRun();
}

function pruneInvalidLinks() {
  state.links = state.links.filter(isValidLink);
}

function renderSelection() {
  document.querySelectorAll('.node').forEach((el) => el.classList.toggle('selected', el.dataset.id === state.selectedNode));
  document.querySelectorAll('path.link').forEach((el) => el.classList.toggle('selected', el.dataset.link === state.selectedLink));
  document.querySelectorAll('.link-label').forEach((el) => el.classList.toggle('selected', el.dataset.link === state.selectedLink));
}

function projectConfig() {
  normalizeSourceTopicNames();
  return {
    format: 'lwrclpy-web-node-editor-project',
    version: 1,
    nodes: state.nodes,
    links: state.links,
    view: state.view,
    nextId: state.nextId,
  };
}

function projectSnapshot() {
  return JSON.stringify({
    format: 'lwrclpy-web-node-editor-project',
    version: 1,
    nodes: state.nodes,
    links: state.links,
    view: state.view,
    nextId: state.nextId,
  });
}

function resetHistory() {
  state.undoStack = [];
  state.redoStack = [];
  state.historySnapshot = projectSnapshot();
}

function commitHistory() {
  if (state.suppressHistory) return;
  const snapshot = projectSnapshot();
  if (!state.historySnapshot) {
    state.historySnapshot = snapshot;
    return;
  }
  if (snapshot === state.historySnapshot) return;
  state.undoStack.push(state.historySnapshot);
  if (state.undoStack.length > 100) state.undoStack.shift();
  state.redoStack = [];
  state.historySnapshot = snapshot;
}

function restoreProjectSnapshot(snapshot) {
  invalidateReady();
  const payload = JSON.parse(snapshot);
  stopAllVideoInputs();
  state.suppressHistory = true;
  state.nodes = (payload.nodes || []).map(normalizeImportedNode);
  state.links = (payload.links || []).map((link) => ({ id: link.id || `l${Date.now()}${Math.random()}`, ...link })).filter(isValidLink);
  state.view = payload.view || { x: 0, y: 0, scale: 1 };
  state.nextId = payload.nextId || Math.max(1, ...state.nodes.map((node) => Number(String(node.id).replace('n', '')) + 1));
  state.selectedNode = null;
  state.selectedLink = null;
  renderAll();
  state.suppressHistory = false;
}

function undoProject() {
  if (!state.undoStack.length) return;
  const current = projectSnapshot();
  const previous = state.undoStack.pop();
  state.redoStack.push(current);
  state.historySnapshot = previous;
  restoreProjectSnapshot(previous);
  setExecutionStatus('idle', 'Undo');
}

function redoProject() {
  if (!state.redoStack.length) return;
  const current = projectSnapshot();
  const next = state.redoStack.pop();
  state.undoStack.push(current);
  state.historySnapshot = next;
  restoreProjectSnapshot(next);
  setExecutionStatus('idle', 'Redo');
}

async function saveProject(saveAs = false) {
  const text = JSON.stringify(projectConfig(), null, 2);
  const forceSaveAs = saveAs || state.projectIsSample;
  if (window.showSaveFilePicker) {
    try {
      if (forceSaveAs || !state.projectFileHandle) {
        state.projectFileHandle = await window.showSaveFilePicker({
          suggestedName: suggestedProjectSaveName(),
          types: [{ description: 'lwrclpy Web Node Editor Project', accept: { 'application/json': ['.json'] } }],
        });
      }
      const writable = await state.projectFileHandle.createWritable();
      await writable.write(text);
      await writable.close();
      state.projectFileName = state.projectFileHandle.name || state.projectFileName;
      state.projectIsSample = false;
      setExecutionStatus('idle', `Saved ${state.projectFileName}`);
      return;
    } catch (err) {
      if (err?.name === 'AbortError') return;
      setExecutionStatus('error', `Save failed: ${err.message}`);
      return;
    }
  }
  const downloadName = suggestedProjectSaveName();
  downloadText(downloadName, text, 'application/json');
  state.projectFileName = downloadName;
  state.projectIsSample = false;
  setExecutionStatus('idle', `Downloaded ${downloadName}`);
}

function suggestedProjectSaveName() {
  const name = String(state.projectFileName || 'lwrclpy_web_node_project.json').split(/[\\/]/).filter(Boolean).pop() || 'lwrclpy_web_node_project.json';
  if (!state.projectIsSample) return name;
  return name.startsWith('copy_') ? name : `copy_${name}`;
}

async function loadProject(event) {
  const file = event.target.files[0];
  event.target.value = '';
  if (!file) return;
  try {
    await applyProjectPayload(JSON.parse(await file.text()), file.name || 'lwrclpy_web_node_project.json', { sample: false });
    setExecutionStatus('idle', `Loaded ${state.projectFileName}`);
  } catch (err) {
    setExecutionStatus('error', `Load failed: ${err.message}`);
  }
}

async function openSampleProjectDialog() {
  try {
    const data = await fetch('/api/sample-projects').then((res) => res.json());
    const samples = Array.isArray(data.samples) ? data.samples : [];
    if (!samples.length) {
      setExecutionStatus('error', 'No sample projects found');
      return;
    }
    let dialog = $('sample-project-dialog');
    if (!dialog) {
      dialog = document.createElement('dialog');
      dialog.id = 'sample-project-dialog';
      dialog.innerHTML = `
        <form method="dialog">
          <header class="dialog-header">
            <h2>Load Sample Project</h2>
            <button type="button" class="icon-button" data-close-sample-dialog>x</button>
          </header>
          <div class="dialog-body">
            <label class="field">
              <span>Sample</span>
              <select id="sample-project-select"></select>
            </label>
            <p id="sample-project-detail" class="hint"></p>
          </div>
          <footer class="dialog-footer">
            <button type="button" data-cancel-sample-dialog>Cancel</button>
            <button type="button" id="sample-project-load">Load</button>
          </footer>
        </form>`;
      document.body.appendChild(dialog);
      dialog.querySelector('[data-close-sample-dialog]').onclick = () => dialog.close();
      dialog.querySelector('[data-cancel-sample-dialog]').onclick = () => dialog.close();
    }
    const select = $('sample-project-select');
    select.innerHTML = samples.map((item) => {
      const category = item.category ? `${item.category}/` : '';
      return `<option value="${escapeAttr(item.path)}">${escapeHtml(category + item.name)}</option>`;
    }).join('');
    const detail = $('sample-project-detail');
    const updateDetail = () => {
      const item = samples.find((sample) => sample.path === select.value);
      detail.textContent = item ? item.path : '';
    };
    select.onchange = updateDetail;
    updateDetail();
    $('sample-project-load').onclick = async () => {
      const selectedPath = select.value;
      dialog.close();
      await loadSampleProject(selectedPath);
    };
    dialog.showModal();
  } catch (err) {
    setExecutionStatus('error', `Sample list failed: ${err.message}`);
  }
}

async function loadSampleProject(path) {
  try {
    const data = await fetch('/api/sample-project', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ path }),
    }).then((res) => res.json());
    if (data.error) throw new Error(data.error);
    await applyProjectPayload(data.project || {}, data.path || path || 'sample_project.json', { sample: true });
    setExecutionStatus('idle', `Loaded sample ${data.path || path}`);
  } catch (err) {
    setExecutionStatus('error', `Sample load failed: ${err.message}`);
  }
}

async function applyProjectPayload(imported, fileName = 'lwrclpy_web_node_project.json', options = {}) {
  await resetGraphRuntimeState({ stopServer: true });
  state.suppressHistory = true;
  state.nodes = (imported.nodes || []).map(normalizeImportedNode);
  state.links = (imported.links || []).map((link) => ({ id: link.id || `l${Date.now()}${Math.random()}`, ...link }));
  state.links = state.links.filter(isValidLink);
  normalizeSourceTopicNames();
  state.view = imported.view || { x: 0, y: 0, scale: 1 };
  state.nextId = imported.nextId || Math.max(1, ...state.nodes.map((node) => Number(String(node.id).replace('n', '')) + 1));
  state.selectedNode = null;
  state.selectedLink = null;
  state.projectFileHandle = null;
  state.projectFileName = String(fileName || 'lwrclpy_web_node_project.json').split(/[\\/]/).filter(Boolean).pop() || 'lwrclpy_web_node_project.json';
  state.projectIsSample = Boolean(options.sample);
  renderAll();
  state.suppressHistory = false;
  resetHistory();
  scheduleRun();
}

async function exportRos2Package() {
  try {
    const response = await fetch('/api/export-ros2-package', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ ...projectConfig(), fileName: state.projectFileName || '' }),
    });
    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try {
        const data = await response.json();
        message = data.error || message;
      } catch {}
      throw new Error(message);
    }
    const blob = await response.blob();
    const disposition = response.headers.get('content-disposition') || '';
    const match = disposition.match(/filename="?([^";]+)"?/i);
    const filename = match ? match[1] : `${projectExportBaseName()}_ros2_package.zip`;
    downloadBlob(filename, blob, 'application/zip');
    setExecutionStatus('idle', `Downloaded ${filename}`);
  } catch (err) {
    setExecutionStatus('error', `ROS 2 export failed: ${err.message}`);
  }
}

async function exportCliPackage() {
  try {
    const response = await fetch('/api/export-cli', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ ...projectConfig(), fileName: state.projectFileName || '' }),
    });
    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try {
        const data = await response.json();
        message = data.error || message;
      } catch {}
      throw new Error(message);
    }
    const blob = await response.blob();
    const disposition = response.headers.get('content-disposition') || '';
    const match = disposition.match(/filename="?([^";]+)"?/i);
    const filename = match ? match[1] : `${projectExportBaseName()}_cli_export.zip`;
    downloadBlob(filename, blob, 'application/zip');
    setExecutionStatus('idle', `Downloaded ${filename}`);
  } catch (err) {
    setExecutionStatus('error', `CLI export failed: ${err.message}`);
  }
}

function exportPythonNode(node) {
  const config = nodePythonConfig(node);
  const python = renderPythonNodeFile(config);
  downloadText(`${safeFileName(node.name)}.py`, python, 'text/x-python');
}

async function importPythonNode(event) {
  const file = event.target.files[0];
  event.target.value = '';
  if (!file) return;
  const text = await file.text();
  const config = extractPythonNodeConfig(text);
  if (!config?.node) {
    alert('This Python file does not contain lwrclpy Web Node Editor metadata.');
    return;
  }
  const node = normalizeImportedNode(config.node);
  node.id = uniqueNodeId(node.id);
  node.x = Math.round(centerWorld().x);
  node.y = Math.round(centerWorld().y);
  state.nodes.push(node);
  state.selectedNode = node.id;
  state.selectedLink = null;
  state.nextId = Math.max(state.nextId, Number(String(node.id).replace('n', '')) + 1 || state.nextId);
  renderAll();
  scheduleRun();
}

function nodePythonConfig(node) {
  normalizeSourceTopicNames();
  const portTopics = { inputs: {}, outputs: {} };
  state.links.forEach((link) => {
    const topic = normalizeTopic(link.name || defaultLinkTopic(link.fromNode, link.fromPort, link.toNode, link.toPort));
    if (link.toNode === node.id) {
      portTopics.inputs[link.toPort] = [...(portTopics.inputs[link.toPort] || []), topic];
    }
    if (link.fromNode === node.id) {
      portTopics.outputs[link.fromPort] = [...(portTopics.outputs[link.fromPort] || []), topic];
    }
  });
  return {
    format: 'lwrclpy-web-node-editor-node',
    version: 1,
    node: structuredClone(node),
    portTopics,
  };
}

function projectPythonConfig() {
  return {
    format: 'lwrclpy-web-node-editor-project-code',
    version: 1,
    nodes: state.nodes.filter((node) => !node.toolType).map((node) => nodePythonConfig(node)),
    skippedNodes: state.nodes.filter((node) => node.toolType).map((node) => ({ id: node.id, name: node.name, toolType: node.toolType })),
  };
}

function projectRos2PackageConfig() {
  const baseName = projectExportBaseName();
  const usedModules = new Set();
  const usedPackages = new Set();
  const nodes = state.nodes.filter((node) => !node.toolType).map((node) => {
    const nodeConfig = nodePythonConfig(node);
    const moduleName = uniqueModuleName(safePythonIdentifier(node.name || node.id), usedModules);
    const packageName = uniqueModuleName(safePackageName(`${baseName}_${moduleName}`), usedPackages);
    return {
      ...nodeConfig,
      moduleName,
      executableName: moduleName,
      packageName,
      dependencies: ros2NodePackageDependencies(nodeConfig),
      requirements: aggregateRequirements([nodeConfig]),
    };
  });
  const launchPackageName = uniqueModuleName(safePackageName(`${baseName}_launch`), usedPackages);
  return {
    format: 'lwrclpy-web-node-editor-ros2-workspace',
    version: 2,
    projectName: baseName,
    packageName: launchPackageName,
    launchPackageName,
    nodes,
    skippedNodes: state.nodes.filter((node) => node.toolType).map((node) => ({ id: node.id, name: node.name, toolType: node.toolType })),
  };
}

function renderRos2PackageFiles(config) {
  const files = [];
  config.nodes.forEach((nodeConfig) => {
    files.push(...renderRos2NodePackageFiles(nodeConfig));
  });
  files.push(...renderRos2LaunchPackageFiles(config));
  files.push({ path: `README.md`, content: renderRos2PackageReadme(config) });
  return files.map((file) => ({
    ...file,
    content: typeof file.content === 'string' ? normalizeGeneratedFile(file.content) : file.content,
  }));
}

function renderRos2NodePackageFiles(nodeConfig) {
  const packageName = nodeConfig.packageName;
  const files = [
    { path: `${packageName}/package.xml`, content: renderRos2NodePackageXml(nodeConfig) },
    { path: `${packageName}/setup.py`, content: renderRos2NodeSetupPy(nodeConfig) },
    { path: `${packageName}/setup.cfg`, content: renderRos2SetupCfg(packageName) },
    { path: `${packageName}/resource/${packageName}`, content: '' },
    { path: `${packageName}/${packageName}/__init__.py`, content: '' },
    { path: `${packageName}/${packageName}/runtime.py`, content: renderRos2RuntimePy() },
    { path: `${packageName}/${packageName}/${nodeConfig.moduleName}.py`, content: renderRos2NodePy(nodeConfig) },
    { path: `${packageName}/README.md`, content: renderRos2NodePackageReadme(nodeConfig) },
  ];
  if (nodeConfig.requirements) files.push({ path: `${packageName}/requirements.txt`, content: nodeConfig.requirements });
  return files;
}

function renderRos2LaunchPackageFiles(config) {
  const packageName = config.launchPackageName;
  return [
    { path: `${packageName}/package.xml`, content: renderRos2LaunchPackageXml(config) },
    { path: `${packageName}/setup.py`, content: renderRos2LaunchSetupPy(config) },
    { path: `${packageName}/setup.cfg`, content: renderRos2SetupCfg(packageName) },
    { path: `${packageName}/resource/${packageName}`, content: '' },
    { path: `${packageName}/${packageName}/__init__.py`, content: '' },
    { path: `${packageName}/launch/${config.projectName}.launch.py`, content: renderRos2LaunchPy(config) },
    { path: `${packageName}/README.md`, content: renderRos2LaunchPackageReadme(config) },
  ];
}

function normalizeGeneratedFile(text) {
  return String(text).trimEnd() + '\n';
}

function pythonJsonLoadExpression(config) {
  const metadata = JSON.stringify(config, null, 2);
  if (!metadata.includes("'''")) return `json.loads(r'''${metadata}''')`;
  if (!metadata.includes('"""')) return `json.loads(r"""${metadata}""")`;
  return `json.loads(${JSON.stringify(metadata)})`;
}

function pythonMultilineString(value) {
  const text = String(value || '');
  if (!text) return "''";
  if (!text.endsWith('\\') && !text.includes("'''")) return `r'''${text}'''`;
  if (!text.endsWith('\\') && !text.includes('"""')) return `r"""${text}"""`;
  return JSON.stringify(text);
}

function configWithoutInlineCode(config) {
  const copy = JSON.parse(JSON.stringify(config));
  if (copy.node) clearNodeInlineCode(copy.node);
  if (Array.isArray(copy.nodes)) {
    copy.nodes.forEach((item) => {
      if (item?.node) clearNodeInlineCode(item.node);
    });
  }
  return copy;
}

function clearNodeInlineCode(node) {
  node.importCode = '';
  node.loopCode = '';
  node.timerCode = '';
  (node.inputs || []).forEach((input) => { input.callbackCode = ''; });
  (node.timers || []).forEach((timer) => { timer.callbackCode = ''; });
}

function nodeInlineCodeAssignments(nodePath, node) {
  const lines = [];
  const add = (path, value) => {
    if (String(value || '')) lines.push(`${path} = ${pythonMultilineString(value)}`);
  };
  add(`${nodePath}["importCode"]`, node.importCode);
  add(`${nodePath}["loopCode"]`, node.loopCode);
  add(`${nodePath}["timerCode"]`, node.timerCode);
  (node.inputs || []).forEach((input, index) => {
    add(`${nodePath}["inputs"][${index}]["callbackCode"]`, input.callbackCode);
  });
  (node.timers || []).forEach((timer, index) => {
    add(`${nodePath}["timers"][${index}]["callbackCode"]`, timer.callbackCode);
  });
  return lines.join('\n');
}

function configInlineCodeAssignments(config) {
  if (config.node) return nodeInlineCodeAssignments('CONFIG["node"]', config.node);
  if (!Array.isArray(config.nodes)) return '';
  return config.nodes
    .map((item, index) => item?.node ? nodeInlineCodeAssignments(`CONFIG["nodes"][${index}]["node"]`, item.node) : '')
    .filter(Boolean)
    .join('\n\n');
}

function renderRos2NodePackageXml(config) {
  const deps = config.dependencies.map((dep) => `  <exec_depend>${escapeXml(dep)}</exec_depend>`).join('\n');
  return `<?xml version="1.0"?>
<package format="3">
  <name>${escapeXml(config.packageName)}</name>
  <version>0.0.0</version>
  <description>ROS 2 rclpy node package exported from Web Node Editor.</description>
  <maintainer email="user@example.com">user</maintainer>
  <license>TODO</license>

  <buildtool_depend>ament_python</buildtool_depend>
${deps}

  <export>
    <build_type>ament_python</build_type>
  </export>
</package>
`;
}

function renderRos2LaunchPackageXml(config) {
  const deps = ['launch', 'launch_ros', ...config.nodes.map((node) => node.packageName)]
    .sort()
    .map((dep) => `  <exec_depend>${escapeXml(dep)}</exec_depend>`)
    .join('\n');
  return `<?xml version="1.0"?>
<package format="3">
  <name>${escapeXml(config.launchPackageName)}</name>
  <version>0.0.0</version>
  <description>Launch package for Web Node Editor project ${escapeXml(config.projectName)}.</description>
  <maintainer email="user@example.com">user</maintainer>
  <license>TODO</license>

  <buildtool_depend>ament_python</buildtool_depend>
${deps}

  <export>
    <build_type>ament_python</build_type>
  </export>
</package>
`;
}

function renderRos2NodeSetupPy(config) {
  return `from setuptools import find_packages, setup

package_name = '${config.packageName}'

setup(
  name=package_name,
  version='0.0.0',
  packages=find_packages(exclude=['test']),
  data_files=[
    ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
    ('share/' + package_name, ['package.xml']),
  ],
  install_requires=['setuptools'],
  zip_safe=True,
  maintainer='user',
  maintainer_email='user@example.com',
  description='ROS 2 rclpy node package exported from Web Node Editor.',
  license='TODO',
  tests_require=['pytest'],
  entry_points={
    'console_scripts': [
      '${config.executableName} = ${config.packageName}.${config.moduleName}:main',
    ],
  },
)
`;
}

function renderRos2LaunchSetupPy(config) {
  return `from glob import glob
from setuptools import find_packages, setup

package_name = '${config.launchPackageName}'

setup(
  name=package_name,
  version='0.0.0',
  packages=find_packages(exclude=['test']),
  data_files=[
    ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
    ('share/' + package_name, ['package.xml']),
    ('share/' + package_name + '/launch', glob('launch/*.launch.py')),
  ],
  install_requires=['setuptools'],
  zip_safe=True,
  maintainer='user',
  maintainer_email='user@example.com',
  description='Launch package exported from Web Node Editor.',
  license='TODO',
  tests_require=['pytest'],
  entry_points={'console_scripts': []},
)
`;
}

function renderRos2SetupCfg(packageName) {
  return `[develop]
script_dir=$base/lib/${packageName}
[install]
install_scripts=$base/lib/${packageName}
`;
}

function renderRos2LaunchPy(config) {
  const nodes = config.nodes.map((node) => `        Node(
            package='${node.packageName}',
            executable='${node.executableName}',
            name='${safeRosName(node.node.name || node.moduleName)}',
            output='screen',
        ),`).join('\n');
  return `from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
${nodes}
    ])
`;
}

function renderRos2NodePy(config) {
  const codeAssignments = configInlineCodeAssignments(config);
  return `#!/usr/bin/env python3
import json

from .runtime import run_node


CONFIG = ${pythonJsonLoadExpression(configWithoutInlineCode(config))}
${codeAssignments ? `\n${codeAssignments}\n` : ''}


def main(args=None):
    run_node(CONFIG, args=args)


if __name__ == '__main__':
    main()
`;
}

function renderRos2RuntimePy() {
  return `import importlib
import keyword
import sys
import time

import rclpy
from rclpy.executors import MultiThreadedExecutor


def import_type_class(type_name):
  package, kind, name = type_name.split('/')
  module = importlib.import_module(f'{package}.{kind}')
  return getattr(module, name)


def split_kind(type_name):
  return type_name.split('/')[1]


class ExportedNode:
  def __init__(self, config):
    self.config = config
    self.node_config = config['node']
    self.port_topics = config.get('portTopics', {'inputs': {}, 'outputs': {}})
    self.state = {}
    self.last_inputs = {}
    self.input_queues = {}
    self.last_outputs = {}
    self.next_timer_at = 0.0
    self.publishers = {}
    self.clients = {}
    self.subscriptions = []
    self.services = []
    self._globals_cache = None
    self.node = rclpy.create_node(self.node_config['name'])
    self._setup_transport()

  def _setup_transport(self):
    for output in self.node_config.get('outputs', []):
      type_cls = import_type_class(output['dataType'])
      for topic in self.port_topics.get('outputs', {}).get(output['id'], []):
        if split_kind(output['dataType']) == 'msg':
          self.publishers.setdefault(output['id'], []).append(self.node.create_publisher(type_cls, topic, 10))
        else:
          self.clients.setdefault(output['id'], []).append(self.node.create_client(type_cls, topic))
    for input_port in self.node_config.get('inputs', []):
      type_cls = import_type_class(input_port['dataType'])
      for topic in self.port_topics.get('inputs', {}).get(input_port['id'], []):
        if split_kind(input_port['dataType']) == 'msg':
          self.subscriptions.append(self.node.create_subscription(type_cls, topic, self._make_subscription_callback(input_port), 10))
        else:
          self.services.append(self.node.create_service(type_cls, topic, self._make_service_callback(input_port)))

  def publish(self, output_id, value):
    self.last_outputs[output_id] = value
    output = self._output_port(output_id)
    if output is None:
      return
    if output_id in self.publishers:
      msg = self._coerce_message(output['dataType'], value)
      for publisher in self.publishers[output_id]:
        publisher.publish(msg)
    if output_id in self.clients:
      request = self._coerce_service_request(output['dataType'], value)
      for client in self.clients[output_id]:
        client.call_async(request)

  def latest(self, input_id, default=None):
    return self.last_inputs.get(input_id, default)

  def take(self, input_id, default=None):
    queue = self.input_queues.get(input_id) or []
    if not queue:
      return default
    return queue.pop(0)

  def has_input(self, input_id):
    return bool(self.input_queues.get(input_id))

  def log(self, *values):
    print(f'[{self.node_config["name"]}]', *values)

  def spin_tick(self):
    outputs = {}
    inputs = dict(self.last_inputs)
    self._execute_timer_if_due(inputs, outputs)
    self._execute_loop(inputs, outputs)
    self._flush_outputs(outputs)

  def _make_subscription_callback(self, input_port):
    def callback(msg):
      self._store_input(input_port['id'], msg)
      if input_port.get('receiveMode', 'callback') != 'callback':
        return
      outputs = {}
      self._execute_callback(input_port, msg, None, outputs)
      self._flush_outputs(outputs)
    return callback

  def _make_service_callback(self, input_port):
    def callback(request, response):
      self._store_input(input_port['id'], request)
      outputs = {}
      if input_port.get('receiveMode', 'callback') == 'callback':
        self._execute_callback(input_port, request, response, outputs)
      self._flush_outputs(outputs)
      return response
    return callback

  def _execute_callback(self, input_port, msg, response, outputs):
    code = input_port.get('callbackCode', '').strip()
    if not code:
      return
    local = self._locals({'input_id': input_port['id'], 'msg': msg, 'request': msg, 'response': response, 'outputs': outputs})
    exec(code, self._globals(), local)

  def _execute_loop(self, inputs, outputs):
    code = self.node_config.get('loopCode', '').strip()
    if not code:
      return
    local = self._locals({'inputs': inputs, 'outputs': outputs, 'now': time.time(), 'latest': self.latest, 'take': self.take, 'has_input': self.has_input})
    exec(code, self._globals(), local)

  def _execute_timer_if_due(self, inputs, outputs):
    if not self.node_config.get('timerEnabled', False):
      return
    code = self.node_config.get('timerCode', '').strip()
    if not code:
      return
    now = time.time()
    period = max(0.001, float(self.node_config.get('timerPeriodSec', 1.0) or 1.0))
    if self.next_timer_at <= 0:
      self.next_timer_at = now
    if now < self.next_timer_at:
      return
    self.next_timer_at = now + period
    local = self._locals({'inputs': inputs, 'outputs': outputs, 'now': now, 'period': period, 'latest': self.latest, 'take': self.take, 'has_input': self.has_input})
    exec(code, self._globals(), local)

  def _flush_outputs(self, outputs):
    for key, value in outputs.items():
      self.last_outputs[key] = value
      self.publish(key, value)

  def _globals(self):
    if self._globals_cache is not None:
      self._sync_param_globals(self._globals_cache)
      return self._globals_cache
    globals_dict = {
      '__builtins__': {
        '__import__': __import__,
        'abs': abs,
        'bool': bool,
        'bytes': bytes,
        'dict': dict,
        'enumerate': enumerate,
        'float': float,
        'getattr': getattr,
        'hasattr': hasattr,
        'int': int,
        'len': len,
        'list': list,
        'max': max,
        'min': min,
        'print': self.log,
        'range': range,
        'round': round,
        'setattr': setattr,
        'str': str,
        'sum': sum,
      }
    }
    self._sync_param_globals(globals_dict)
    import_code = self.node_config.get('importCode', '').strip()
    if import_code:
      exec(import_code, globals_dict, globals_dict)
    self._sync_param_globals(globals_dict)
    self._globals_cache = globals_dict
    return globals_dict

  def _locals(self, extra):
    params = self.node_config.get('params', {})
    return {'node': self.node, 'params': params, 'state': self.state, 'publish': self.publish, 'log': self.log, **self._param_globals(params), **extra}

  def _sync_param_globals(self, globals_dict):
    previous = globals_dict.get('__lwrclpy_param_names__', set())
    if isinstance(previous, set):
      for name in previous:
        globals_dict.pop(name, None)
    param_globals = self._param_globals(self.node_config.get('params', {}))
    globals_dict.update(param_globals)
    globals_dict['__lwrclpy_param_names__'] = set(param_globals)

  def _param_globals(self, params):
    if not isinstance(params, dict):
      return {}
    reserved = {
      'node', 'params', 'state', 'publish', 'log',
      'input_id', 'msg', 'request', 'response',
      'inputs', 'outputs', 'now', 'period',
      'timer_id', 'timer_name', 'latest', 'take', 'has_input',
    }
    return {
      key: value
      for key, value in params.items()
      if isinstance(key, str) and key.isidentifier() and not keyword.iskeyword(key) and key not in reserved
    }

  def _store_input(self, input_id, value):
    self.last_inputs[input_id] = value
    queue = self.input_queues.setdefault(input_id, [])
    queue.append(value)
    limit = 2 if self._input_type(input_id).replace('.', '/') in {'sensor_msgs/msg/Image', 'sensor_msgs/msg/CompressedImage'} else 100
    del queue[:-limit]

  def _input_type(self, input_id):
    for input_port in self.node_config.get('inputs', []):
      if input_port['id'] == input_id:
        return input_port.get('dataType', '')
    return ''

  def _output_port(self, output_id):
    for output in self.node_config.get('outputs', []):
      if output['id'] == output_id:
        return output
    return None

  def _coerce_message(self, data_type, value):
    msg_cls = import_type_class(data_type)
    if hasattr(value, '_fields_and_field_types'):
      return value
    msg = msg_cls()
    self._populate_message(msg, value)
    return msg

  def _populate_message(self, msg, value):
    if isinstance(value, dict):
      for key, item in value.items():
        if hasattr(msg, key):
          setattr(msg, key, item)
    elif hasattr(msg, 'data'):
      msg.data = value

  def _coerce_service_request(self, data_type, value):
    srv_cls = import_type_class(data_type)
    request = srv_cls.Request()
    if hasattr(value, '_fields_and_field_types'):
      return value
    if hasattr(request, 'data'):
      request.data = value
    elif isinstance(value, dict):
      for key, item in value.items():
        if hasattr(request, key):
          setattr(request, key, item)
    return request


def run_node(config, args=None):
  rclpy.init(args=args)
  exported = ExportedNode(config)
  executor = MultiThreadedExecutor()
  executor.add_node(exported.node)
  try:
    while rclpy.ok():
      executor.spin_once(timeout_sec=0.05)
      exported.spin_tick()
  finally:
    executor.remove_node(exported.node)
    exported.node.destroy_node()
    rclpy.shutdown()
`;
}

function renderRos2PackageReadme(config) {
  const packages = [config.launchPackageName, ...config.nodes.map((node) => node.packageName)];
  const nodes = config.nodes.map((node) => `- \`${node.packageName}\`: executable \`${node.executableName}\`, ROS node \`${node.node.name}\``).join('\n');
  const skipped = config.skippedNodes.length
    ? `\n\nThe following built-in/browser tool nodes were not exported:\n${config.skippedNodes.map((node) => `- ${node.name} (${node.toolType})`).join('\n')}\n`
    : '';
  return `# ${config.projectName} ROS 2 export

This export contains one ROS 2 package per custom node plus one launch package.
Built-in nodes are not exported as executables.

## Packages

${packages.map((name) => `- \`${name}\``).join('\n')}

## Nodes

${nodes}
${skipped}
## Build and run

Copy every package directory from this archive into a ROS 2 workspace ` + '`src`' + ` directory, then run:

` + '```bash' + `
colcon build --packages-select ${packages.join(' ')}
source install/setup.bash
ros2 launch ${config.launchPackageName} ${config.projectName}.launch.py
` + '```' + `

If a node package contains a ` + '`requirements.txt`' + `, install those Python packages in the same ROS 2 environment before launching.
`;
}

function renderRos2NodePackageReadme(config) {
  return `# ${config.packageName}

ROS 2 Python package for custom node \`${config.node.name}\`.

## Run

` + '```bash' + `
ros2 run ${config.packageName} ${config.executableName}
` + '```' + `
`;
}

function renderRos2LaunchPackageReadme(config) {
  return `# ${config.launchPackageName}

Launch package for project \`${config.projectName}\`.

## Run

` + '```bash' + `
ros2 launch ${config.launchPackageName} ${config.projectName}.launch.py
` + '```' + `
`;
}

function ros2NodePackageDependencies(item) {
  const deps = new Set(['rclpy']);
  const node = item.node || {};
  [...(node.inputs || []), ...(node.outputs || [])].forEach((port) => {
    const packageName = String(port.dataType || '').split('/')[0];
    if (packageName) deps.add(packageName);
  });
  return [...deps].sort();
}

function aggregateRequirements(nodes) {
  const lines = new Set();
  nodes.forEach((item) => {
    String(item.node?.requirements || '').split(/\r?\n/).map((line) => line.trim()).filter(Boolean).forEach((line) => lines.add(line));
  });
  return [...lines].join('\n') + (lines.size ? '\n' : '');
}

function projectExportBaseName() {
  const fromFile = String(state.projectFileName || '').replace(/\.[^.]+$/, '');
  return safePackageName(projectConfig().name || fromFile || 'lwrclpy_exported_project');
}

function safePackageName(value) {
  let name = String(value || 'rclpy_exported_nodes').toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '');
  if (!name || /^[0-9]/.test(name)) name = `ros2_${name || 'exported_nodes'}`;
  return name;
}

function safePythonIdentifier(value) {
  let name = String(value || 'node').toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '');
  if (!name || /^[0-9]/.test(name)) name = `node_${name || 'exported'}`;
  return name;
}

function safeRosName(value) {
  return safePythonIdentifier(value).replace(/_+/g, '_');
}

function uniqueModuleName(base, used) {
  const cleanBase = base || 'node';
  let name = cleanBase;
  let index = 2;
  while (used.has(name)) {
    name = `${cleanBase}_${index++}`;
  }
  used.add(name);
  return name;
}

function escapeXml(value) {
  return String(value).replace(/[<>&"']/g, (ch) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&apos;' }[ch]));
}

function renderProjectPythonFile(config) {
  const codeAssignments = configInlineCodeAssignments(config);
  return `#!/usr/bin/env python3
# Generated by Web Node Editor for standard ROS 2 rclpy.
# This file runs exported custom ROS 2 nodes from one saved project.

import importlib
import hashlib
import json
import keyword
import shutil
import subprocess
import sys
import time
from pathlib import Path

import rclpy
from rclpy.executors import MultiThreadedExecutor


CONFIG = ${pythonJsonLoadExpression(configWithoutInlineCode(config))}
${codeAssignments ? `\n${codeAssignments}\n` : ''}


def import_type_class(type_name):
    package, kind, name = type_name.split("/")
    module = importlib.import_module(f"{package}.{kind}")
    return getattr(module, name)


def split_kind(type_name):
    return type_name.split("/")[1]


class ProjectNode:
    def __init__(self, config):
        self.config = config
        self.node_config = config["node"]
        self.port_topics = config.get("portTopics", {"inputs": {}, "outputs": {}})
        self.state = {}
        self.last_inputs = {}
        self.input_queues = {}
        self.last_outputs = {}
        self.next_timer_at = 0.0
        self.publishers = {}
        self.clients = {}
        self.subscriptions = []
        self.services = []
        self.env_path = None
        self.env_site_packages = None
        self.node = rclpy.create_node(self.node_config["name"])
        self._setup_environment()
        self._setup_transport()

    def _setup_environment(self):
        uv = shutil.which("uv")
        if not uv:
            sibling = Path(sys.executable).parent / ("uv.exe" if sys.platform.startswith("win") else "uv")
            uv = str(sibling) if sibling.exists() else None
        if not uv:
            raise RuntimeError("uv command not found")
        env_root = Path.cwd() / ".node_envs" / self.node_config["id"]
        req_text = (self.node_config.get("requirements") or "").strip() + "\\n"
        req_hash = hashlib.sha256(req_text.encode("utf-8")).hexdigest()
        hash_file = env_root / ".requirements.sha256"
        req_file = env_root / "requirements.txt"
        python_bin = env_root / ("Scripts/python.exe" if sys.platform.startswith("win") else "bin/python")
        env_root.mkdir(parents=True, exist_ok=True)
        if not python_bin.exists():
            subprocess.run([uv, "venv", str(env_root)], check=True)
        req_file.write_text(req_text, encoding="utf-8")
        current_hash = hash_file.read_text(encoding="utf-8") if hash_file.exists() else ""
        if current_hash != req_hash:
            if req_text.strip():
                subprocess.run([uv, "pip", "install", "--python", str(python_bin), "-r", str(req_file)], check=True)
            hash_file.write_text(req_hash, encoding="utf-8")
        self.env_path = env_root
        self.env_site_packages = self._site_packages_for(env_root)

    def _site_packages_for(self, env_root):
        if sys.platform.startswith("win"):
            return env_root / "Lib" / "site-packages"
        for item in (env_root / "lib").iterdir():
            candidate = item / "site-packages"
            if candidate.exists():
                return candidate
        return None

    def _setup_transport(self):
        for output in self.node_config.get("outputs", []):
            type_cls = import_type_class(output["dataType"])
            for topic in self.port_topics.get("outputs", {}).get(output["id"], []):
                if split_kind(output["dataType"]) == "msg":
                    self.publishers.setdefault(output["id"], []).append(self.node.create_publisher(type_cls, topic, 10))
                else:
                    self.clients.setdefault(output["id"], []).append(self.node.create_client(type_cls, topic))
        for input_port in self.node_config.get("inputs", []):
            type_cls = import_type_class(input_port["dataType"])
            for topic in self.port_topics.get("inputs", {}).get(input_port["id"], []):
                if split_kind(input_port["dataType"]) == "msg":
                    self.subscriptions.append(self.node.create_subscription(type_cls, topic, self._make_subscription_callback(input_port), 10))
                else:
                    self.services.append(self.node.create_service(type_cls, topic, self._make_service_callback(input_port)))

    def publish(self, output_id, value):
        output = self._output_port(output_id)
        if output is None:
            return
        if output_id in self.publishers:
            msg = self._coerce_message(output["dataType"], value)
            for publisher in self.publishers[output_id]:
                publisher.publish(msg)
        if output_id in self.clients:
            request = self._coerce_service_request(output["dataType"], value)
            for client in self.clients[output_id]:
                client.call_async(request)

    def latest(self, input_id, default=None):
        return self.last_inputs.get(input_id, default)

    def take(self, input_id, default=None):
        queue = self.input_queues.get(input_id) or []
        if not queue:
            return default
        return queue.pop(0)

    def has_input(self, input_id):
        return bool(self.input_queues.get(input_id))

    def log(self, *values):
        print(f"[{self.node_config['name']}]", *values)

    def spin_tick(self):
        outputs = {}
        inputs = dict(self.last_inputs)
        self._execute_timer_if_due(inputs, outputs)
        self._execute_loop(inputs, outputs)
        self._flush_outputs(outputs)

    def _make_subscription_callback(self, input_port):
        def callback(msg):
            self._store_input(input_port["id"], msg)
            if input_port.get("receiveMode", "callback") != "callback":
                return
            outputs = {}
            self._execute_callback(input_port, msg, None, outputs)
            self._flush_outputs(outputs)
        return callback

    def _make_service_callback(self, input_port):
        def callback(request, response):
            self._store_input(input_port["id"], request)
            outputs = {}
            if input_port.get("receiveMode", "callback") == "callback":
                self._execute_callback(input_port, request, response, outputs)
            self._flush_outputs(outputs)
            return response
        return callback

    def _execute_callback(self, input_port, msg, response, outputs):
        code = input_port.get("callbackCode", "").strip()
        if not code:
            return
        local = self._locals({"input_id": input_port["id"], "msg": msg, "request": msg, "response": response, "outputs": outputs})
        exec(code, self._globals(), local)

    def _execute_loop(self, inputs, outputs):
        code = self.node_config.get("loopCode", "").strip()
        if not code:
            return
        local = self._locals({"inputs": inputs, "outputs": outputs, "now": time.time(), "latest": self.latest, "take": self.take, "has_input": self.has_input})
        exec(code, self._globals(), local)

    def _execute_timer_if_due(self, inputs, outputs):
        if not self.node_config.get("timerEnabled", False):
            return
        code = self.node_config.get("timerCode", "").strip()
        if not code:
            return
        now = time.time()
        period = max(0.001, float(self.node_config.get("timerPeriodSec", 1.0) or 1.0))
        if self.next_timer_at <= 0:
            self.next_timer_at = now
        if now < self.next_timer_at:
            return
        self.next_timer_at = now + period
        local = self._locals({"inputs": inputs, "outputs": outputs, "now": now, "period": period, "latest": self.latest, "take": self.take, "has_input": self.has_input})
        exec(code, self._globals(), local)

    def _flush_outputs(self, outputs):
        for key, value in outputs.items():
            self.last_outputs[key] = value
            self.publish(key, value)

    def _globals(self):
        globals_dict = {"__builtins__": {"__import__": __import__, "abs": abs, "bool": bool, "dict": dict, "enumerate": enumerate, "float": float, "getattr": getattr, "hasattr": hasattr, "int": int, "len": len, "list": list, "max": max, "min": min, "print": self.log, "range": range, "round": round, "setattr": setattr, "str": str, "sum": sum}}
        self._sync_param_globals(globals_dict)
        import_code = self.node_config.get("importCode", "").strip()
        if import_code:
            original_path = list(sys.path)
            try:
                if self.env_site_packages:
                    sys.path.insert(0, str(self.env_site_packages))
                exec(import_code, globals_dict, globals_dict)
            finally:
                sys.path[:] = original_path
        self._sync_param_globals(globals_dict)
        return globals_dict

    def _locals(self, extra):
        params = self.node_config.get("params", {})
        return {"params": params, "state": self.state, "publish": self.publish, "log": self.log, **self._param_globals(params), **extra}

    def _sync_param_globals(self, globals_dict):
        previous = globals_dict.get("__lwrclpy_param_names__", set())
        if isinstance(previous, set):
            for name in previous:
                globals_dict.pop(name, None)
        param_globals = self._param_globals(self.node_config.get("params", {}))
        globals_dict.update(param_globals)
        globals_dict["__lwrclpy_param_names__"] = set(param_globals)

    def _param_globals(self, params):
        if not isinstance(params, dict):
            return {}
        reserved = {
            "node", "params", "state", "publish", "log",
            "input_id", "msg", "request", "response",
            "inputs", "outputs", "now", "period",
            "timer_id", "timer_name", "latest", "take", "has_input",
        }
        return {
            key: value
            for key, value in params.items()
            if isinstance(key, str) and key.isidentifier() and not keyword.iskeyword(key) and key not in reserved
        }

    def _store_input(self, input_id, value):
        self.last_inputs[input_id] = value
        queue = self.input_queues.setdefault(input_id, [])
        queue.append(value)
        limit = 2 if self._input_type(input_id).replace('.', '/') in {'sensor_msgs/msg/Image', 'sensor_msgs/msg/CompressedImage'} else 100
        del queue[:-limit]

    def _input_type(self, input_id):
        for input_port in self.node_config.get('inputs', []):
            if input_port['id'] == input_id:
                return input_port.get('dataType', '')
        return ''

    def _output_port(self, output_id):
        for output in self.node_config.get("outputs", []):
            if output["id"] == output_id:
                return output
        return None

    def _coerce_message(self, data_type, value):
        msg_cls = import_type_class(data_type)
        if hasattr(value, "_fields_and_field_types"):
            return value
        msg = msg_cls()
        self._populate_message(msg, value)
        return msg

    def _populate_message(self, msg, value):
        if isinstance(value, dict):
            for key, item in value.items():
                if hasattr(msg, key):
                    setattr(msg, key, item)
        elif hasattr(msg, "data"):
            msg.data = value

    def _coerce_service_request(self, data_type, value):
        srv_cls = import_type_class(data_type)
        request = srv_cls.Request()
        if hasattr(value, "_fields_and_field_types"):
            return value
        if hasattr(request, "data"):
            request.data = value
        elif isinstance(value, dict):
            for key, item in value.items():
                if hasattr(request, key):
                    setattr(request, key, item)
        return request


def main():
    rclpy.init(args=None)
    nodes = [ProjectNode(item) for item in CONFIG.get("nodes", [])]
    executor = MultiThreadedExecutor()
    for item in nodes:
        executor.add_node(item.node)
    try:
        while rclpy.ok():
            executor.spin_once(timeout_sec=0.05)
            for item in nodes:
                item.spin_tick()
    finally:
        for item in nodes:
            executor.remove_node(item.node)
            item.node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
`;
}

function renderProjectLaunchFile() {
  return `#!/usr/bin/env python3
from launch import LaunchDescription
from launch.actions import ExecuteProcess
from launch.substitutions import PathJoinSubstitution, ThisLaunchFileDir


def generate_launch_description():
    # Put rclpy_project.py next to this launch file or edit this path.
    project_script = PathJoinSubstitution([
        ThisLaunchFileDir(),
        'rclpy_project.py',
    ])
    return LaunchDescription([
        ExecuteProcess(
            cmd=['python3', project_script],
            output='screen',
        )
    ])
`;
}

function renderPythonNodeFile(config) {
  const metadata = JSON.stringify(config, null, 2);
  const codeAssignments = configInlineCodeAssignments(config);
  return `#!/usr/bin/env python3
# Generated by Web Node Editor for standard ROS 2 rclpy.
# LWRCLPY_WEB_NODE_EDITOR_CONFIG_START
${metadata.split('\n').map((line) => `# ${line}`).join('\n')}
# LWRCLPY_WEB_NODE_EDITOR_CONFIG_END

import importlib
import json
import keyword
import time

import rclpy
from rclpy.executors import MultiThreadedExecutor


CONFIG = ${pythonJsonLoadExpression(configWithoutInlineCode(config))}
${codeAssignments ? `\n${codeAssignments}\n` : ''}


def import_type_class(type_name):
    package, kind, name = type_name.split("/")
    module = importlib.import_module(f"{package}.{kind}")
    return getattr(module, name)


def split_kind(type_name):
    return type_name.split("/")[1]


class ExportedNode:
    def __init__(self, config):
        self.config = config
        self.node_config = config["node"]
        self.port_topics = config.get("portTopics", {"inputs": {}, "outputs": {}})
        self.state = {}
        self.last_inputs = {}
        self.input_queues = {}
        self.last_outputs = {}
        self.next_timer_at = 0.0
        self.publishers = {}
        self.clients = {}
        self.subscriptions = []
        self.services = []
        self.node = rclpy.create_node(self.node_config["name"])
        self._setup_transport()

    def _setup_transport(self):
        for output in self.node_config.get("outputs", []):
            type_cls = import_type_class(output["dataType"])
            for topic in self.port_topics.get("outputs", {}).get(output["id"], []):
                if split_kind(output["dataType"]) == "msg":
                    self.publishers.setdefault(output["id"], []).append(self.node.create_publisher(type_cls, topic, 10))
                else:
                    self.clients.setdefault(output["id"], []).append(self.node.create_client(type_cls, topic))
        for input_port in self.node_config.get("inputs", []):
            type_cls = import_type_class(input_port["dataType"])
            for topic in self.port_topics.get("inputs", {}).get(input_port["id"], []):
                if split_kind(input_port["dataType"]) == "msg":
                    self.subscriptions.append(self.node.create_subscription(type_cls, topic, self._make_subscription_callback(input_port), 10))
                else:
                    self.services.append(self.node.create_service(type_cls, topic, self._make_service_callback(input_port)))

    def publish(self, output_id, value):
        output = self._output_port(output_id)
        if output is None:
            return
        if output_id in self.publishers:
            msg = self._coerce_message(output["dataType"], value)
            for publisher in self.publishers[output_id]:
                publisher.publish(msg)
        if output_id in self.clients:
            request = self._coerce_service_request(output["dataType"], value)
            for client in self.clients[output_id]:
                client.call_async(request)

    def latest(self, input_id, default=None):
        return self.last_inputs.get(input_id, default)

    def take(self, input_id, default=None):
        queue = self.input_queues.get(input_id) or []
        if not queue:
            return default
        return queue.pop(0)

    def has_input(self, input_id):
        return bool(self.input_queues.get(input_id))

    def log(self, *values):
        print(*values)

    def spin_tick(self):
        outputs = {}
        inputs = dict(self.last_inputs)
        self._execute_timer_if_due(inputs, outputs)
        self._execute_loop(inputs, outputs)
        self._flush_outputs(outputs)

    def _make_subscription_callback(self, input_port):
        def callback(msg):
            self._store_input(input_port["id"], msg)
            if input_port.get("receiveMode", "callback") != "callback":
                return
            outputs = {}
            self._execute_callback(input_port, msg, None, outputs)
            self._flush_outputs(outputs)
        return callback

    def _make_service_callback(self, input_port):
        def callback(request, response):
            self._store_input(input_port["id"], request)
            outputs = {}
            if input_port.get("receiveMode", "callback") == "callback":
                self._execute_callback(input_port, request, response, outputs)
            self._flush_outputs(outputs)
            return response
        return callback

    def _execute_callback(self, input_port, msg, response, outputs):
        code = input_port.get("callbackCode", "").strip()
        if not code:
            return
        local = self._locals({
            "input_id": input_port["id"],
            "msg": msg,
            "request": msg,
            "response": response,
            "outputs": outputs,
        })
        exec(code, self._globals(), local)

    def _execute_loop(self, inputs, outputs):
        code = self.node_config.get("loopCode", "").strip()
        if not code:
            return
        local = self._locals({
            "inputs": inputs,
            "outputs": outputs,
            "now": time.time(),
            "latest": self.latest,
            "take": self.take,
            "has_input": self.has_input,
        })
        exec(code, self._globals(), local)

    def _execute_timer_if_due(self, inputs, outputs):
        if not self.node_config.get("timerEnabled", False):
            return
        code = self.node_config.get("timerCode", "").strip()
        if not code:
            return
        now = time.time()
        period = max(0.001, float(self.node_config.get("timerPeriodSec", 1.0) or 1.0))
        if self.next_timer_at <= 0:
            self.next_timer_at = now
        if now < self.next_timer_at:
            return
        self.next_timer_at = now + period
        local = self._locals({
            "inputs": inputs,
            "outputs": outputs,
            "now": now,
            "period": period,
            "latest": self.latest,
            "take": self.take,
            "has_input": self.has_input,
        })
        exec(code, self._globals(), local)

    def _flush_outputs(self, outputs):
        for key, value in outputs.items():
            self.last_outputs[key] = value
            self.publish(key, value)

    def _globals(self):
        globals_dict = {
            "__builtins__": {
                "abs": abs,
                "bool": bool,
                "dict": dict,
                "enumerate": enumerate,
                "float": float,
                "getattr": getattr,
                "hasattr": hasattr,
                "int": int,
                "len": len,
                "list": list,
                "max": max,
                "min": min,
                "print": self.log,
                "range": range,
                "round": round,
                "setattr": setattr,
                "str": str,
                "sum": sum,
            }
        }
        self._sync_param_globals(globals_dict)
        return globals_dict

    def _locals(self, extra):
        params = self.node_config.get("params", {})
        return {
            "params": params,
            "state": self.state,
            "publish": self.publish,
            "log": self.log,
            **self._param_globals(params),
            **extra,
        }

    def _sync_param_globals(self, globals_dict):
        previous = globals_dict.get("__lwrclpy_param_names__", set())
        if isinstance(previous, set):
            for name in previous:
                globals_dict.pop(name, None)
        param_globals = self._param_globals(self.node_config.get("params", {}))
        globals_dict.update(param_globals)
        globals_dict["__lwrclpy_param_names__"] = set(param_globals)

    def _param_globals(self, params):
        if not isinstance(params, dict):
            return {}
        reserved = {
            "node", "params", "state", "publish", "log",
            "input_id", "msg", "request", "response",
            "inputs", "outputs", "now", "period",
            "timer_id", "timer_name", "latest", "take", "has_input",
        }
        return {
            key: value
            for key, value in params.items()
            if isinstance(key, str) and key.isidentifier() and not keyword.iskeyword(key) and key not in reserved
        }

    def _store_input(self, input_id, value):
        self.last_inputs[input_id] = value
        queue = self.input_queues.setdefault(input_id, [])
        queue.append(value)
        limit = 2 if self._input_type(input_id).replace('.', '/') in {'sensor_msgs/msg/Image', 'sensor_msgs/msg/CompressedImage'} else 100
        del queue[:-limit]

    def _input_type(self, input_id):
        for input_port in self.node_config.get('inputs', []):
            if input_port['id'] == input_id:
                return input_port.get('dataType', '')
        return ''

    def _output_port(self, output_id):
        for output in self.node_config.get("outputs", []):
            if output["id"] == output_id:
                return output
        return None

    def _coerce_message(self, data_type, value):
        msg_cls = import_type_class(data_type)
        if hasattr(value, "_fields_and_field_types"):
            return value
        msg = msg_cls()
        self._populate_message(msg, value)
        return msg

    def _populate_message(self, msg, value):
        if isinstance(value, dict):
            for key, item in value.items():
                if hasattr(msg, key):
                    setattr(msg, key, item)
        elif hasattr(msg, "data"):
            msg.data = value

    def _coerce_service_request(self, data_type, value):
        srv_cls = import_type_class(data_type)
        request = srv_cls.Request()
        if hasattr(value, "_fields_and_field_types"):
            return value
        if hasattr(request, "data"):
            request.data = value
        elif isinstance(value, dict):
            for key, item in value.items():
                if hasattr(request, key):
                    setattr(request, key, item)
        return request


def main():
    rclpy.init(args=None)
    exported = ExportedNode(CONFIG)
    executor = MultiThreadedExecutor()
    executor.add_node(exported.node)
    try:
        while rclpy.ok():
            executor.spin_once(timeout_sec=0.05)
            exported.spin_tick()
    finally:
        executor.remove_node(exported.node)
        exported.node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
`;
}

function extractPythonNodeConfig(text) {
  const match = text.match(/LWRCLPY_WEB_NODE_EDITOR_CONFIG_START\n([\s\S]*?)\n# LWRCLPY_WEB_NODE_EDITOR_CONFIG_END/);
  if (!match) return null;
  const jsonText = match[1].split('\n').map((line) => line.replace(/^# ?/, '')).join('\n');
  try {
    return JSON.parse(jsonText);
  } catch {
    return null;
  }
}

function normalizeImportedNode(node) {
  const isTool = Boolean(node.toolType);
  const toolInputType = (port, fallback) => {
    if (node.toolType === 'graph_view' && !port.dataType) return 'std_msgs/msg/Float32';
    if (node.toolType === 'mcap_record' && !port.dataType) return '';
    return port.dataType ?? fallback;
  };
  const normalized = {
    id: node.id || `n${state.nextId++}`,
    name: node.name || 'imported_lwrclpy_node',
    x: Number(node.x || 0),
    y: Number(node.y || 0),
    width: Math.max(DEFAULT_NODE_WIDTH, Math.round(Number(node.width || DEFAULT_NODE_WIDTH))),
    height: Math.max(DEFAULT_NODE_MIN_HEIGHT, Math.round(Number(node.height || DEFAULT_NODE_MIN_HEIGHT))),
    inputs: (node.inputs || []).map((port, index) => ({
      id: port.id || `in${index + 1}`,
      name: port.name || `in${index + 1}`,
      dataType: toolInputType(port, firstDataType()),
      receiveMode: port.receiveMode || 'callback',
      callbackCode: port.callbackCode || '',
    })),
    outputs: (node.outputs || []).map((port, index) => ({
      id: port.id || `out${index + 1}`,
      name: port.name || `out${index + 1}`,
      dataType: port.dataType ?? firstDataType(),
    })),
    loopCode: node.loopCode || '',
    timers: isTool ? [] : normalizeTimers(node),
    timerEnabled: Boolean(node.timerEnabled),
    timerPeriodSec: Number(node.timerPeriodSec || 1.0),
    timerCode: Object.prototype.hasOwnProperty.call(node, 'timerCode') ? node.timerCode : DEFAULT_TIMER_CODE,
    importCode: node.importCode || DEFAULT_IMPORT_CODE,
    requirements: node.requirements || '',
    pythonVersion: isTool ? '' : (node.pythonVersion || defaultPythonVersion()),
    lwrclpyVersion: isTool ? '' : (node.lwrclpyVersion || defaultLwrclpyVersion()),
    toolType: node.toolType || '',
    params: node.params || {},
    customNodeMeta: node.customNodeMeta || null,
  };
  if (normalized.toolType === 'video_file_input') {
    const outputType = normalized.params.outputType || normalized.outputs?.[0]?.dataType;
    const videoType = outputType === VIDEO_COMPRESSED_IMAGE_TYPE ? VIDEO_COMPRESSED_IMAGE_TYPE : VIDEO_RAW_IMAGE_TYPE;
    if (normalized.outputs?.[0]) {
      normalized.outputs[0].name = portDisplayName(normalized.outputs[0].name || 'frame');
      normalized.outputs[0].dataType = videoType;
    }
    normalized.params = { ...(normalized.params || {}), outputType: videoType };
  }
  if (!normalized.toolType) {
    normalized.params = {
      ...(normalized.params || {}),
      tfInputEnabled: Boolean(normalized.params?.tfInputEnabled),
      tfOutputEnabled: Boolean(normalized.params?.tfOutputEnabled),
    };
    applyCustomTfPorts(normalized);
  }
  if (normalized.toolType === 'mcap_record') {
    const count = Math.max(1, Math.min(64, Math.floor(Number(normalized.params.topicCount || normalized.inputs.length || 1))));
    const splitSizeMb = Math.max(0, Number(normalized.params.splitSizeMb || 0));
    normalized.params = { ...(normalized.params || {}), topicCount: count, splitSizeMb: Number.isFinite(splitSizeMb) ? splitSizeMb : 0 };
    while (normalized.inputs.length < count) {
      const index = normalized.inputs.length;
      normalized.inputs.push({ id: `in${index + 1}`, name: `topic${index + 1}`, dataType: '', receiveMode: 'manual', callbackCode: '' });
    }
    normalized.inputs = normalized.inputs.slice(0, count).map((port, index) => ({
      ...port,
      id: port.id || `in${index + 1}`,
      name: port.name || `topic${index + 1}`,
      dataType: port.dataType || '',
      receiveMode: 'manual',
      callbackCode: '',
    }));
  }
  if (normalized.toolType === 'urdf_static_tf_publisher') {
    normalized.inputs = [];
    normalized.outputs = [{ id: 'tf_static', name: 'TF', dataType: 'tf2_msgs/msg/TFMessage' }];
    normalized.params = { ...(normalized.params || {}), urdfPath: String(normalized.params.urdfPath || ''), fileName: String(normalized.params.fileName || '') };
  }
  if (normalized.toolType === 'tf_merge') {
    const count = Math.max(1, Math.min(64, Math.floor(Number(normalized.params.topicCount || normalized.inputs.length || 2))));
    normalized.params = { ...(normalized.params || {}), topicCount: count };
    while (normalized.inputs.length < count) {
      const index = normalized.inputs.length;
      normalized.inputs.push({ id: `in${index + 1}`, name: 'TF', dataType: 'tf2_msgs/msg/TFMessage', receiveMode: 'manual', callbackCode: '' });
    }
    normalized.inputs = normalized.inputs.slice(0, count).map((port, index) => ({
      ...port,
      id: port.id || `in${index + 1}`,
      name: port.name || 'TF',
      dataType: 'tf2_msgs/msg/TFMessage',
      receiveMode: 'manual',
      callbackCode: '',
    }));
    normalized.outputs = [
      { id: 'tf', name: 'TF', dataType: 'tf2_msgs/msg/TFMessage' },
    ];
  }
  if (normalized.toolType === 'tf_viewer' || normalized.toolType === '3d_viewer') {
    if (normalized.toolType === 'tf_viewer') normalized.toolType = '3d_viewer';
    normalized.params = { ...(normalized.params || {}), ...tfViewerDefaults(normalized.params || {}) };
    apply3dViewerPorts(normalized, normalized.params, false);
  }
  if (normalized.toolType === 'interactive_text_input') {
    const messages = Array.isArray(normalized.params.messages) ? normalized.params.messages.filter((item) => item && typeof item === 'object') : [];
    const promptHistory = Array.isArray(normalized.params.promptHistory) ? normalized.params.promptHistory.map((item) => String(item || '')).filter(Boolean) : [];
    normalized.inputs = [];
    normalized.outputs = [{ id: 'out1', name: 'text', dataType: 'std_msgs/msg/String' }];
    normalized.params = {
      ...(normalized.params || {}),
      draft: String(normalized.params.draft || ''),
      messages,
      promptHistory,
      historyCursor: Number.isFinite(Number(normalized.params.historyCursor)) ? Number(normalized.params.historyCursor) : -1,
      nextSeq: Math.max(1, Math.floor(Number(normalized.params.nextSeq || 1))),
      maxMessages: Math.max(1, Math.min(1000, Math.floor(Number(normalized.params.maxMessages || 100)))),
    };
  }
  if (normalized.toolType === 'chat_string_view') {
    normalized.inputs = [{ id: 'in1', name: 'text', dataType: 'std_msgs/msg/String', receiveMode: 'manual', callbackCode: '' }];
    normalized.outputs = [];
    normalized.params = {
      ...(normalized.params || {}),
      maxMessages: Math.max(1, Math.min(1000, Math.floor(Number(normalized.params.maxMessages || 100)))),
      maxChars: Math.max(1, Math.floor(Number(normalized.params.maxChars || 20000))),
    };
  }
  return normalized;
}

function uniqueNodeId(preferred) {
  let id = preferred || `n${state.nextId++}`;
  if (!state.nodes.some((node) => node.id === id)) return id;
  do {
    id = `n${state.nextId++}`;
  } while (state.nodes.some((node) => node.id === id));
  return id;
}

function normalizeTopic(name) {
  let topic = String(name || '').trim();
  if (!topic) return '/topic';
  topic = topic.replace(/^\/+/, '');
  return `/${topic}`;
}

function portDisplayName(name) {
  return String(name || '').trim().replace(/^\/+/, '') || 'topic';
}

function safeFileName(value) {
  return String(value || 'lwrclpy_node').trim().replace(/[^A-Za-z0-9_.-]+/g, '_').replace(/^_+|_+$/g, '') || 'lwrclpy_node';
}

function downloadText(filename, text, type) {
  const blob = new Blob([text], { type });
  downloadBlob(filename, blob, type);
}

function downloadBlob(filename, blob, type) {
  const payload = blob instanceof Blob ? blob : new Blob([blob], { type });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(payload);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

function makeZip(files) {
  const encoder = new TextEncoder();
  const localParts = [];
  const centralParts = [];
  let offset = 0;
  const now = new Date();
  const dosTime = ((now.getHours() & 31) << 11) | ((now.getMinutes() & 63) << 5) | ((Math.floor(now.getSeconds() / 2)) & 31);
  const dosDate = (((now.getFullYear() - 1980) & 127) << 9) | (((now.getMonth() + 1) & 15) << 5) | (now.getDate() & 31);
  files.forEach((file) => {
    const nameBytes = encoder.encode(file.path);
    const data = typeof file.content === 'string' ? encoder.encode(file.content) : file.content;
    const crc = crc32(data);
    const localHeader = concatBytes([
      u32(0x04034b50), u16(20), u16(0), u16(0), u16(dosTime), u16(dosDate),
      u32(crc), u32(data.length), u32(data.length), u16(nameBytes.length), u16(0), nameBytes,
    ]);
    localParts.push(localHeader, data);
    centralParts.push(concatBytes([
      u32(0x02014b50), u16(20), u16(20), u16(0), u16(0), u16(dosTime), u16(dosDate),
      u32(crc), u32(data.length), u32(data.length), u16(nameBytes.length), u16(0), u16(0),
      u16(0), u16(0), u32(0), u32(offset), nameBytes,
    ]));
    offset += localHeader.length + data.length;
  });
  const centralSize = centralParts.reduce((sum, part) => sum + part.length, 0);
  const end = concatBytes([
    u32(0x06054b50), u16(0), u16(0), u16(files.length), u16(files.length), u32(centralSize), u32(offset), u16(0),
  ]);
  return new Blob([...localParts, ...centralParts, end], { type: 'application/zip' });
}

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let i = 0; i < 8; i += 1) {
      crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function u16(value) {
  const bytes = new Uint8Array(2);
  bytes[0] = value & 255;
  bytes[1] = (value >>> 8) & 255;
  return bytes;
}

function u32(value) {
  const bytes = new Uint8Array(4);
  bytes[0] = value & 255;
  bytes[1] = (value >>> 8) & 255;
  bytes[2] = (value >>> 16) & 255;
  bytes[3] = (value >>> 24) & 255;
  return bytes;
}

function concatBytes(parts) {
  const total = parts.reduce((sum, part) => sum + part.length, 0);
  const output = new Uint8Array(total);
  let offset = 0;
  parts.forEach((part) => {
    output.set(part, offset);
    offset += part.length;
  });
  return output;
}

function nodeFor(id) {
  return state.nodes.find((node) => node.id === id);
}

function isValidLink(link) {
  return canConnect(link.fromNode, link.fromPort, link.toNode, link.toPort);
}

function canConnect(fromNodeId, fromPortId, toNodeId, toPortId) {
  if (!fromNodeId || !toNodeId || fromNodeId === toNodeId) return false;
  const fromNode = nodeFor(fromNodeId);
  const toNode = nodeFor(toNodeId);
  const from = fromNode?.outputs.find((port) => port.id === fromPortId);
  const to = toNode?.inputs.find((port) => port.id === toPortId);
  if (!from || !to) return false;
  const fromInterface = fromNode?.toolType === 'topic_input';
  const toInterface = toNode?.toolType === 'topic_output';
  if (fromInterface && toInterface) return false;
  if (fromInterface || toInterface) return true;
  if (!from.dataType || !to.dataType) return true;
  if (isVideoImageType(from.dataType) && isVideoImageType(to.dataType) && acceptsVideoImageType(toNode)) return true;
  return from.dataType === to.dataType;
}

function defaultLinkTopic(fromNode, fromPort, toNode, toPort) {
  const fixedTopic = fixedTfTopicForOutput(fromNode, fromPort);
  if (fixedTopic) return fixedTopic;
  const src = nodeFor(fromNode)?.outputs.find((port) => port.id === fromPort);
  return normalizeTopic(src?.name || fromPort || 'topic');
}

function isTfLink(link) {
  const src = nodeFor(link?.fromNode)?.outputs.find((port) => port.id === link?.fromPort);
  const dst = nodeFor(link?.toNode)?.inputs.find((port) => port.id === link?.toPort);
  return src?.dataType === 'tf2_msgs/msg/TFMessage' || dst?.dataType === 'tf2_msgs/msg/TFMessage';
}

function displayLinkName(link) {
  if (isTfLink(link)) return 'TF';
  return link.name || defaultLinkTopic(link.fromNode, link.fromPort, link.toNode, link.toPort);
}

function fixedTfTopicForOutput(fromNode, fromPort) {
  const node = nodeFor(fromNode);
  if (node?.toolType === 'urdf_static_tf_publisher') return '/tf_static';
  const src = node?.outputs.find((port) => port.id === fromPort);
  if (src?.dataType !== 'tf2_msgs/msg/TFMessage') return '';
  const label = `${src.id || ''} ${src.name || ''}`.toLowerCase();
  return label.includes('static') ? '/tf_static' : '/tf';
}

function sourceTopicKey(fromNode, fromPort) {
  return `${fromNode || ''}:${fromPort || ''}`;
}

function sourceTopicName(fromNode, fromPort) {
  const link = state.links.find((item) => item.fromNode === fromNode && item.fromPort === fromPort && item.name);
  return link?.name ? normalizeTopic(link.name) : '';
}

function syncSourceTopicNames(fromNode, fromPort, name) {
  const fallback = defaultLinkTopic(fromNode, fromPort, '', '');
  const topic = fixedTfTopicForOutput(fromNode, fromPort) || normalizeTopic(name || fallback);
  state.links.forEach((link) => {
    if (link.fromNode === fromNode && link.fromPort === fromPort) link.name = topic;
  });
}

function normalizeSourceTopicNames() {
  const topics = new Map();
  state.links.forEach((link) => {
    const key = sourceTopicKey(link.fromNode, link.fromPort);
    if (!topics.has(key)) topics.set(key, fixedTfTopicForOutput(link.fromNode, link.fromPort) || normalizeTopic(link.name || defaultLinkTopic(link.fromNode, link.fromPort, link.toNode, link.toPort)));
  });
  state.links.forEach((link) => {
    link.name = fixedTfTopicForOutput(link.fromNode, link.fromPort) || normalizeTopic(topics.get(sourceTopicKey(link.fromNode, link.fromPort)) || defaultLinkTopic(link.fromNode, link.fromPort, link.toNode, link.toPort));
  });
}

function editLinkName(linkId) {
  const link = state.links.find((item) => item.id === linkId);
  if (!link) return;
  if (fixedTfTopicForOutput(link.fromNode, link.fromPort)) {
    syncSourceTopicNames(link.fromNode, link.fromPort, '');
    renderLinks();
    renderInspector();
    scheduleRun();
    return;
  }
  const fallback = defaultLinkTopic(link.fromNode, link.fromPort, link.toNode, link.toPort);
  const next = prompt('Topic name for this output topic', link.name || fallback);
  if (next === null) return;
  syncSourceTopicNames(link.fromNode, link.fromPort, next.trim() || fallback);
  renderLinks();
  renderInspector();
  scheduleRun();
}

function flashPort(row, cls) {
  row.classList.add(cls);
  setTimeout(() => row.classList.remove(cls), 350);
}

function clamp(v, min, max) {
  return Math.max(min, Math.min(max, v));
}

function nextPeriodicTimeMs(previousNext, periodMs, now) {
  let next = (previousNext > 0 ? previousNext : now) + periodMs;
  while (next <= now) next += periodMs;
  return next;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, '&#96;');
}

window.addEventListener('resize', renderLinks);
window.addEventListener('keydown', (ev) => {
  const shortcut = ev.metaKey || ev.ctrlKey;
  const target = ev.target;
  const textEditing = target?.closest?.('input, textarea, select, [contenteditable="true"]');
  if (shortcut && ev.key.toLowerCase() === 's') {
    ev.preventDefault();
    saveProject(ev.shiftKey);
    return;
  }
  if (shortcut && !textEditing && ev.key.toLowerCase() === 'z') {
    ev.preventDefault();
    if (ev.shiftKey) redoProject();
    else undoProject();
    return;
  }
  if (shortcut && !textEditing && ev.key.toLowerCase() === 'y') {
    ev.preventDefault();
    redoProject();
    return;
  }
  if (ev.key === 'Delete' || ev.key === 'Backspace') {
    if (textEditing) return;
    if (state.selectedNode) deleteNode(state.selectedNode);
    else if (state.selectedLink) deleteLink(state.selectedLink);
  }
});

init();
