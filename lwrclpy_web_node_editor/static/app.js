const UI_DISPLAY_FPS = 30;
const UI_DISPLAY_FRAME_MS = 1000 / UI_DISPLAY_FPS;
const IMAGE_FRAME_STALE_MS = UI_DISPLAY_FRAME_MS * 2;

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
  videoInputs: {},
  embeddedVideoInputs: {},
  undoStack: [],
  redoStack: [],
  historySnapshot: '',
  suppressHistory: false,
  projectFileHandle: null,
  projectFileName: 'lwrclpy_web_node_project.json',
  ready: false,
  readyInFlight: false,
  readySignature: '',
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

const INTERFACE_NODE_TEMPLATES = [
  {
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
    label: 'Video File Input',
    toolType: 'video_file_input',
    node: {
      name: 'video_file_input',
      inputs: [],
      outputs: [{ id: 'out1', name: 'frame', dataType: 'sensor_msgs/msg/Image' }],
      params: { loop: false, publishHz: 30, detectedFps: 0 },
      loopCode: '',
    },
  },
  {
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
  bindToolbar();
  bindCanvas();
  renderInterfaceNodeList();
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
  $('run-hz').oninput = refreshRunTimer;
  $('run-duration-model').onclick = runForDuration;
  $('save-project').onclick = () => saveProject(false);
  $('load-project').onchange = loadProject;
  $('export-ros2-package').onclick = exportRos2Package;
  $('config-input-count').oninput = renderConfigPorts;
  $('config-output-count').oninput = renderConfigPorts;
  $('config-timer-count').oninput = renderConfigPorts;
  $('config-import-code').oninput = updateEnvironmentDraft;
  $('config-requirements').oninput = updateEnvironmentDraft;
  $('node-form').addEventListener('submit', saveNodeDialog);
  $('code-form').addEventListener('submit', saveCodeDialog);
  $('signal-form').addEventListener('submit', saveSignalDialog);
  $('graph-form').addEventListener('submit', saveGraphDialog);
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
    const indexStr = ev.dataTransfer.getData('application/x-node-template');
    if (!indexStr) return;
    ev.preventDefault();
    const template = INTERFACE_NODE_TEMPLATES[parseInt(indexStr, 10)];
    if (!template) return;
    const pos = screenToWorld(ev.clientX, ev.clientY);
    const node = createInterfaceNode(template, pos);
    state.nodes.push(node);
    state.selectedNode = node.id;
    state.selectedLink = null;
    commitHistory();
    renderAll();
    scheduleRun();
  });
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
  };
}

function createInterfaceNode(template, pos = centerWorld()) {
  const node = structuredClone(template.node);
  node.id = `n${state.nextId++}`;
  node.toolType = template.toolType;
  node.x = Math.round(pos.x);
  node.y = Math.round(pos.y);
  node.params = node.params || {};
  return node;
}

function renderInterfaceNodeList() {
  const list = $('interface-node-list');
  list.innerHTML = '';
  INTERFACE_NODE_TEMPLATES.forEach((template, index) => {
    const button = document.createElement('button');
    button.className = 'interface-node-item';
    button.textContent = template.label;
    button.draggable = true;
    button.addEventListener('dragstart', (ev) => {
      ev.dataTransfer.setData('application/x-node-template', String(index));
      ev.dataTransfer.effectAllowed = 'copy';
    });
    button.onclick = () => {
      const node = createInterfaceNode(template);
      state.nodes.push(node);
      state.selectedNode = node.id;
      state.selectedLink = null;
      renderAll();
      scheduleRun();
    };
    list.appendChild(button);
  });
}

function openNodeDialog(node = null) {
  state.editingNode = node ? node.id : null;
  const draft = node ? structuredClone(node) : createDefaultNode();
  draft.timers = normalizeTimers(draft);
  $('node-dialog').dataset.draft = JSON.stringify(draft);
  $('node-dialog-title').textContent = node ? 'Edit lwrclpy Node' : 'Create lwrclpy Node';
  $('config-node-name').value = draft.name;
  $('config-input-count').value = draft.inputs.length;
  $('config-output-count').value = draft.outputs.length;
  $('config-timer-count').value = draft.timers.length;
  $('config-import-code').value = draft.importCode || DEFAULT_IMPORT_CODE;
  $('config-requirements').value = draft.requirements || '';
  renderConfigPorts();
  $('node-dialog').showModal();
}

function renderConfigPorts() {
  const dialog = $('node-dialog');
  const draft = JSON.parse(dialog.dataset.draft || JSON.stringify(createDefaultNode()));
  draft.name = $('config-node-name').value || draft.name;
  draft.inputs = resizePorts(draft.inputs || [], Number($('config-input-count').value || 0), 'in');
  draft.outputs = resizePorts(draft.outputs || [], Number($('config-output-count').value || 0), 'out');
  draft.timers = resizeTimers(normalizeTimers(draft), Number($('config-timer-count').value || 0));
  draft.timerEnabled = draft.timers.length > 0;
  draft.timerPeriodSec = draft.timers[0]?.periodSec || 1.0;
  draft.timerCode = draft.timers[0]?.callbackCode || DEFAULT_TIMER_CODE;
  draft.importCode = $('config-import-code').value || '';
  draft.requirements = $('config-requirements').value || '';
  dialog.dataset.draft = JSON.stringify(draft);
  renderPortConfigList('input-configs', draft.inputs, 'Input');
  renderPortConfigList('output-configs', draft.outputs, 'Output');
  renderTimerConfigList(draft.timers);
}

function updateEnvironmentDraft() {
  const draft = JSON.parse($('node-dialog').dataset.draft || JSON.stringify(createDefaultNode()));
  draft.importCode = $('config-import-code').value || '';
  draft.requirements = $('config-requirements').value || '';
  $('node-dialog').dataset.draft = JSON.stringify(draft);
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
  const next = ports.slice(0, count);
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

function renderPortConfigList(containerId, ports, labelPrefix) {
  const container = $(containerId);
  container.innerHTML = '';
  ports.forEach((port, index) => {
    const row = document.createElement('div');
    row.className = 'port-config-row';
    const type = parseDataType(port.dataType);
    const receiveMode = port.receiveMode || 'callback';
    const receiveModeField = containerId.startsWith('input')
      ? `<label class="checkbox-field"><input data-key="receiveMode" data-index="${index}" type="checkbox" ${receiveMode !== 'manual' ? 'checked' : ''}><span>Use Callback</span></label>`
      : '';
    row.innerHTML = `
      <label><span>${labelPrefix} Name</span><input data-key="name" data-index="${index}" value="${escapeAttr(port.name)}"></label>
      <label><span>Package</span><select data-key="typePackage" data-index="${index}">${packageOptions(type.pkg)}</select></label>
      <label><span>Kind</span><select data-key="typeKind" data-index="${index}">${kindOptions(type.pkg, type.kind)}</select></label>
      <label><span>Name</span><select data-key="typeName" data-index="${index}">${nameOptions(type.pkg, type.kind, type.name)}</select></label>
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
    el.className = 'node ros-node';
    el.dataset.id = node.id;
    el.style.left = `${node.x}px`;
    el.style.top = `${node.y}px`;
    el.innerHTML = `
      <div class="node-title">
        <div><strong>${escapeHtml(node.name)}</strong><small>${escapeHtml(nodeKindLabel(node))}</small></div>
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
        </div>`}`;
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
    makeNodeDraggable(el, node);
    renderPorts(el.querySelector('.inputs'), node, node.inputs, 'input');
    renderPorts(el.querySelector('.outputs'), node, node.outputs, 'output');
    // Restore canvas views immediately after node element is added to DOM
    const viewEl = el.querySelector('[data-node-view]');
    if (viewEl) patchNodeViewEl(viewEl, state.nodeViews[node.id]);
  });
}

function nodeKindLabel(node) {
  if (!node.toolType) return 'lwrclpy Custom Node';
  if (['topic_input', 'topic_output'].includes(node.toolType)) return 'Boundary Node';
  return 'Tool Node';
}

function inspectorHint(node) {
  if (!node.toolType) {
    const timers = normalizeTimers(node);
    return `${node.inputs.length} subscriptions / ${node.outputs.length} publishers${timers.length ? ` / ${timers.length} timer${timers.length === 1 ? '' : 's'}` : ''}`;
  }
  if (['topic_input', 'topic_output'].includes(node.toolType)) {
    return 'Graph boundary only. Sub/Pub is handled by the connected processing node.';
  }
  return 'Built-in processing/view node.';
}

function effectiveVideoHz(node) {
  const p = node.params || {};
  return Math.max(0.01, Number(p.detectedFps || p.nativeFps || p.sourceFps || p.publishHz || 30));
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
    const fpsLabel = detectedFps > 0 ? detectedFps.toFixed(2) + ' fps' : 'auto (30 fps)';
    return `<div class="node-actions tool-actions">
      <label class="tool-field tool-field-wide"><span>Path</span><input data-tool-video-path type="text" value="${escapeAttr(videoPath)}" placeholder="No video selected" readonly tabindex="-1"></label>
      <button data-action="select-video-file">Select Video</button>
      <label class="tool-field"><span>FPS</span><span class="tool-value-display">${escapeHtml(fpsLabel)}</span></label>
      <label class="tool-check"><input data-tool-video-loop type="checkbox" ${loopChecked}> Loop</label>
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

function viewNodeHtml(node) {
  if (!['image_file_input', 'video_file_input', 'function_generator', 'image_view', 'image_file_save', 'graph_view', 'topic_hz_monitor'].includes(node.toolType)) return '';
  const viewClass = node.toolType === 'video_file_input' ? ' node-view-video' : '';
  return `<div class="node-view${viewClass}" data-node-view="${escapeAttr(node.id)}">${renderViewContent(state.nodeViews[node.id])}</div>`;
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
  const setVideoPath = (path) => {
    path = String(path || '').trim();
    if (!path) return;
    stopVideoInput(node.id);
    node.params = {
      ...(node.params || {}),
      fileName: path.split(/[\\/]/).filter(Boolean).pop() || path,
      videoPath: path,
      serverDecode: true,
      publishHz: effectiveVideoHz(node),
      maxSide: Number(node.params?.maxSide || 640),
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
      if (selected?.path) setVideoPath(selected.path);
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
    box.innerHTML = `
      <div class="inspector-title">Edge</div>
      <div class="hint">${link.fromNode}.${link.fromPort} -> ${link.toNode}.${link.toPort}</div>
      <label class="field"><span>Topic Name</span><input id="link-name" value="${escapeAttr(link.name || defaultLinkTopic(link.fromNode, link.fromPort, link.toNode, link.toPort))}"></label>
      <button id="delete-link">Delete Link</button>`;
    $('link-name').oninput = () => {
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
    ${node.toolType ? '' : `<div class="inspector-actions">
        <button id="inspect-config">Configure Ports</button>
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
  const inspectSignalSettings = $('inspect-signal-settings');
  if (inspectSignalSettings) inspectSignalSettings.onclick = () => openSignalDialog(node);
  const inspectGraphSettings = $('inspect-graph-settings');
  if (inspectGraphSettings) inspectGraphSettings.onclick = () => openGraphDialog(node);
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

function makeNodeDraggable(el, node) {
  const title = el.querySelector('.node-title');
  title.addEventListener('pointerdown', (ev) => {
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
    state.dragLink.pointer = { x: e.clientX, y: e.clientY };
    updateDropTargets();
    renderLinks();
  };
  const up = (e) => {
    const target = document.elementFromPoint(e.clientX, e.clientY)?.closest('.port.input');
    if (target) finishLinkDrag(e, target);
    clearLinkDrag();
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', up);
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
  const defaultTopic = sourceTopicName(state.dragLink.fromNode, state.dragLink.fromPort) || defaultLinkTopic(state.dragLink.fromNode, state.dragLink.fromPort, inputRow.dataset.node, inputRow.dataset.port);
  const topic = prompt('Topic name for this output topic', defaultTopic);
  if (topic === null) return;
  const topicName = topic.trim() || defaultTopic;
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
  text.dataset.link = link.id;
  text.textContent = link.name || defaultLinkTopic(link.fromNode, link.fromPort, link.toNode, link.toPort);
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
  const maxX = Math.max(...state.nodes.map((n) => n.x)) + 320;
  const maxY = Math.max(...state.nodes.map((n) => n.y)) + 240;
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
  const payload = graphRunPayload();
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
  setExecutionStatus('running', 'Preparing node environments');
  try {
    const payload = graphRunPayload();
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
    if (state.autoTimer) state.autoTimer = setTimeout(pollRunStatus, UI_DISPLAY_FRAME_MS);
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
      maxSide: Number(p.maxSide || 640),
    };
  }
  return {
    fileName: p.fileName || '',
    frameMessage: p.frameMessage,
    loop: Boolean(p.loop),
    publishHz: effectiveVideoHz(node),
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
    $('run-model').classList.remove('active');
    setExecutionStatus('stopped', `Server run stopped after ${state.tickCount} ticks`);
  }
}

function runHasStartingNodes(nodes) {
  return Object.values(nodes || {}).some((payload) => {
    const env = String(payload?.meta?.environment || '').toLowerCase();
    const status = String(payload?.view?.status || '').toLowerCase();
    if (/starting|waiting|dds discovery|worker startup/.test(env)) return true;
    if (/starting|waiting|dds discovery|worker startup/.test(status)) return true;
    const view = payload?.view;
    if (view?.kind === 'image' && !(view.dataUrl || view.raw || view.frameRef) && /worker/.test(status)) return true;
    if (view?.kind === 'plot' && Array.isArray(view.series) && view.series.length === 0 && /worker/.test(status)) return true;
    return false;
  });
}

async function stopWorkers(force = false) {
  try {
    const endpoint = force ? '/api/force-stop' : '/api/stop';
    const data = await fetch(endpoint, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ force }),
    }).then((res) => res.json());
    const count = Object.keys(data.stopped || {}).length;
    setExecutionStatus('stopped', `${force ? 'Force stopped' : 'Stopped'} ${count} worker process${count === 1 ? '' : 'es'}`);
    return data;
  } catch (err) {
    setExecutionStatus('error', `Stop API error: ${err.message}`);
    return null;
  }
}

function updateStatus(data) {
  const runtime = data.lwrclpy || {};
  const setup = data.setup?.complete === false ? ` / setup blocked${runtime.error ? ': ' + runtime.error : ''}` : '';
  const text = (runtime.available ? 'lwrclpy available' : `lwrclpy unavailable${runtime.error ? ': ' + runtime.error : ''}`) + setup;
  $('runtime-status').textContent = text;
  $('runtime-detail').textContent = text;
  $('node-count').textContent = `${state.nodes.length} nodes / ${state.links.length} links`;
}

async function refreshRuntimeHealth() {
  try {
    const data = await fetch('/api/health').then((res) => res.json());
    const runtime = data.lwrclpy || {};
    const text = runtime.available ? 'lwrclpy available' : `lwrclpy unavailable${runtime.error ? ': ' + runtime.error : ''}`;
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
      // Don't replace a valid image view with an empty one (avoids flicker when frames are sparse)
      const existingHasImage = existing?.kind === 'image' && (existing?.dataUrl || existing?.raw?.data || existing?.frameRef);
      const newHasImage = newView?.kind === 'image' && (newView?.dataUrl || newView?.raw?.data || newView?.frameRef);
      if (existingHasImage && newView?.kind === 'image' && !newHasImage) {
        const nextView = { ...existing, status: newView.status || existing.status };
        if (nodeViewSignature(existing) !== nodeViewSignature(nextView)) changedNodeIds.add(nodeId);
        state.nodeViews[nodeId] = nextView;
      } else {
        if (nodeViewSignature(existing) !== nodeViewSignature(newView)) changedNodeIds.add(nodeId);
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

function nodeViewSignature(view) {
  if (!view) return '';
  if (view.kind === 'image') {
    if (view.frameRef && isStreamFrameRef(view.frameRef)) return `image:stream:${view.frameRef.nodeId}:${view.status || ''}`;
    if (view.frameRef) return `image:frame:${view.frameRef.nodeId}:${view.frameRef.seq}:${view.status || ''}`;
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
  const signature = `${frameRef.nodeId}:${frameRef.seq}`;
  const previousDesired = canvas.dataset.desiredFrame || '';
  canvas.dataset.desiredFrame = signature;
  canvas._nextFrameRef = frameRef;
  if (canvas._frameDrawInProgress && previousDesired && previousDesired !== signature) {
    const startedAt = Number(canvas.dataset.frameDrawStartedAt || 0);
    if (performance.now() - startedAt > IMAGE_FRAME_STALE_MS) cancelCanvasFrameLoad(canvas);
  }
  if (canvas._frameDrawScheduled) return;
  canvas._frameDrawScheduled = true;
  requestAnimationFrame(() => {
    canvas._frameDrawScheduled = false;
    pumpFrameRefDraw(canvas);
  });
}

function pumpFrameRefDraw(canvas) {
  if (!canvas || canvas._frameDrawInProgress) return;
  const next = canvas._nextFrameRef;
  canvas._nextFrameRef = null;
  if (!next) return;
  const signature = `${next.nodeId}:${next.seq}`;
  if (canvas.dataset.rawSignature === signature) return;
  canvas._frameDrawInProgress = true;
  canvas.dataset.frameDrawStartedAt = String(performance.now());
  drawFrameRefToCanvas(canvas, next).finally(() => {
    canvas._frameDrawInProgress = false;
    delete canvas.dataset.frameDrawStartedAt;
    const queued = canvas._nextFrameRef;
    if (!queued) return;
    const queuedSignature = `${queued.nodeId}:${queued.seq}`;
    if (canvas.dataset.rawSignature === queuedSignature) {
      canvas._nextFrameRef = null;
      return;
    }
    requestAnimationFrame(() => pumpFrameRefDraw(canvas));
  });
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

function drawEncodedFrameImage(canvas, frameRef, signature) {
  return new Promise((resolve) => {
    const img = canvas._frameImage || new Image();
    canvas._frameImage = img;
    canvas._frameImageResolve = resolve;
    const finish = () => {
      if (canvas._frameImageResolve === resolve) canvas._frameImageResolve = null;
      resolve();
    };
    img.onload = () => {
      img.onload = null;
      img.onerror = null;
      if (canvas.dataset.desiredFrame !== signature) {
        finish();
        return;
      }
      drawBitmapLike(canvas, img);
      canvas.dataset.rawSignature = signature;
      finish();
    };
    img.onerror = () => {
      img.onload = null;
      img.onerror = null;
      finish();
    };
    img.decoding = 'async';
    if ('fetchPriority' in img) img.fetchPriority = 'high';
    img.src = `/api/node-frame?nodeId=${encodeURIComponent(frameRef.nodeId)}&seq=${encodeURIComponent(frameRef.seq)}`;
  });
}

async function drawFrameRefToCanvas(canvas, frameRef) {
  if (!frameRef?.nodeId || !frameRef.seq) return;
  const signature = `${frameRef.nodeId}:${frameRef.seq}`;
  canvas.dataset.desiredFrame = signature;
  if (canvas.dataset.rawSignature === signature) return;
  if (canvas.dataset.pendingFrame === signature) return;
  cancelCanvasFrameLoad(canvas);
  if (['jpeg', 'jpg', 'bmp', 'png', 'webp'].includes(String(frameRef.encoding || '').toLowerCase())) {
    canvas.dataset.pendingFrame = signature;
    try {
      await drawEncodedFrameImage(canvas, frameRef, signature);
    } finally {
      if (canvas.dataset.pendingFrame === signature) delete canvas.dataset.pendingFrame;
    }
    return;
  }
  const controller = new AbortController();
  frameFetchControllers.set(canvas, controller);
  canvas.dataset.pendingFrame = signature;
  try {
    const response = await fetch(`/api/node-frame?nodeId=${encodeURIComponent(frameRef.nodeId)}&seq=${encodeURIComponent(frameRef.seq)}`, {
      signal: controller.signal,
      cache: 'no-store',
    });
    if (response.status === 204) return;
    if (!response.ok) return;
    const responseSeq = Number(response.headers.get('x-frame-seq') || frameRef.seq);
    const drawnSignature = `${frameRef.nodeId}:${Number.isFinite(responseSeq) && responseSeq > 0 ? responseSeq : frameRef.seq}`;
    if (canvas.dataset.desiredFrame !== signature && canvas.dataset.desiredFrame !== drawnSignature) return;
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (canvas.dataset.desiredFrame !== signature && canvas.dataset.desiredFrame !== drawnSignature) return;
    const width = Math.max(1, Number(frameRef.width));
    const height = Math.max(1, Number(frameRef.height));
    if (!Number(frameRef.width) || !Number(frameRef.height)) return;
    const required = width * height * 4;
    if (!canvas._rgbaBuffer || canvas._rgbaBuffer.length !== required) {
      canvas._rgbaBuffer = new Uint8ClampedArray(required);
      canvas._imageData = new ImageData(canvas._rgbaBuffer, width, height);
    }
    const rgba = canvas._rgbaBuffer;
    const pixelCount = width * height;
    for (let i = 0; i < pixelCount; i += 1) {
      const src = i * 3;
      const dst = i * 4;
      rgba[dst] = bytes[src] || 0;
      rgba[dst + 1] = bytes[src + 1] || 0;
      rgba[dst + 2] = bytes[src + 2] || 0;
      rgba[dst + 3] = 255;
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
  if (view?.kind === 'image' && (view.dataUrl || view.raw || view.frameRef)) {
    const existingFig = el.querySelector('figure.image-view');
    if (existingFig) {
      if (view.frameRef && isStreamFrameRef(view.frameRef)) {
        const img = existingFig.querySelector('img.image-stream');
        const cap = existingFig.querySelector('figcaption');
        if (img && cap) {
          const newCap = view.status || '';
          if (cap.textContent !== newCap) cap.textContent = newCap;
          const streamSrc = frameStreamSrc(view.frameRef);
          if (img.dataset.streamSrc !== streamSrc) {
            img.dataset.streamSrc = streamSrc;
            img.src = streamSrc;
          }
          return;
        }
      } else if (existingFig.querySelector('img.image-stream')) {
        // Switching from MJPEG stream back to canvas/raw requires a full rebuild.
      } else {
        const canvas = existingFig.querySelector('canvas.image-canvas');
        const cap = existingFig.querySelector('figcaption');
        if (canvas && cap) {
          const newCap = view.status || '';
          if (cap.textContent !== newCap) cap.textContent = newCap;
          if (view.frameRef) scheduleFrameRefDraw(canvas, view.frameRef);
          else if (view.raw) drawRawImageToCanvas(canvas, view.raw);
          else drawToCanvas(canvas, view.dataUrl);
          return;
        }
      }
    }
  }
  const newHtml = renderViewContent(view);
  if (el.innerHTML !== newHtml) {
    el.innerHTML = newHtml;
    if (view?.kind === 'image' && (view.dataUrl || view.raw || view.frameRef)) {
      const canvas = el.querySelector('canvas.image-canvas');
      if (canvas && view.frameRef) scheduleFrameRefDraw(canvas, view.frameRef);
      else if (canvas && view.raw) drawRawImageToCanvas(canvas, view.raw);
      else if (canvas) drawToCanvas(canvas, view.dataUrl);
    }
  }
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
  const newHtml = renderViewContent(nextView);
  if (el.innerHTML !== newHtml) el.innerHTML = newHtml;
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
  return view;
}

function renderViewContent(view) {
  if (!view) return '<div class="view-empty">No data</div>';
  if (view.kind === 'image' && (view.dataUrl || view.raw || view.frameRef)) {
    if (view.frameRef && isStreamFrameRef(view.frameRef)) {
      const src = frameStreamSrc(view.frameRef);
      return `<figure class="image-view"><img class="image-stream" data-stream-src="${escapeAttr(src)}" src="${escapeAttr(src)}" alt=""><figcaption>${escapeHtml(view.status || '')}</figcaption></figure>`;
    }
    return `<figure class="image-view"><canvas class="image-canvas"></canvas><figcaption>${escapeHtml(view.status || '')}</figcaption></figure>`;
  }
  if (view.kind === 'plot') {
    return renderPlot(view.series || [], view.status || '', view);
  }
  return `<div class="view-empty">${escapeHtml(view.status || 'No data')}</div>`;
}

function isStreamFrameRef(frameRef) {
  return ['jpeg', 'jpg'].includes(String(frameRef?.encoding || '').toLowerCase()) && frameRef?.nodeId;
}

function frameStreamSrc(frameRef) {
  if (frameRef?.streamUrl) return String(frameRef.streamUrl);
  return `/api/node-stream?nodeId=${encodeURIComponent(frameRef.nodeId)}`;
}

function renderPlot(series, label, view = {}) {
  const width = 280;
  const height = 120;
  const allPoints = series.map((item, index) => {
    if (item && typeof item === 'object') return { t: Number(item.t), y: Number(item.y) };
    return { t: index, y: Number(item) };
  }).filter((point) => Number.isFinite(point.t) && Number.isFinite(point.y));
  const latestT = allPoints.length ? Math.max(...allPoints.map((point) => point.t)) : 0;
  const windowSec = Math.max(0.1, Number(view.xAxisSeconds || (latestT - (allPoints[0]?.t || 0)) || 1));
  const startT = latestT - windowSec;
  const pointsData = allPoints.filter((point) => point.t >= startT);
  if (pointsData.length < 2) {
    return `<div class="plot-view"><svg viewBox="0 0 ${width} ${height}"></svg><span>${escapeHtml(label || 'Waiting for values')}</span></div>`;
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
  return `<div class="plot-view"><svg viewBox="0 0 ${width} ${height}"><polyline points="${points}"></polyline></svg><span>${escapeHtml(label)} ${windowSec.toFixed(1)}s / ${min.toFixed(3)} .. ${max.toFixed(3)}</span></div>`;
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
  const frame = imageElementToMessage(video, 360, { includeDataUrl: options.includeDataUrl !== false });
  node.params = {
    ...(node.params || {}),
    fileName: controller.fileName,
    dataUrl: frame.dataUrl,
    frameMessage: frame.message,
    loop: controller.loop,
    publishHz: effectiveVideoHz(node),
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
    const frameIndex = Math.floor(elapsed * fps);
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

function imageElementToMessage(source, maxSide = 640, options = {}) {
  const naturalWidth = source.videoWidth || source.naturalWidth || source.width;
  const naturalHeight = source.videoHeight || source.naturalHeight || source.height;
  const scale = Math.min(1, maxSide / Math.max(naturalWidth, naturalHeight));
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
  return clamp(Number($('run-hz')?.value || 1000), 1, 1000);
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
  $('run-model').classList.remove('active');
  setExecutionStatus('stopping', `Force stopping after ${state.tickCount} ticks`);
  stopWorkers(true);
}

function runForDuration() {
  const seconds = Math.max(0.1, Number($('run-duration').value || 5));
  if (state.runStopTimer) {
    clearTimeout(state.runStopTimer);
    state.runStopTimer = null;
  }
  startServerRun(seconds);
}

function clearGraph() {
  invalidateReady();
  stopAllVideoInputs();
  state.nodes = [];
  state.links = [];
  state.selectedNode = null;
  state.selectedLink = null;
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
  if (window.showSaveFilePicker) {
    try {
      if (saveAs || !state.projectFileHandle) {
        state.projectFileHandle = await window.showSaveFilePicker({
          suggestedName: state.projectFileName || 'lwrclpy_web_node_project.json',
          types: [{ description: 'lwrclpy Web Node Editor Project', accept: { 'application/json': ['.json'] } }],
        });
      }
      const writable = await state.projectFileHandle.createWritable();
      await writable.write(text);
      await writable.close();
      state.projectFileName = state.projectFileHandle.name || state.projectFileName;
      setExecutionStatus('idle', `Saved ${state.projectFileName}`);
      return;
    } catch (err) {
      if (err?.name === 'AbortError') return;
      setExecutionStatus('error', `Save failed: ${err.message}`);
      return;
    }
  }
  downloadText(state.projectFileName || 'lwrclpy_web_node_project.json', text, 'application/json');
  setExecutionStatus('idle', `Downloaded ${state.projectFileName || 'project JSON'}`);
}

async function loadProject(event) {
  const file = event.target.files[0];
  event.target.value = '';
  if (!file) return;
  stopAllVideoInputs();
  const imported = JSON.parse(await file.text());
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
  state.projectFileName = file.name || 'lwrclpy_web_node_project.json';
  renderAll();
  state.suppressHistory = false;
  resetHistory();
  scheduleRun();
}

function exportRos2Package() {
  const config = projectRos2PackageConfig();
  if (!config.nodes.length) {
    alert('No custom ROS 2 nodes to export. Built-in browser tool nodes are not exported.');
    return;
  }
  const files = renderRos2PackageFiles(config);
  const zip = makeZip(files);
  downloadBlob(`${config.packageName}.zip`, zip, 'application/zip');
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
    const baseName = safePackageName(projectConfig().name || 'rclpy_exported_nodes');
    const usedModules = new Set();
    const nodes = state.nodes.filter((node) => !node.toolType).map((node) => {
    const config = nodePythonConfig(node);
    const moduleName = uniqueModuleName(safePythonIdentifier(node.name || node.id), usedModules);
    return { ...config, moduleName, executableName: moduleName };
    });
    return {
    format: 'lwrclpy-web-node-editor-ros2-package',
    version: 1,
    packageName: baseName,
    nodes,
    skippedNodes: state.nodes.filter((node) => node.toolType).map((node) => ({ id: node.id, name: node.name, toolType: node.toolType })),
    dependencies: ros2PackageDependencies(nodes),
    };
  }

  function renderRos2PackageFiles(config) {
    const packageName = config.packageName;
    const files = [];
    files.push({ path: `${packageName}/package.xml`, content: renderRos2PackageXml(config) });
    files.push({ path: `${packageName}/setup.py`, content: renderRos2SetupPy(config) });
    files.push({ path: `${packageName}/setup.cfg`, content: renderRos2SetupCfg(config) });
    files.push({ path: `${packageName}/resource/${packageName}`, content: '' });
    files.push({ path: `${packageName}/${packageName}/__init__.py`, content: '' });
    files.push({ path: `${packageName}/${packageName}/runtime.py`, content: renderRos2RuntimePy() });
    files.push({ path: `${packageName}/launch/project.launch.py`, content: renderRos2LaunchPy(config) });
    files.push({ path: `${packageName}/README.md`, content: renderRos2PackageReadme(config) });
    const requirements = aggregateRequirements(config.nodes);
    if (requirements) files.push({ path: `${packageName}/requirements.txt`, content: requirements });
    config.nodes.forEach((nodeConfig) => {
    files.push({ path: `${packageName}/${packageName}/${nodeConfig.moduleName}.py`, content: renderRos2NodePy(nodeConfig) });
    });
    return files.map((file) => ({
      ...file,
      content: typeof file.content === 'string' ? normalizeGeneratedFile(file.content) : file.content,
    }));
  }

  function normalizeGeneratedFile(text) {
    return String(text).replace(/\n {2}/g, '\n').trimEnd() + '\n';
  }

  function renderRos2PackageXml(config) {
    const deps = config.dependencies.map((dep) => `  <exec_depend>${escapeXml(dep)}</exec_depend>`).join('\n');
    return `<?xml version="1.0"?>
  <package format="3">
    <name>${escapeXml(config.packageName)}</name>
    <version>0.0.0</version>
    <description>ROS 2 rclpy package exported from Web Node Editor.</description>
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

  function renderRos2SetupPy(config) {
    const consoleScripts = config.nodes.map((node) => `            '${node.executableName} = ${config.packageName}.${node.moduleName}:main',`).join('\n');
    return `from setuptools import find_packages, setup

  package_name = '${config.packageName}'

  setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
      ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
      ('share/' + package_name, ['package.xml']),
      ('share/' + package_name + '/launch', ['launch/project.launch.py']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='user',
    maintainer_email='user@example.com',
    description='ROS 2 rclpy package exported from Web Node Editor.',
    license='TODO',
    tests_require=['pytest'],
    entry_points={
      'console_scripts': [
  ${consoleScripts}
      ],
    },
  )
  `;
  }

  function renderRos2SetupCfg(config) {
    return `[develop]
  script_dir=$base/lib/${config.packageName}
  [install]
  install_scripts=$base/lib/${config.packageName}
  `;
  }

  function renderRos2LaunchPy(config) {
    const nodes = config.nodes.map((node) => `        Node(
        package='${config.packageName}',
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
    const metadata = JSON.stringify(config, null, 2);
    return `#!/usr/bin/env python3
  import json

  from .runtime import run_node


  CONFIG = json.loads(${JSON.stringify(metadata)})


  def main(args=None):
    run_node(CONFIG, args=args)


  if __name__ == '__main__':
    main()
  `;
  }

  function renderRos2RuntimePy() {
    return `import importlib
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
      import_code = self.node_config.get('importCode', '').strip()
      if import_code:
        exec(import_code, globals_dict, globals_dict)
      self._globals_cache = globals_dict
      return globals_dict

    def _locals(self, extra):
      return {'node': self.node, 'params': self.node_config.get('params', {}), 'state': self.state, 'publish': self.publish, 'log': self.log, **extra}

    def _store_input(self, input_id, value):
      self.last_inputs[input_id] = value
      queue = self.input_queues.setdefault(input_id, [])
      queue.append(value)
      del queue[:-100]

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
    const nodes = config.nodes.map((node) => `- \`${node.executableName}\`: \`${node.node.name}\``).join('\n');
    const skipped = config.skippedNodes.length
    ? `\n\nThe following browser-only nodes were not exported as ROS 2 executables:\n${config.skippedNodes.map((node) => `- ${node.name} (${node.toolType})`).join('\n')}\n`
    : '';
    return `# ${config.packageName}

  ROS 2 Python package exported for standard rclpy.

  ## Nodes

  ${nodes}
  ${skipped}
  ## Build and run

  Copy this package into a ROS 2 workspace ` + '`src`' + ` directory, then run:

  ` + '```bash' + `
  colcon build --packages-select ${config.packageName}
  source install/setup.bash
  ros2 launch ${config.packageName} project.launch.py
  ` + '```' + `

  If node code imports extra Python packages, install the generated ` + '`requirements.txt`' + ` in the same environment before launching.
  `;
  }

  function ros2PackageDependencies(nodes) {
    const deps = new Set(['rclpy', 'launch', 'launch_ros']);
    nodes.forEach((item) => {
    const node = item.node || {};
    [...(node.inputs || []), ...(node.outputs || [])].forEach((port) => {
      const packageName = String(port.dataType || '').split('/')[0];
      if (packageName) deps.add(packageName);
    });
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
    let name = base || 'node';
    let index = 2;
    while (used.has(name)) {
    name = `${base}_${index++}`;
    }
    used.add(name);
    return name;
  }

  function escapeXml(value) {
    return String(value).replace(/[<>&"']/g, (ch) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&apos;' }[ch]));
  }

function renderProjectPythonFile(config) {
  const metadata = JSON.stringify(config, null, 2);
  return `#!/usr/bin/env python3
# Generated by Web Node Editor for standard ROS 2 rclpy.
# This file runs exported custom ROS 2 nodes from one saved project.

import importlib
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import rclpy
from rclpy.executors import MultiThreadedExecutor


CONFIG = json.loads(${JSON.stringify(metadata)})


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
        import_code = self.node_config.get("importCode", "").strip()
        if import_code:
            original_path = list(sys.path)
            try:
                if self.env_site_packages:
                    sys.path.insert(0, str(self.env_site_packages))
                exec(import_code, globals_dict, globals_dict)
            finally:
                sys.path[:] = original_path
        return globals_dict

    def _locals(self, extra):
        return {"params": self.node_config.get("params", {}), "state": self.state, "publish": self.publish, "log": self.log, **extra}

    def _store_input(self, input_id, value):
        self.last_inputs[input_id] = value
        queue = self.input_queues.setdefault(input_id, [])
        queue.append(value)
        del queue[:-100]

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
  return `#!/usr/bin/env python3
# Generated by Web Node Editor for standard ROS 2 rclpy.
# LWRCLPY_WEB_NODE_EDITOR_CONFIG_START
${metadata.split('\n').map((line) => `# ${line}`).join('\n')}
# LWRCLPY_WEB_NODE_EDITOR_CONFIG_END

import importlib
import json
import time

import rclpy
from rclpy.executors import MultiThreadedExecutor


CONFIG = json.loads(${JSON.stringify(metadata)})


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
        return {
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

    def _locals(self, extra):
        return {
            "params": self.node_config.get("params", {}),
            "state": self.state,
            "publish": self.publish,
            "log": self.log,
            **extra,
        }

    def _store_input(self, input_id, value):
        self.last_inputs[input_id] = value
        queue = self.input_queues.setdefault(input_id, [])
        queue.append(value)
        del queue[:-100]

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
    return port.dataType ?? fallback;
  };
  return {
    id: node.id || `n${state.nextId++}`,
    name: node.name || 'imported_lwrclpy_node',
    x: Number(node.x || 0),
    y: Number(node.y || 0),
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
    timerCode: node.timerCode || DEFAULT_TIMER_CODE,
    importCode: node.importCode || DEFAULT_IMPORT_CODE,
    requirements: node.requirements || '',
    toolType: node.toolType || '',
    params: node.params || {},
  };
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
  const topic = String(name || '').trim();
  if (!topic) return '/topic';
  return topic.startsWith('/') ? topic : `/${topic}`;
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
  return from.dataType === to.dataType;
}

function defaultLinkTopic(fromNode, fromPort, toNode, toPort) {
  const src = nodeFor(fromNode)?.outputs.find((port) => port.id === fromPort);
  return `/${src?.name || fromPort || 'topic'}`;
}

function sourceTopicKey(fromNode, fromPort) {
  return `${fromNode || ''}:${fromPort || ''}`;
}

function sourceTopicName(fromNode, fromPort) {
  const link = state.links.find((item) => item.fromNode === fromNode && item.fromPort === fromPort && item.name);
  return link?.name || '';
}

function syncSourceTopicNames(fromNode, fromPort, name) {
  const fallback = defaultLinkTopic(fromNode, fromPort, '', '');
  const topic = name || fallback;
  state.links.forEach((link) => {
    if (link.fromNode === fromNode && link.fromPort === fromPort) link.name = topic;
  });
}

function normalizeSourceTopicNames() {
  const topics = new Map();
  state.links.forEach((link) => {
    const key = sourceTopicKey(link.fromNode, link.fromPort);
    if (!topics.has(key)) topics.set(key, link.name || defaultLinkTopic(link.fromNode, link.fromPort, link.toNode, link.toPort));
  });
  state.links.forEach((link) => {
    link.name = topics.get(sourceTopicKey(link.fromNode, link.fromPort)) || defaultLinkTopic(link.fromNode, link.fromPort, link.toNode, link.toPort);
  });
}

function editLinkName(linkId) {
  const link = state.links.find((item) => item.id === linkId);
  if (!link) return;
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
