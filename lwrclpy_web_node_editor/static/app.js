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
  runStopTimer: null,
  view: { x: 0, y: 0, scale: 1 },
  dragLink: null,
  lastRunAt: 0,
  nodeViews: {},
};

const $ = (id) => document.getElementById(id);
const workspace = () => $('workspace');
const scene = () => $('scene');

const DEFAULT_CALLBACK_CODE = `# Runs when this input receives a message/request and Receive Mode is Callback.
# Available: input_id, msg, request, response, state, outputs, publish(output_id, value), log(...)
log("received", input_id, msg)

# Example for std_msgs/msg/String:
# outputs["out1"] = msg.data
`;

const DEFAULT_LOOP_CODE = `# Runs on every Run / Run For tick.
# Available: inputs, outputs, state, now, latest(input_id), take(input_id), has_input(input_id), publish(...), log(...)

# Example:
# while has_input("in1"):
#     msg = take("in1")
#     outputs["out1"] = msg.data
`;

const DEFAULT_TIMER_CODE = `# Runs when the node timer period elapses.
# Available: inputs, outputs, state, now, period, latest(input_id), take(input_id), has_input(input_id), publish(...), log(...)

# Example:
# state["count"] = state.get("count", 0) + 1
# outputs["out1"] = state["count"]
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
      params: {},
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
      params: {},
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
      inputs: [{ id: 'in1', name: 'value', dataType: '', receiveMode: 'manual', callbackCode: '' }],
      outputs: [],
      params: { fieldPath: 'data', sampleLimit: 120 },
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
  runGraph();
}

function bindToolbar() {
  $('create-node-side').onclick = () => openNodeDialog();
  $('run-model').onclick = startRun;
  $('stop-model').onclick = stopRun;
  $('run-duration-model').onclick = runForDuration;
  $('save-project').onclick = saveProject;
  $('load-project').onchange = loadProject;
  $('export-project-python').onclick = exportProjectPython;
  $('export-project-launch').onclick = exportProjectLaunch;
  $('config-input-count').oninput = renderConfigPorts;
  $('config-output-count').oninput = renderConfigPorts;
  $('config-timer-enabled').oninput = updateTimerDraft;
  $('config-timer-period').oninput = updateTimerDraft;
  $('config-import-code').oninput = updateEnvironmentDraft;
  $('config-requirements').oninput = updateEnvironmentDraft;
  $('node-form').addEventListener('submit', saveNodeDialog);
  $('code-form').addEventListener('submit', saveCodeDialog);
  document.addEventListener('selectstart', (ev) => {
    if (ev.target.closest('#workspace')) ev.preventDefault();
  });
  document.querySelectorAll('[data-close-dialog]').forEach((button) => {
    button.onclick = () => $(button.dataset.closeDialog).close();
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
  INTERFACE_NODE_TEMPLATES.forEach((template) => {
    const button = document.createElement('button');
    button.className = 'interface-node-item';
    button.textContent = template.label;
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
  $('node-dialog').dataset.draft = JSON.stringify(draft);
  $('node-dialog-title').textContent = node ? 'Edit lwrclpy Node' : 'Create lwrclpy Node';
  $('config-node-name').value = draft.name;
  $('config-input-count').value = draft.inputs.length;
  $('config-output-count').value = draft.outputs.length;
  $('config-timer-enabled').checked = Boolean(draft.timerEnabled);
  $('config-timer-period').value = Number(draft.timerPeriodSec || 1.0);
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
  draft.timerEnabled = $('config-timer-enabled').checked;
  draft.timerPeriodSec = Math.max(0.001, Number($('config-timer-period').value || 1.0));
  draft.timerCode = draft.timerCode || DEFAULT_TIMER_CODE;
  draft.importCode = $('config-import-code').value || '';
  draft.requirements = $('config-requirements').value || '';
  dialog.dataset.draft = JSON.stringify(draft);
  renderPortConfigList('input-configs', draft.inputs, 'Input');
  renderPortConfigList('output-configs', draft.outputs, 'Output');
}

function updateEnvironmentDraft() {
  const draft = JSON.parse($('node-dialog').dataset.draft || JSON.stringify(createDefaultNode()));
  draft.importCode = $('config-import-code').value || '';
  draft.requirements = $('config-requirements').value || '';
  $('node-dialog').dataset.draft = JSON.stringify(draft);
}

function updateTimerDraft() {
  const draft = JSON.parse($('node-dialog').dataset.draft || JSON.stringify(createDefaultNode()));
  draft.timerEnabled = $('config-timer-enabled').checked;
  draft.timerPeriodSec = Math.max(0.001, Number($('config-timer-period').value || 1.0));
  draft.timerCode = draft.timerCode || DEFAULT_TIMER_CODE;
  draft.importCode = $('config-import-code').value || '';
  draft.requirements = $('config-requirements').value || '';
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
      ? `<label><span>Receive Mode</span><select data-key="receiveMode" data-index="${index}">
          <option value="callback"${receiveMode === 'callback' ? ' selected' : ''}>Callback</option>
          <option value="manual"${receiveMode === 'manual' ? ' selected' : ''}>Manual take/latest</option>
        </select></label>`
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
    port[input.dataset.key] = input.value;
  }
  $('node-dialog').dataset.draft = JSON.stringify(draft);
}

function saveNodeDialog(ev) {
  ev.preventDefault();
  const draft = JSON.parse($('node-dialog').dataset.draft);
  draft.name = $('config-node-name').value || 'custom_ros_node';
  draft.timerEnabled = $('config-timer-enabled').checked;
  draft.timerPeriodSec = Math.max(0.001, Number($('config-timer-period').value || 1.0));
  draft.timerCode = draft.timerCode || DEFAULT_TIMER_CODE;
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
  const isTimer = kind === 'timerCode';
  const isImport = kind === 'importCode';
  const isRequirements = kind === 'requirements';
  $('code-dialog-title').textContent = callbackPort
    ? `${node.name}.${callbackPort.name}: Callback Code`
    : (isTimer ? `${node.name}: Timer Callback Code` : (isImport ? `${node.name}: Import Code` : (isRequirements ? `${node.name}: requirements.txt` : `${node.name}: Main Loop Code`)));
  $('code-editor').value = callbackPort
    ? (callbackPort.callbackCode || '')
    : (isTimer ? (node.timerCode || DEFAULT_TIMER_CODE) : (isImport ? (node.importCode || DEFAULT_IMPORT_CODE) : (isRequirements ? (node.requirements || '') : (node.loopCode || ''))));
  $('code-hint').textContent = callbackPort
    ? 'Callback scope: input_id, msg, request, response, state, outputs, publish(output_id, value), log(...).'
    : (isTimer
      ? 'Timer callback scope: inputs, outputs, latest(input_id), take(input_id), has_input(input_id), state, now, period, publish(...), log(...).'
      : (isImport
        ? 'Import code runs once after this node venv is ready. Put imports such as import cv2 and import numpy as np here.'
        : (isRequirements
          ? 'One requirement per line. uv creates this node venv and installs these packages before execution.'
          : 'Main loop scope: inputs, outputs, latest(input_id), take(input_id), has_input(input_id), state, now, publish(...), log(...).')));
  $('code-dialog').showModal();
}

function saveCodeDialog(ev) {
  ev.preventDefault();
  const { nodeId, kind } = state.editingCode;
  const node = nodeFor(nodeId);
  if (node && kind.startsWith('callback:')) {
    const port = node.inputs.find((item) => item.id === kind.slice('callback:'.length));
    if (port) port.callbackCode = $('code-editor').value;
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

function renderAll() {
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
          <button data-action="timer">Timer Callback Code</button>
          ${node.inputs.map((input) => `<button data-callback-input="${escapeAttr(input.id)}">Callback: ${escapeHtml(input.name)}</button>`).join('')}
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
    const timerButton = el.querySelector('[data-action="timer"]');
    if (timerButton) {
      timerButton.onclick = (ev) => {
        ev.stopPropagation();
        openCodeDialog(node, 'timerCode');
      };
    }
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
  });
}

function nodeKindLabel(node) {
  if (!node.toolType) return 'lwrclpy Custom Node';
  if (['topic_input', 'topic_output'].includes(node.toolType)) return 'Interface Node';
  return 'Tool Node';
}

function toolActionHtml(node) {
  if (node.toolType === 'image_file_input') {
    return `<div class="node-actions tool-actions">
      <label class="file-button">Load Image<input data-tool-file="image" type="file" accept="image/*"></label>
    </div>`;
  }
  if (node.toolType === 'video_file_input') {
    return `<div class="node-actions tool-actions">
      <label class="file-button">Load Video<input data-tool-file="video" type="file" accept="video/*"></label>
    </div>`;
  }
  if (node.toolType === 'graph_view') {
    return `<div class="node-actions tool-actions">
      <button data-action="plot-field">Plot Field: ${escapeHtml(node.params?.fieldPath || 'data')}</button>
    </div>`;
  }
  return '';
}

function viewNodeHtml(node) {
  if (!['image_file_input', 'video_file_input', 'image_view', 'image_file_save', 'graph_view'].includes(node.toolType)) return '';
  return `<div class="node-view" data-node-view="${escapeAttr(node.id)}">${renderViewContent(state.nodeViews[node.id])}</div>`;
}

function bindToolActions(el, node) {
  const imageInput = el.querySelector('[data-tool-file="image"]');
  if (imageInput) imageInput.onchange = (ev) => loadImageFile(node, ev.target.files[0]);
  const videoInput = el.querySelector('[data-tool-file="video"]');
  if (videoInput) videoInput.onchange = (ev) => loadVideoFile(node, ev.target.files[0]);
  const plotField = el.querySelector('[data-action="plot-field"]');
  if (plotField) {
    plotField.onclick = (ev) => {
      ev.stopPropagation();
      const value = prompt('Field path to plot', node.params?.fieldPath || 'data');
      if (value === null) return;
      node.params = node.params || {};
      node.params.fieldPath = value.trim() || 'data';
      renderAll();
      scheduleRun();
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
      link.name = $('link-name').value;
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
    <div class="hint">${node.toolType ? 'Terminal topic node. Type is inferred from the connected node.' : `${node.inputs.length} subscriptions / ${node.outputs.length} publishers${node.timerEnabled ? ` / timer ${node.timerPeriodSec || 1}s` : ''}`}</div>
    ${node.toolType ? '' : `<div class="inspector-actions">
        <button id="inspect-config">Configure Ports</button>
        <button id="inspect-imports">Import Code</button>
        <button id="inspect-requirements">Requirements</button>
        <button id="inspect-callback">Subscribe Callback Code</button>
        <button id="inspect-timer">Timer Callback Code</button>
        <button id="inspect-loop">Main Loop Code</button>
      </div>`}
    <h3>Inputs</h3>
    ${inputSummary(node)}
    <h3>Outputs</h3>
    ${portSummary(node.outputs)}`;
  const inspectConfig = $('inspect-config');
  if (inspectConfig) inspectConfig.onclick = () => openNodeDialog(node);
  const inspectCallback = $('inspect-callback');
  if (inspectCallback) inspectCallback.onclick = () => {
    const firstInput = node.inputs[0];
    if (firstInput) openCodeDialog(node, `callback:${firstInput.id}`);
  };
  const inspectLoop = $('inspect-loop');
  if (inspectLoop) inspectLoop.onclick = () => openCodeDialog(node, 'loopCode');
  const inspectImports = $('inspect-imports');
  if (inspectImports) inspectImports.onclick = () => openCodeDialog(node, 'importCode');
  const inspectRequirements = $('inspect-requirements');
  if (inspectRequirements) inspectRequirements.onclick = () => openCodeDialog(node, 'requirements');
  const inspectTimer = $('inspect-timer');
  if (inspectTimer) inspectTimer.onclick = () => openCodeDialog(node, 'timerCode');
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
  return node.inputs.map((p) => `<div class="port-summary"><b>${escapeHtml(p.name)}</b><span>${escapeHtml(p.dataType || 'connected topic type')}</span>${node.toolType ? '' : `<small>${escapeHtml(p.receiveMode || 'callback')}</small><button data-callback-port="${escapeAttr(p.id)}">Edit Callback</button>`}</div>`).join('');
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
  const defaultTopic = defaultLinkTopic(state.dragLink.fromNode, state.dragLink.fromPort, inputRow.dataset.node, inputRow.dataset.port);
  const topic = prompt('Topic name for this edge', defaultTopic);
  if (topic === null) return;
  state.links = state.links.filter((link) => !(link.toNode === inputRow.dataset.node && link.toPort === inputRow.dataset.port));
  state.links.push({
    id: `l${Date.now()}${Math.random().toString(16).slice(2)}`,
    fromNode: state.dragLink.fromNode,
    fromPort: state.dragLink.fromPort,
    toNode: inputRow.dataset.node,
    toPort: inputRow.dataset.port,
    name: topic.trim() || defaultTopic,
  });
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
  state.lastRunAt = performance.now();
  const payload = {
    nodes: state.nodes,
    links: state.links.map(({ fromNode, fromPort, toNode, toPort, name }) => ({ fromNode, fromPort, toNode, toPort, name })),
  };
  try {
    const data = await fetch('/api/run', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
    }).then((res) => res.json());
    updateStatus(data);
    updateNodeViews(data.nodes || {});
  } catch (err) {
    $('runtime-status').textContent = `API error: ${err.message}`;
  }
}

function updateStatus(data) {
  const runtime = data.lwrclpy || {};
  const setup = data.setup?.complete === false ? ` / setup blocked${runtime.error ? ': ' + runtime.error : ''}` : '';
  $('runtime-status').textContent = (runtime.available ? 'lwrclpy available' : `lwrclpy unavailable${runtime.error ? ': ' + runtime.error : ''}`) + setup;
  $('node-count').textContent = `${state.nodes.length} nodes / ${state.links.length} links`;
}

function updateNodeViews(nodes) {
  Object.entries(nodes).forEach(([nodeId, payload]) => {
    if (payload?.view) state.nodeViews[nodeId] = payload.view;
  });
  document.querySelectorAll('[data-node-view]').forEach((el) => {
    el.innerHTML = renderViewContent(state.nodeViews[el.dataset.nodeView]);
  });
}

function renderViewContent(view) {
  if (!view) return '<div class="view-empty">No data</div>';
  if (view.kind === 'image' && view.dataUrl) {
    return `<figure class="image-view"><img src="${escapeAttr(view.dataUrl)}" alt=""><figcaption>${escapeHtml(view.status || '')}</figcaption></figure>`;
  }
  if (view.kind === 'plot') {
    return renderPlot(view.series || [], view.status || '');
  }
  return `<div class="view-empty">${escapeHtml(view.status || 'No data')}</div>`;
}

function renderPlot(series, label) {
  const width = 280;
  const height = 120;
  const values = series.map(Number).filter((value) => Number.isFinite(value));
  if (values.length < 2) {
    return `<div class="plot-view"><svg viewBox="0 0 ${width} ${height}"></svg><span>${escapeHtml(label || 'Waiting for values')}</span></div>`;
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const points = values.map((value, index) => {
    const x = values.length === 1 ? 0 : (index / (values.length - 1)) * width;
    const y = height - ((value - min) / span) * (height - 12) - 6;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return `<div class="plot-view"><svg viewBox="0 0 ${width} ${height}"><polyline points="${points}"></polyline></svg><span>${escapeHtml(label)} ${min.toFixed(3)} .. ${max.toFixed(3)}</span></div>`;
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
    };
    URL.revokeObjectURL(img.src);
    renderAll();
    scheduleRun();
  };
  img.src = URL.createObjectURL(file);
}

function loadVideoFile(node, file) {
  if (!file) return;
  const video = document.createElement('video');
  video.muted = true;
  video.loop = true;
  video.playsInline = true;
  video.onloadeddata = async () => {
    await video.play().catch(() => {});
    captureVideoFrame(node, video, file.name);
    if (node.params?.videoTimer) clearInterval(node.params.videoTimer);
    node.params.videoTimer = setInterval(() => captureVideoFrame(node, video, file.name), 250);
  };
  video.src = URL.createObjectURL(file);
}

function captureVideoFrame(node, video, fileName) {
  if (!video.videoWidth || !video.videoHeight) return;
  const frame = imageElementToMessage(video);
  node.params = {
    ...(node.params || {}),
    fileName,
    dataUrl: frame.dataUrl,
    frameMessage: frame.message,
  };
  renderAll();
  scheduleRun();
}

function imageElementToMessage(source) {
  const maxSide = 640;
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
  const rgb = new Array(width * height * 3);
  for (let src = 0, dst = 0; src < image.data.length; src += 4) {
    rgb[dst++] = image.data[src];
    rgb[dst++] = image.data[src + 1];
    rgb[dst++] = image.data[src + 2];
  }
  return {
    dataUrl: canvas.toDataURL('image/png'),
    message: {
      width,
      height,
      encoding: 'rgb8',
      is_bigendian: 0,
      step: width * 3,
      data: rgb,
      dataUrl: canvas.toDataURL('image/png'),
    },
  };
}

function scheduleRun() {
  const now = performance.now();
  if (now - state.lastRunAt > 80) {
    runGraph();
    return;
  }
  clearTimeout(state.runTimer);
  state.runTimer = setTimeout(runGraph, 120);
}

function startRun() {
  if (state.autoTimer) return;
  state.autoTimer = setInterval(runGraph, 100);
  $('run-model').classList.add('active');
  runGraph();
}

function stopRun() {
  if (state.autoTimer) {
    clearInterval(state.autoTimer);
    state.autoTimer = null;
  }
  if (state.runStopTimer) {
    clearTimeout(state.runStopTimer);
    state.runStopTimer = null;
  }
  $('run-model').classList.remove('active');
}

function runForDuration() {
  stopRun();
  startRun();
  const seconds = Math.max(0.1, Number($('run-duration').value || 5));
  state.runStopTimer = setTimeout(stopRun, seconds * 1000);
}

function clearGraph() {
  state.nodes = [];
  state.links = [];
  state.selectedNode = null;
  state.selectedLink = null;
  renderAll();
  runGraph();
}

function selectNode(ev, id) {
  ev.stopPropagation();
  state.selectedNode = id;
  state.selectedLink = null;
  renderSelection();
  renderInspector();
}

function deleteNode(id) {
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
  return {
    format: 'lwrclpy-web-node-editor-project',
    version: 1,
    nodes: state.nodes,
    links: state.links,
    view: state.view,
    nextId: state.nextId,
  };
}

function saveProject() {
  downloadText('lwrclpy_web_node_project.json', JSON.stringify(projectConfig(), null, 2), 'application/json');
}

async function loadProject(event) {
  const file = event.target.files[0];
  event.target.value = '';
  if (!file) return;
  const imported = JSON.parse(await file.text());
  state.nodes = imported.nodes || [];
  state.links = (imported.links || []).map((link) => ({ id: link.id || `l${Date.now()}${Math.random()}`, ...link }));
  state.links = state.links.filter(isValidLink);
  state.view = imported.view || { x: 0, y: 0, scale: 1 };
  state.nextId = imported.nextId || Math.max(1, ...state.nodes.map((node) => Number(String(node.id).replace('n', '')) + 1));
  renderAll();
  scheduleRun();
}

function exportProjectPython() {
  downloadText('lwrclpy_project.py', renderProjectPythonFile(projectPythonConfig()), 'text/x-python');
}

function exportProjectLaunch() {
  downloadText('lwrclpy_project.launch.py', renderProjectLaunchFile(), 'text/x-python');
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

function renderProjectPythonFile(config) {
  const metadata = JSON.stringify(config, null, 2);
  return `#!/usr/bin/env python3
# Generated by lwrclpy Web Node Editor.
# This file runs exported custom lwrclpy nodes from one saved project.

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
        if hasattr(msg, "data"):
            msg.data = value
        elif isinstance(value, dict):
            for key, item in value.items():
                if hasattr(msg, key):
                    setattr(msg, key, item)
        return msg

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
    # Put lwrclpy_project.py next to this launch file or edit this path.
    project_script = PathJoinSubstitution([
        ThisLaunchFileDir(),
        'lwrclpy_project.py',
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
# Generated by lwrclpy Web Node Editor.
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
        if hasattr(msg, "data"):
            msg.data = value
        elif isinstance(value, dict):
            for key, item in value.items():
                if hasattr(msg, key):
                    setattr(msg, key, item)
        return msg

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
  return {
    id: node.id || `n${state.nextId++}`,
    name: node.name || 'imported_lwrclpy_node',
    x: Number(node.x || 0),
    y: Number(node.y || 0),
    inputs: (node.inputs || []).map((port, index) => ({
      id: port.id || `in${index + 1}`,
      name: port.name || `in${index + 1}`,
      dataType: port.dataType || firstDataType(),
      receiveMode: port.receiveMode || 'callback',
      callbackCode: port.callbackCode || '',
    })),
    outputs: (node.outputs || []).map((port, index) => ({
      id: port.id || `out${index + 1}`,
      name: port.name || `out${index + 1}`,
      dataType: port.dataType || firstDataType(),
    })),
    loopCode: node.loopCode || '',
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
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
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
  const dst = nodeFor(toNode)?.inputs.find((port) => port.id === toPort);
  return `/${src?.name || fromPort}_to_${dst?.name || toPort}`;
}

function editLinkName(linkId) {
  const link = state.links.find((item) => item.id === linkId);
  if (!link) return;
  const fallback = defaultLinkTopic(link.fromNode, link.fromPort, link.toNode, link.toPort);
  const next = prompt('Topic name for this edge', link.name || fallback);
  if (next === null) return;
  link.name = next.trim() || fallback;
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

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, '&#96;');
}

window.addEventListener('resize', renderLinks);
window.addEventListener('keydown', (ev) => {
  if (ev.key === 'Delete' || ev.key === 'Backspace') {
    if (state.selectedNode) deleteNode(state.selectedNode);
    else if (state.selectedLink) deleteLink(state.selectedLink);
  }
});

init();
